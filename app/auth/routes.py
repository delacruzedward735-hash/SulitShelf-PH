from datetime import timedelta

import requests
from authlib.integrations.base_client.errors import OAuthError
from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db, limiter, oauth
from app.models import OAuthIdentity, Shop, User, utcnow
from app.services.catalog import slugify
from app.services.oauth import PROVIDERS, OAuthProfileError, load_provider_profile, provider_is_configured

bp = Blueprint("auth", __name__)


def _safe_next(value):
    return value if value and value.startswith("/") and not value.startswith("//") else None


def _new_user(email, display_name, oauth_only=False):
    role = "admin" if email in current_app.config["ADMIN_EMAILS"] else "promoter"
    user = User(email=email, display_name=display_name[:80], role=role, is_active_account=True)
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
        if len(display_name) < 2 or len(display_name) > 80 or len(password) < 10:
            flash("Use a display name and a password with at least 10 characters.", "error")
            return render_template("auth/register.html")
        if db.session.scalar(db.select(User).where(User.email == email)):
            flash("An account already exists for that email.", "error")
            return render_template("auth/login.html")
        user = _new_user(email, display_name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
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
        login_user(user, remember=request.form.get("remember") == "yes")
        return redirect(_safe_next(request.args.get("next")) or url_for("promoter.dashboard"))
    return render_template("auth/login.html")


@bp.get("/oauth/<provider>")
@limiter.limit("20 per hour")
def oauth_start(provider):
    if provider not in PROVIDERS:
        abort(404)
    if not provider_is_configured(provider):
        return _oauth_return(provider, f"{PROVIDERS[provider].name} sign-in is not configured yet.")
    requested_mode = request.args.get("mode", "login")
    if requested_mode == "link" and not current_user.is_authenticated:
        flash("Sign in with your password before connecting another account.", "info")
        return redirect(url_for("auth.login", next=url_for("promoter.dashboard", tab="settings")))
    mode = "link" if current_user.is_authenticated else "login"
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
        return _oauth_return(provider, f"{PROVIDERS[provider].name} sign-in could not be completed. Please try again.")

    identity = db.session.scalar(
        db.select(OAuthIdentity).where(
            OAuthIdentity.provider == provider,
            OAuthIdentity.provider_user_id == profile["provider_user_id"],
        )
    )
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
    session.clear()
    login_user(user)
    flash(f"Signed in securely with {PROVIDERS[provider].name}.", "success")
    return redirect(next_url or url_for("promoter.dashboard"))


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
    flash("You have signed out.", "success")
    return redirect(url_for("main.home"))
