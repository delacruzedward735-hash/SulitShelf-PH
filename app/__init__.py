from pathlib import Path
import click
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db, limiter, login_manager, migrate, oauth


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    for directory in ("products", "receipts", "settings"):
        (Path(app.config["UPLOAD_ROOT"]) / directory).mkdir(parents=True, exist_ok=True)

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
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth.routes import bp as auth_bp
    from app.main.routes import bp as main_bp
    from app.promoter.routes import bp as promoter_bp
    from app.admin.routes import bp as admin_bp
    from app.payments.routes import bp as payments_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(promoter_bp, url_prefix="/studio")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(payments_bp, url_prefix="/payments")

    from app.services.billing import ensure_defaults
    @app.cli.command("seed-defaults")
    def seed_defaults():
        """Create default donation tiers and platform settings."""
        ensure_defaults()
        click.echo("Default donation configuration is ready.")

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
        """Reactivate an administrator and reset its password locally."""
        from app.services.admin_recovery import recover_admin_account

        email = (email or next(iter(app.config["ADMIN_EMAILS"]))).strip().lower()
        try:
            _, created = recover_admin_account(email, password)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        ensure_defaults()
        action = "created" if created else "recovered"
        click.echo(f"Administrator {action}: {email}")
        click.echo("If compromise is suspected, rotate SECRET_KEY to invalidate existing sessions.")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https://res.cloudinary.com; style-src 'self'; script-src 'self'; font-src 'self'; connect-src 'self'")
        return response

    return app
