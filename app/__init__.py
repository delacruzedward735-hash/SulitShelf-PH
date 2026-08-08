import logging
import re
import secrets
import time
import uuid
from pathlib import Path

import click
from flask import Flask, g, render_template, request
from flask_wtf.csrf import CSRFError
from sqlalchemy import text
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db, limiter, login_manager, migrate, oauth
from app.services.production import production_issues, validate_production_config


# A stable, application-specific PostgreSQL advisory-lock key. It serializes
# Alembic migrations when a platform starts more than one container at once.
RELEASE_LOCK_KEY = 831_729_154


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    validate_production_config(app.config)

    proxy_counts = {
        "x_for": app.config["PROXY_FIX_X_FOR"],
        "x_proto": app.config["PROXY_FIX_X_PROTO"],
        "x_host": app.config["PROXY_FIX_X_HOST"],
        "x_port": app.config["PROXY_FIX_X_PORT"],
        "x_prefix": app.config["PROXY_FIX_X_PREFIX"],
    }
    if any(proxy_counts.values()):
        app.wsgi_app = ProxyFix(app.wsgi_app, **proxy_counts)

    logging.basicConfig(
        level=getattr(logging, app.config["LOG_LEVEL"], logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app.logger.setLevel(getattr(logging, app.config["LOG_LEVEL"], logging.INFO))

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    for directory in ("products", "receipts", "settings", "branding"):
        (Path(app.config["UPLOAD_ROOT"]) / directory).mkdir(parents=True, exist_ok=True)

    # Register request metadata before CSRFProtect and Flask-Limiter install
    # their own before-request hooks. Those extensions may intentionally stop
    # a rejected POST early; after-request security headers must still have a
    # nonce and request ID for the resulting 400/429 response.
    @app.before_request
    def begin_request():
        supplied_request_id = request.headers.get("X-Request-ID", "")[:64]
        g.request_id = supplied_request_id if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_request_id) else uuid.uuid4().hex
        g.request_started_at = time.monotonic()
        g.csp_nonce = secrets.token_urlsafe(18)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    oauth.init_app(app)

    from app.services.oauth import configure_oauth, provider_context

    configure_oauth(app)

    @app.context_processor
    def oauth_template_context():
        return {"oauth_providers": provider_context()}

    from app.models import User
    from app.services.health import database_schema_issues, liveness_report, readiness_report
    from app.services.two_factor import configuration_issue as two_factor_configuration_issue

    @login_manager.user_loader
    def load_user(session_id):
        try:
            user_id, version = session_id.split(":", 1)
            user = db.session.get(User, int(user_id))
            if not user or not user.is_active_account or user.session_version != int(version):
                return None
            return user
        except (AttributeError, TypeError, ValueError):
            return None

    from app.admin.routes import bp as admin_bp
    from app.auth.routes import bp as auth_bp
    from app.main.routes import bp as main_bp
    from app.payments.routes import bp as payments_bp
    from app.promoter.routes import bp as promoter_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(promoter_bp, url_prefix="/studio")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(payments_bp, url_prefix="/payments")

    @app.before_request
    def guard_two_factor_dependencies():
        if not request.path.startswith(("/security", "/two-factor")):
            return None

        try:
            schema_issues = database_schema_issues()
        except Exception as error:
            db.session.rollback()
            app.logger.exception(
                "2fa dependency check failed dependency=database error_type=%s request_id=%s",
                type(error).__name__,
                g.request_id,
            )
            return render_template(
                "security/two_factor_unavailable.html",
                request_id=g.request_id,
            ), 503

        configuration_problem = two_factor_configuration_issue()
        if schema_issues or configuration_problem:
            app.logger.error(
                "2fa unavailable schema_issues=%s configuration_issue=%s request_id=%s",
                "; ".join(schema_issues[:12]) or "none",
                configuration_problem or "none",
                g.request_id,
            )
            return render_template(
                "security/two_factor_unavailable.html",
                request_id=g.request_id,
            ), 503
        return None

    @app.after_request
    def secure_and_observe(response):
        request_id = g.get("request_id") or uuid.uuid4().hex
        csp_nonce = g.get("csp_nonce") or secrets.token_urlsafe(18)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

        csp = (
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            "form-action 'self'; img-src 'self' data: blob: https://res.cloudinary.com; "
            f"style-src 'self'; script-src 'self' 'nonce-{csp_nonce}'; font-src 'self'; connect-src 'self'"
        )
        if app.config["IS_PRODUCTION"]:
            csp += "; upgrade-insecure-requests"
        response.headers.setdefault("Content-Security-Policy", csp)

        if request.is_secure and app.config["SECURITY_HSTS_SECONDS"]:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={app.config['SECURITY_HSTS_SECONDS']}; includeSubDomains",
            )

        private_prefixes = (
            "/admin", "/studio", "/login", "/logout", "/register", "/oauth", "/payments",
            "/forgot-password", "/reset-password", "/two-factor", "/security", "/reauthenticate",
        )
        if request.path.startswith(private_prefixes):
            response.headers["Cache-Control"] = "private, no-store"
        if request.path in {"/forgot-password", "/reset-password"} or request.path.startswith("/reset-password/"):
            response.headers["Referrer-Policy"] = "no-referrer"
        elif request.path.startswith(("/two-factor", "/security", "/reauthenticate")):
            # Flask-WTF's HTTPS CSRF check requires a same-origin Referer on
            # form POSTs. This policy supplies it only to SulitShelf itself and
            # still strips the referrer entirely for every cross-origin request.
            response.headers["Referrer-Policy"] = "same-origin"
        elif request.path.startswith("/health/"):
            response.headers["Cache-Control"] = "no-store"

        elapsed_ms = round((time.monotonic() - g.get("request_started_at", time.monotonic())) * 1000, 2)
        app.logger.info(
            "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    @app.get("/health/live")
    @limiter.exempt
    def health_live():
        return liveness_report()

    @app.get("/health/ready")
    @limiter.exempt
    def health_ready():
        return readiness_report()

    @app.get("/health/heartbeat")
    @limiter.exempt
    def health_heartbeat():
        configured_token = app.config["HEARTBEAT_TOKEN"]
        if configured_token:
            authorization = request.headers.get("Authorization", "")
            scheme, _, supplied_token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(supplied_token, configured_token):
                return {"status": "not_found"}, 404

        report, status_code = readiness_report()
        report["heartbeat"] = "SULITSHELF_OK" if status_code == 200 else "SULITSHELF_UNHEALTHY"
        return report, status_code

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        if request.path == "/studio/products" and error.code in {413, 429}:
            from flask import flash, redirect, url_for

            if error.code == 413:
                flash("Product not published: choose a JPG, PNG, or WebP image smaller than 8 MB.", "error")
            else:
                flash("Publishing was paused after too many attempts. Wait a few minutes, then try again once.", "error")
            return redirect(url_for("promoter.dashboard", tab="add"), code=303)
        response = error.get_response()
        response.set_data(
            render_template(
                "error.html",
                status=error.code,
                title=error.name,
                message=error.description,
            )
        )
        response.content_type = "text/html; charset=utf-8"
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        app.logger.warning(
            "csrf rejected method=%s path=%s reason=%s request_id=%s",
            request.method,
            request.path,
            error.description,
            g.get("request_id", ""),
        )
        if request.path == "/security/2fa/setup":
            from flask import flash, redirect, url_for

            flash("Your verification form expired. Enter the newest authenticator code and try once more.", "error")
            return redirect(url_for("auth.two_factor_setup"), code=303)
        if request.path == "/studio/products":
            from flask import flash, redirect, url_for

            flash("Your product form expired before it reached the server. Return to the form, reselect the image, and publish again.", "error")
            return redirect(url_for("promoter.dashboard", tab="add"), code=303)
        return render_template(
            "error.html",
            status=400,
            title="Form verification expired",
            message="Reload the page and submit the form again.",
        ), 400

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if app.config["TESTING"]:
            raise error
        db.session.rollback()
        app.logger.exception("Unhandled application error request_id=%s", g.get("request_id", ""))
        return render_template(
            "error.html",
            status=500,
            title="Something went wrong",
            message="The request could not be completed. Please try again.",
        ), 500

    from app.services.billing import ensure_defaults

    @app.cli.command("seed-defaults")
    def seed_defaults():
        """Create default plans, donation tiers, campaigns, and settings."""
        ensure_defaults()
        click.echo("Default plans and platform configuration are ready.")

    @app.cli.command("seed-admin")
    @click.option("--email", envvar="ADMIN_EMAIL")
    @click.option("--password", envvar="ADMIN_PASSWORD", prompt=True, hide_input=True, confirmation_prompt=True)
    def seed_admin(email, password):
        """Create or promote an administrator."""
        from app.services.admin_recovery import recover_admin_account

        email = (email or next(iter(app.config["ADMIN_EMAILS"]))).strip().lower()
        try:
            recover_admin_account(email, password, actor="seed-admin")
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        ensure_defaults()
        click.echo(f"Administrator ready: {email}")

    @app.cli.command("recover-admin")
    @click.option("--email", envvar="ADMIN_EMAIL")
    @click.option(
        "--password",
        envvar="ADMIN_RECOVERY_PASSWORD",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
    )
    def recover_admin(email, password):
        """Reactivate an administrator, reset its password, and revoke its sessions."""
        from app.services.admin_recovery import recover_admin_account

        email = (email or next(iter(app.config["ADMIN_EMAILS"]))).strip().lower()
        try:
            _, created = recover_admin_account(email, password)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        ensure_defaults()
        action = "created" if created else "recovered"
        click.echo(f"Administrator {action}: {email}")
        click.echo("Existing sessions for this account have been revoked.")

    @app.cli.command("reset-two-factor")
    @click.option("--email", prompt=True)
    def reset_two_factor(email):
        """Disable 2FA for one account through trusted host access."""
        from app.services.admin_recovery import reset_two_factor_account

        try:
            user, was_enabled = reset_two_factor_account(email)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        state = "disabled" if was_enabled else "already off"
        click.echo(f"Two-factor authentication is {state} for {user.email}.")
        click.echo("Existing sessions for this account have been revoked.")

    @app.cli.command("check-production")
    def check_production():
        """Fail unless the loaded production configuration is safe to start."""
        if app.config["APP_ENV"] != "production":
            raise click.ClickException("Set APP_ENV=production before running this check.")
        issues = production_issues(app.config)
        if issues:
            raise click.ClickException("\n".join(issues))
        click.echo("Production configuration check passed.")

    @app.cli.command("scan-product-health")
    def scan_all_product_health():
        """Refresh non-network product health signals for every shop."""
        from app.models import Shop
        from app.services.growth import scan_product_health

        total_healthy = 0
        total_attention = 0
        for shop in db.session.scalars(db.select(Shop).order_by(Shop.id)):
            summary = scan_product_health(shop)
            total_healthy += summary["healthy"]
            total_attention += summary["needs_attention"]
        db.session.commit()
        click.echo(f"Product health scan complete: {total_healthy} healthy, {total_attention} need attention.")

    @app.cli.command("deploy-release")
    def deploy_release():
        """Apply migrations and seed idempotent defaults before web startup."""
        from flask_migrate import upgrade
        from app.services.health import assert_database_schema_current

        lock_connection = None
        try:
            if db.engine.dialect.name == "postgresql":
                lock_connection = db.engine.connect()
                lock_connection.execute(
                    text("SELECT pg_advisory_lock(:lock_key)"),
                    {"lock_key": RELEASE_LOCK_KEY},
                )
                lock_connection.commit()
                click.echo("Database release lock acquired.")

            click.echo("Applying database migrations...")
            upgrade()
            click.echo("Verifying application database schema...")
            assert_database_schema_current()
            click.echo("Seeding default application data...")
            ensure_defaults()
            click.echo("Database release completed successfully.")
        finally:
            if lock_connection is not None:
                try:
                    lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": RELEASE_LOCK_KEY},
                    )
                    lock_connection.commit()
                finally:
                    lock_connection.close()

    return app
