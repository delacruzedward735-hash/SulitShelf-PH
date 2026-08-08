import hashlib
import json
import re
import secrets
import time
from datetime import timedelta
from io import BytesIO

import qrcode
import requests
from authlib.integrations.base_client.errors import OAuthError
from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import confirm_login, current_user, fresh_login_required, login_fresh, login_required, login_user, logout_user
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db, limiter, oauth
from app.models import AuditLog, OAuthIdentity, PasswordResetToken, Shop, User, utcnow
from app.services.catalog import slugify
from app.services.email import EmailDeliveryError, send_password_reset_email
from app.services.oauth import PROVIDERS, OAuthProfileError, load_provider_profile, provider_is_configured
from app.services.two_factor import (
    TwoFactorError,
    clear_two_factor,
    decrypt_secret,
    encrypt_secret,
    format_secret,
    generate_secret,
    matching_totp_counter,
    provisioning_uri,
    remaining_recovery_codes,
    replace_recovery_codes,
    verify_and_consume_code,
)

bp = Blueprint("auth", __name__)
PREAUTH_SESSION_KEY = "two_factor_preauth"
SETUP_SESSION_KEY = "two_factor_setup"


def _safe_next(value):
    return value if value and value.startswith("/") and not value.startswith("//") else None


def _audit_security(user, action, details=None):
    db.session.add(
        AuditLog(
            admin_email=user.email,
            action=action,
            target_type="user",
            target_id=str(user.id),
            details=(details or "{}") if isinstance(details, str) else json.dumps(details or {}, separators=(",", ":")),
        )
    )


def _finish_or_challenge_login(user, *, remember=False, next_url=None, success_message=None):
    safe_next = _safe_next(next_url)
    session.clear()
    if user.two_factor_enabled:
        session[PREAUTH_SESSION_KEY] = {
            "user_id": user.id,
            "session_version": user.session_version,
            "remember": bool(remember),
            "next": safe_next,
            "issued_at": int(time.time()),
            "success_message": (success_message or "")[:160],
        }
        return redirect(url_for("auth.two_factor_challenge"))
    login_user(user, remember=remember, fresh=True)
    if success_message:
        flash(success_message, "success")
    return redirect(safe_next or url_for("promoter.dashboard"))


def _pending_login():
    data = session.get(PREAUTH_SESSION_KEY)
    if not isinstance(data, dict):
        return None, None
    try:
        user_id = int(data["user_id"])
        session_version = int(data["session_version"])
        issued_at = int(data["issued_at"])
    except (KeyError, TypeError, ValueError):
        session.pop(PREAUTH_SESSION_KEY, None)
        return None, None
    max_age = current_app.config["TWO_FACTOR_CHALLENGE_MINUTES"] * 60
    age = int(time.time()) - issued_at
    user = db.session.get(User, user_id)
    if (
        age < 0
        or age > max_age
        or not user
        or not user.is_active_account
        or user.session_version != session_version
        or not user.two_factor_enabled
    ):
        session.pop(PREAUTH_SESSION_KEY, None)
        return None, None
    return user, data


def _pending_setup_secret(create=False):
    data = session.get(SETUP_SESSION_KEY)
    secret = None
    if isinstance(data, dict) and data.get("user_id") == current_user.id:
        try:
            secret = decrypt_secret(data.get("ciphertext", ""), ttl=10 * 60)
        except TwoFactorError:
            session.pop(SETUP_SESSION_KEY, None)
    if not secret and create:
        secret = generate_secret()
        session[SETUP_SESSION_KEY] = {
            "user_id": current_user.id,
            "ciphertext": encrypt_secret(secret),
        }
    return secret


def _valid_reset_token(raw_token, lock=False):
    if not raw_token or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", raw_token):
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    statement = db.select(PasswordResetToken).where(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > utcnow(),
    )
    if lock:
        statement = statement.with_for_update()
    reset = db.session.scalar(statement)
    if not reset or not reset.user.is_active_account:
        return None
    return reset


def _new_user(email, display_name, oauth_only=False):
    # Public registration and first-time OAuth sign-in must never grant an
    # administrative role. Administrator access is created or recovered only
    # through the trusted CLI command, where the operator controls the host.
    user = User(email=email, display_name=display_name[:80], role="promoter", is_active_account=True)
    if oauth_only:
        user.set_unusable_password()
    base = slugify(display_name) or "promoter"
    slug = base
    counter = 1
    while db.session.scalar(db.select(Shop).where(Shop.slug == slug)):
        counter += 1
        slug = f"{base[:50]}-{counter}"
    user.shop = Shop(
        name=f"{display_name[:68]}'s Shelf",
        slug=slug,
        plan_key="free",
        subscription_status="free",
        subscription_source="open_source",
        subscription_ends_at=utcnow() + timedelta(days=36500),
    )
    return user


def _oauth_return(provider, message, category="error"):
    flash(message, category)
    if current_user.is_authenticated:
        return redirect(url_for("promoter.dashboard", tab="settings"))
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("8 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("promoter.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        try:
            email = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            flash("Enter a valid email address.", "error")
            return render_template("auth/register.html")
        if len(display_name) < 2 or len(display_name) > 80 or not 12 <= len(password) <= 128:
            flash("Use a display name and a password with 12–128 characters.", "error")
            return render_template("auth/register.html")
        if db.session.scalar(db.select(User).where(User.email == email)):
            flash("An account already exists for that email.", "error")
            return render_template("auth/login.html")
        user = _new_user(email, display_name)
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # A concurrent registration can win the unique email or shop-slug
            # race after the preflight checks above. Return a safe response
            # instead of exposing a database error.
            db.session.rollback()
            flash("That account or shop address was just registered. Sign in or try another name.", "error")
            return render_template("auth/register.html"), 409
        session.clear()
        login_user(user)
        flash("Your free open-source promoter shelf is ready.", "success")
        return redirect(url_for("promoter.dashboard"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("promoter.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.session.scalar(db.select(User).where(User.email == email))
        if not user or not user.check_password(password) or not user.is_active_account:
            flash("Incorrect email or password.", "error")
            return render_template("auth/login.html"), 401
        return _finish_or_challenge_login(
            user,
            remember=request.form.get("remember") == "yes",
            next_url=request.args.get("next"),
        )
    return render_template("auth/login.html")


@bp.route("/two-factor/challenge", methods=["GET", "POST"])
@limiter.limit("10 per 10 minutes")
def two_factor_challenge():
    if current_user.is_authenticated:
        return redirect(url_for("promoter.dashboard"))
    user, pending = _pending_login()
    if not user:
        flash("Your sign-in verification expired. Enter your credentials again.", "info")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        user = db.session.scalar(db.select(User).where(User.id == user.id).with_for_update())
        method = verify_and_consume_code(user, request.form.get("code", "")[:64])
        if not method:
            flash("That code is invalid, expired, or was already used.", "error")
            return render_template("auth/two_factor_challenge.html"), 401
        db.session.commit()
        remember = bool(pending.get("remember"))
        next_url = _safe_next(pending.get("next"))
        success_message = pending.get("success_message", "")
        session.clear()
        login_user(user, remember=remember, fresh=True)
        if success_message:
            flash(success_message, "success")
        if method == "recovery":
            flash("A recovery code was used and cannot be used again.", "info")
        return redirect(next_url or url_for("promoter.dashboard"))

    return render_template("auth/two_factor_challenge.html")


@bp.get("/security")
@login_required
def account_security():
    return render_template(
        "security/account_security.html",
        recovery_codes_remaining=remaining_recovery_codes(current_user.id) if current_user.two_factor_enabled else 0,
        session_is_fresh=login_fresh(),
    )


@bp.route("/security/2fa/setup", methods=["GET", "POST"])
@fresh_login_required
@limiter.limit("12 per hour")
def two_factor_setup():
    if current_user.two_factor_enabled:
        flash("Authenticator-app verification is already enabled.", "info")
        return redirect(url_for("auth.account_security"))
    secret = _pending_setup_secret(create=True)
    if request.method == "POST":
        counter = matching_totp_counter(secret, request.form.get("code", ""))
        if counter is None:
            flash("That six-digit code did not match. Wait for a new code and try again.", "error")
            return render_template(
                "security/two_factor_setup.html",
                setup_key=format_secret(secret),
            ), 400
        user = db.session.scalar(db.select(User).where(User.id == current_user.id).with_for_update())
        if user.two_factor_enabled:
            db.session.rollback()
            return redirect(url_for("auth.account_security"))
        user.two_factor_secret_ciphertext = encrypt_secret(secret)
        user.two_factor_enabled_at = utcnow()
        user.two_factor_last_counter = counter
        recovery_codes = replace_recovery_codes(user)
        user.session_version += 1
        _audit_security(user, "security.two_factor_enabled", {"recovery_code_count": len(recovery_codes)})
        db.session.commit()
        session.pop(SETUP_SESSION_KEY, None)
        logout_user()
        login_user(user, remember=False, fresh=True)
        return render_template(
            "security/two_factor_recovery_codes.html",
            recovery_codes=recovery_codes,
            first_setup=True,
        )
    return render_template(
        "security/two_factor_setup.html",
        setup_key=format_secret(secret),
    )


@bp.get("/security/2fa/setup/qr")
@fresh_login_required
@limiter.limit("30 per hour")
def two_factor_setup_qr():
    if current_user.two_factor_enabled:
        abort(404)
    secret = _pending_setup_secret(create=False)
    if not secret:
        abort(404)
    image = qrcode.make(provisioning_uri(secret, current_user.email))
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    response = send_file(output, mimetype="image/png", max_age=0, download_name="sulitshelf-2fa-setup.png")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Disposition"] = 'inline; filename="sulitshelf-2fa-setup.png"'
    return response


@bp.post("/security/2fa/recovery-codes")
@fresh_login_required
@limiter.limit("6 per hour")
def regenerate_two_factor_recovery_codes():
    user = db.session.scalar(db.select(User).where(User.id == current_user.id).with_for_update())
    if not user.two_factor_enabled:
        abort(400)
    method = verify_and_consume_code(user, request.form.get("code", "")[:64])
    if not method:
        db.session.rollback()
        flash("Enter a current authenticator or recovery code.", "error")
        return redirect(url_for("auth.account_security"))
    recovery_codes = replace_recovery_codes(user)
    _audit_security(user, "security.two_factor_recovery_codes_regenerated", {"verified_with": method})
    db.session.commit()
    return render_template(
        "security/two_factor_recovery_codes.html",
        recovery_codes=recovery_codes,
        first_setup=False,
    )


@bp.post("/security/2fa/disable")
@fresh_login_required
@limiter.limit("6 per hour")
def disable_two_factor():
    user = db.session.scalar(db.select(User).where(User.id == current_user.id).with_for_update())
    if not user.two_factor_enabled:
        abort(400)
    method = verify_and_consume_code(user, request.form.get("code", "")[:64])
    if not method:
        db.session.rollback()
        flash("Enter a current authenticator or recovery code before disabling two-factor authentication.", "error")
        return redirect(url_for("auth.account_security"))
    clear_two_factor(user)
    user.session_version += 1
    _audit_security(user, "security.two_factor_disabled", {"verified_with": method})
    db.session.commit()
    logout_user()
    session.pop(SETUP_SESSION_KEY, None)
    session.pop(PREAUTH_SESSION_KEY, None)
    flash("Two-factor authentication was disabled. Sign in again to continue.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/reauthenticate", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per 15 minutes")
def reauthenticate():
    next_url = _safe_next(request.args.get("next")) or url_for("auth.account_security")
    if login_fresh():
        return redirect(next_url)
    if request.method == "POST":
        if not current_user.check_password(request.form.get("password", "")):
            flash("That password did not match your account.", "error")
            return render_template(
                "auth/reauthenticate.html",
                next_url=next_url,
                reauth_providers=[],
            ), 401
        confirm_login()
        flash("Your identity was confirmed.", "success")
        return redirect(next_url)
    connected = {identity.provider for identity in current_user.oauth_identities}
    reauth_providers = [
        provider for provider in PROVIDERS.values()
        if provider.key in connected and provider_is_configured(provider.key)
    ]
    return render_template(
        "auth/reauthenticate.html",
        next_url=next_url,
        reauth_providers=reauth_providers,
    )


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    if not current_app.config["PASSWORD_RESET_ENABLED"]:
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for("promoter.dashboard"))

    submitted = False
    if request.method == "POST":
        supplied_email = request.form.get("email", "").strip().lower()
        try:
            normalized_email = validate_email(supplied_email, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            normalized_email = ""

        user = db.session.scalar(db.select(User).where(User.email == normalized_email)) if normalized_email else None
        if user and user.is_active_account:
            now = utcnow()
            db.session.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                )
                .values(used_at=now)
            )
            raw_token = secrets.token_urlsafe(32)
            reset = PasswordResetToken(
                user=user,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=now + timedelta(minutes=current_app.config["PASSWORD_RESET_TOKEN_MINUTES"]),
            )
            db.session.add(reset)
            db.session.commit()
            reset_url = (
                f"{current_app.config['PUBLIC_BASE_URL']}"
                f"{url_for('auth.reset_password')}?token={raw_token}"
            )
            try:
                send_password_reset_email(user, reset_url, reset.id)
            except EmailDeliveryError:
                current_app.logger.error("Password reset email could not be delivered user_id=%s", user.id)
                failed_reset = db.session.get(PasswordResetToken, reset.id)
                if failed_reset and failed_reset.used_at is None:
                    failed_reset.used_at = utcnow()
                    db.session.commit()
        submitted = True

    return render_template("auth/forgot_password.html", submitted=submitted)


@bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def reset_password():
    if not current_app.config["PASSWORD_RESET_ENABLED"]:
        abort(404)

    raw_token = request.args.get("token", "") if request.method == "GET" else request.form.get("token", "")
    reset = _valid_reset_token(raw_token, lock=request.method == "POST")
    if not reset:
        return render_template("auth/reset_password.html", valid_token=False), 400

    if request.method == "POST":
        password = request.form.get("password", "")
        confirmation = request.form.get("password_confirmation", "")
        if not 12 <= len(password) <= 128:
            flash("Use a password with 12–128 characters.", "error")
            return render_template("auth/reset_password.html", valid_token=True, token=raw_token), 400
        if password != confirmation:
            flash("The password confirmation does not match.", "error")
            return render_template("auth/reset_password.html", valid_token=True, token=raw_token), 400

        user = reset.user
        user.set_password(password)
        user.session_version += 1
        db.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=utcnow())
        )
        db.session.commit()
        logout_user()
        session.clear()
        flash("Your password was changed. Sign in with your new password.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", valid_token=True, token=raw_token)


@bp.get("/oauth/<provider>")
@limiter.limit("20 per hour")
def oauth_start(provider):
    if provider not in PROVIDERS:
        abort(404)
    if not provider_is_configured(provider):
        return _oauth_return(provider, f"{PROVIDERS[provider].name} sign-in is not configured yet.")
    requested_mode = request.args.get("mode", "login")
    if requested_mode in {"link", "reauth"} and not current_user.is_authenticated:
        flash("Sign in before connecting or confirming another account.", "info")
        return redirect(url_for("auth.login", next=url_for("promoter.dashboard", tab="settings")))
    mode = requested_mode if current_user.is_authenticated and requested_mode in {"link", "reauth"} else ("link" if current_user.is_authenticated else "login")
    session[f"oauth_{provider}_mode"] = mode
    session[f"oauth_{provider}_next"] = _safe_next(request.args.get("next"))
    redirect_uri = f"{current_app.config['OAUTH_REDIRECT_BASE_URL']}{url_for('auth.oauth_callback', provider=provider)}"
    client = oauth.create_client(provider)
    options = {"prompt": "select_account"} if provider == "google" else {}
    return client.authorize_redirect(redirect_uri, **options)


@bp.get("/oauth/<provider>/callback")
@limiter.limit("20 per hour")
def oauth_callback(provider):
    if provider not in PROVIDERS:
        abort(404)
    if not provider_is_configured(provider):
        return _oauth_return(provider, f"{PROVIDERS[provider].name} sign-in is not configured yet.")
    mode = session.pop(f"oauth_{provider}_mode", "link" if current_user.is_authenticated else "login")
    next_url = session.pop(f"oauth_{provider}_next", None)
    client = oauth.create_client(provider)
    try:
        token = client.authorize_access_token()
        profile = load_provider_profile(provider, client, token)
    except (OAuthError, OAuthProfileError, requests.RequestException, ValueError):
        current_app.logger.exception("OAuth callback failed for %s", provider)
        if mode == "reauth":
            flash(f"{PROVIDERS[provider].name} could not confirm your identity. Please try again.", "error")
            return redirect(url_for("auth.reauthenticate", next=next_url or url_for("auth.account_security")))
        return _oauth_return(provider, f"{PROVIDERS[provider].name} sign-in could not be completed. Please try again.")

    identity = db.session.scalar(
        db.select(OAuthIdentity).where(
            OAuthIdentity.provider == provider,
            OAuthIdentity.provider_user_id == profile["provider_user_id"],
        )
    )
    if mode == "reauth":
        if not current_user.is_authenticated or not identity or identity.user_id != current_user.id:
            flash("That social account does not match your signed-in SulitShelf account.", "error")
            return redirect(url_for("auth.reauthenticate", next=next_url or url_for("auth.account_security")))
        identity.last_used_at = utcnow()
        db.session.commit()
        confirm_login()
        flash(f"Your identity was confirmed with {PROVIDERS[provider].name}.", "success")
        return redirect(next_url or url_for("auth.account_security"))
    if mode == "link":
        if not current_user.is_authenticated:
            return _oauth_return(provider, "Your session expired. Sign in and connect the account again.")
        existing_for_user = db.session.scalar(
            db.select(OAuthIdentity).where(OAuthIdentity.user_id == current_user.id, OAuthIdentity.provider == provider)
        )
        if identity and identity.user_id != current_user.id:
            return _oauth_return(provider, f"That {PROVIDERS[provider].name} account is already connected to another SulitShelf account.")
        if existing_for_user and existing_for_user.provider_user_id != profile["provider_user_id"]:
            return _oauth_return(provider, f"Disconnect your current {PROVIDERS[provider].name} account before connecting a different one.")
        if not existing_for_user:
            db.session.add(
                OAuthIdentity(
                    user=current_user,
                    provider=provider,
                    provider_user_id=profile["provider_user_id"],
                    email_at_link=profile["email"] or None,
                    last_used_at=utcnow(),
                )
            )
        else:
            existing_for_user.last_used_at = utcnow()
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return _oauth_return(provider, "That social account was connected elsewhere while this request was processing.")
        return _oauth_return(provider, f"{PROVIDERS[provider].name} is now connected to your account.", "success")

    if identity:
        user = identity.user
        identity.last_used_at = utcnow()
    else:
        if not profile["email"] or not profile["email_verified"]:
            return _oauth_return(provider, f"{PROVIDERS[provider].name} did not provide a verified email address.")
        try:
            email = validate_email(profile["email"], check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            return _oauth_return(provider, f"{PROVIDERS[provider].name} returned an invalid email address.")
        existing_user = db.session.scalar(db.select(User).where(User.email == email))
        if existing_user:
            return _oauth_return(
                provider,
                f"An account already uses {email}. Sign in with its password, then connect {PROVIDERS[provider].name} from Shop settings.",
            )
        display_name = (profile["display_name"] or email.split("@", 1)[0]).strip()[:80]
        user = _new_user(email, display_name, oauth_only=True)
        identity = OAuthIdentity(
            user=user,
            provider=provider,
            provider_user_id=profile["provider_user_id"],
            email_at_link=email,
            last_used_at=utcnow(),
        )
        db.session.add_all([user, identity])
    if not user.is_active_account:
        db.session.rollback()
        return _oauth_return(provider, "This SulitShelf account is disabled. Contact the administrator.")
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _oauth_return(provider, "The account changed while signing in. Please try again.")
    return _finish_or_challenge_login(
        user,
        next_url=next_url,
        success_message=f"Signed in securely with {PROVIDERS[provider].name}.",
    )


@bp.post("/oauth/<provider>/disconnect")
@login_required
def oauth_disconnect(provider):
    if provider not in PROVIDERS:
        abort(404)
    identity = db.session.scalar(
        db.select(OAuthIdentity).where(OAuthIdentity.user_id == current_user.id, OAuthIdentity.provider == provider)
    )
    if not identity:
        return _oauth_return(provider, f"{PROVIDERS[provider].name} is not connected.")
    if not current_user.has_usable_password and len(current_user.oauth_identities) <= 1:
        return _oauth_return(provider, "You cannot disconnect your only sign-in method.")
    db.session.delete(identity)
    db.session.commit()
    return _oauth_return(provider, f"{PROVIDERS[provider].name} was disconnected.", "success")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("main.home"))
