import re

from sqlalchemy import inspect, text

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import DonationTier, PlatformSettings, User
from app.services.admin_recovery import recover_admin_account
from app.services.billing import ensure_defaults
from app.services.catalog import detect_marketplace
from app.services import health as health_service
from app.services.production import production_issues
from app.services.two_factor import decrypt_secret, totp_code


def _production_config():
    return {
        "APP_ENV": "production",
        "SECRET_KEY": "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL",
        "TWO_FACTOR_ENCRYPTION_KEY": "twofactorZYXWVUTSRQPONMLKJIHGFEDCBA9876543210",
        "COMMISSION_HASH_KEY": "LKJIHGFEDCBAzyxwvutsrqponmlkjihgfedcba9876543210",
        "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://user:password@db:5432/sulitshelf",
        "TRUSTED_HOSTS": ["sulitshelf.ph"],
        "SESSION_COOKIE_SECURE": True,
        "REMEMBER_COOKIE_SECURE": True,
        "PUBLIC_BASE_URL": "https://sulitshelf.ph",
        "OAUTH_REDIRECT_BASE_URL": "https://sulitshelf.ph",
        "PROXY_FIX_X_FOR": 1,
        "PROXY_FIX_X_PROTO": 1,
        "PROXY_FIX_X_HOST": 1,
        "PROXY_FIX_X_PORT": 0,
        "PROXY_FIX_X_PREFIX": 0,
        "RATELIMIT_STORAGE_URI": "redis://redis:6379/0",
        "IMAGE_STORAGE_BACKEND": "cloudinary",
        "CLOUDINARY_URL": "cloudinary://key:secret@cloud-name",
        "ADMIN_EMAILS": {"admin@sulitshelf.ph"},
        "DODO_PAYMENTS_API_KEY": "",
        "DODO_PAYMENTS_WEBHOOK_KEY": "",
        "DODO_PAYMENTS_ENVIRONMENT": "live_mode",
    }


def test_production_configuration_accepts_only_safe_dependencies():
    config = _production_config()
    assert production_issues(config) == []

    config["SECRET_KEY"] = "short"
    config["TWO_FACTOR_ENCRYPTION_KEY"] = "weak"
    config["COMMISSION_HASH_KEY"] = "weak"
    config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///production.db"
    config["RATELIMIT_STORAGE_URI"] = "memory://"
    config["CLOUDINARY_URL"] = "not-a-cloudinary-url"
    config["HEARTBEAT_TOKEN"] = "weak"
    issues = production_issues(config)
    assert any("SECRET_KEY" in issue for issue in issues)
    assert any("TWO_FACTOR_ENCRYPTION_KEY" in issue for issue in issues)
    assert any("COMMISSION_HASH_KEY" in issue for issue in issues)
    assert any("PostgreSQL" in issue for issue in issues)
    assert any("Redis" in issue for issue in issues)
    assert any("Cloudinary" in issue for issue in issues)
    assert any("HEARTBEAT_TOKEN" in issue for issue in issues)


def test_malformed_redis_port_is_reported_before_extension_startup():
    config = _production_config()
    config["RATELIMIT_STORAGE_URI"] = "redis://redis.internal:6379BROKEN:6379/0"
    issues = production_issues(config)
    assert any("RATELIMIT_STORAGE_URI" in issue for issue in issues)

    config = _production_config()
    config["RATELIMIT_STORAGE_URI"] = "redis://[broken-ipv6/0"
    issues = production_issues(config)
    assert any("RATELIMIT_STORAGE_URI" in issue for issue in issues)


def test_password_recovery_requires_a_provider_and_verified_sender():
    config = _production_config()
    config.update(
        PASSWORD_RESET_ENABLED=True,
        EMAIL_PROVIDER="auto",
        EMAIL_FAILOVER_ENABLED=True,
        RESEND_API_KEY="",
        MAILERSEND_API_TOKEN="",
        MAIL_FROM_EMAIL="",
    )
    issues = production_issues(config)
    assert any("RESEND_API_KEY" in issue for issue in issues)
    assert any("MAIL_FROM_EMAIL" in issue for issue in issues)

    config.update(
        EMAIL_PROVIDER="resend",
        RESEND_API_KEY="re_production_key",
        MAIL_FROM_EMAIL="no-reply@sulitshelf.ph",
    )
    assert production_issues(config) == []


def test_configured_crm_email_is_validated_even_when_password_reset_is_disabled():
    config = _production_config()
    config.update(
        PASSWORD_RESET_ENABLED=False,
        EMAIL_PROVIDER="invalid-provider",
        RESEND_API_KEY="re_production_key",
        MAIL_FROM_EMAIL="",
    )
    issues = production_issues(config)
    assert any("EMAIL_PROVIDER" in issue for issue in issues)
    assert any("MAIL_FROM_EMAIL" in issue for issue in issues)


def test_health_checks_and_security_headers(client):
    live = client.get("/health/live")
    ready = client.get("/health/ready")
    heartbeat = client.get("/health/heartbeat")
    assert live.status_code == 200
    assert ready.status_code == 200
    assert heartbeat.status_code == 200
    assert live.json["status"] == "ok"
    assert live.json["code_release"] == "2026.07.19-signout.28"
    assert ready.json["status"] == "ok"
    assert ready.json["checks"]["database"]["status"] == "ok"
    assert ready.json["checks"]["redis"]["status"] == "skipped"
    assert heartbeat.json["heartbeat"] == "SULITSHELF_OK"
    assert heartbeat.headers["Cache-Control"] == "no-store"

    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "img-src 'self' data: blob: https://res.cloudinary.com" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Request-ID"]


def test_heartbeat_supports_an_optional_bearer_token(client, app):
    token = "0123456789abcdefghijklmnopqrstuvwxyz"
    app.config["HEARTBEAT_TOKEN"] = token

    assert client.get("/health/heartbeat").status_code == 404
    assert client.get(
        "/health/heartbeat", headers={"Authorization": "Bearer wrong-token"}
    ).status_code == 404

    response = client.get(
        "/health/heartbeat", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json["heartbeat"] == "SULITSHELF_OK"


def test_readiness_fails_closed_when_redis_is_unavailable(client, app, monkeypatch):
    class UnavailableRedis:
        def ping(self):
            raise ConnectionError("synthetic Redis outage")

    app.config["RATELIMIT_STORAGE_URI"] = "redis://redis.example.test:6379/0"
    monkeypatch.setattr(health_service, "_redis_client", lambda _url: UnavailableRedis())

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json["status"] == "not_ready"
    assert response.json["reason"] == "redis_unavailable"
    assert response.json["checks"]["database"]["status"] == "ok"
    assert response.json["checks"]["redis"]["status"] == "failed"


def test_two_factor_setup_fails_safely_when_encryption_is_unconfigured(client, app):
    app.config["TWO_FACTOR_ENCRYPTION_KEY"] = ""

    response = client.get("/security/2fa/setup")

    assert response.status_code == 503
    assert b"Authenticator setup is paused" in response.data
    assert b"Internal Server Error" not in response.data
    assert response.headers["Cache-Control"] == "private, no-store"


def test_csrf_runs_after_request_metadata_and_valid_two_factor_post_succeeds(tmp_path):
    database_path = tmp_path / "csrf-enabled.db"

    class CsrfEnabledConfig(TestConfig):
        WTF_CSRF_ENABLED = True
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path.as_posix()}"
        UPLOAD_ROOT = tmp_path / "csrf-uploads"

    csrf_app = create_app(CsrfEnabledConfig)
    with csrf_app.app_context():
        db.create_all()
        ensure_defaults()
    csrf_client = csrf_app.test_client()
    secure_origin = "https://localhost"

    # An early CSRF rejection must retain the request security context and may
    # never be converted into the production-only g.csp_nonce 500 regression.
    rejected = csrf_client.post(
        "/security/2fa/setup",
        base_url=secure_origin,
        data={"code": "000000"},
    )
    assert rejected.status_code == 303
    assert rejected.headers["X-Request-ID"]
    assert "Content-Security-Policy" in rejected.headers

    register_page = csrf_client.get("/register", base_url=secure_origin)
    register_token = re.search(rb'name="csrf_token" value="([^"]+)"', register_page.data).group(1).decode()
    registered = csrf_client.post(
        "/register",
        data={
            "csrf_token": register_token,
            "email": "csrf-2fa@example.com",
            "display_name": "CSRF 2FA",
            "password": "a-production-style-password",
        },
        base_url=secure_origin,
        headers={"Referer": f"{secure_origin}/register"},
    )
    assert registered.status_code == 302

    setup_page = csrf_client.get("/security/2fa/setup", base_url=secure_origin)
    assert setup_page.status_code == 200
    assert setup_page.headers["Referrer-Policy"] == "same-origin"
    setup_token = re.search(rb'name="csrf_token" value="([^"]+)"', setup_page.data).group(1).decode()
    with csrf_client.session_transaction() as browser_session:
        pending_ciphertext = browser_session["two_factor_setup"]["ciphertext"]
    with csrf_app.app_context():
        secret = decrypt_secret(pending_ciphertext)

    enabled = csrf_client.post(
        "/security/2fa/setup",
        data={"csrf_token": setup_token, "code": totp_code(secret)},
        base_url=secure_origin,
        headers={"Referer": f"{secure_origin}/security/2fa/setup"},
    )
    assert enabled.status_code == 200
    assert b"TWO-FACTOR AUTHENTICATION IS ON" in enabled.data


def test_outdated_two_factor_schema_is_reported_before_user_loading(client, app):
    with app.app_context():
        db.session.execute(text("DROP TABLE two_factor_recovery_code"))
        db.session.commit()
        app.extensions.pop("sulitshelf_schema_check", None)

    setup = client.get("/security/2fa/setup")
    assert setup.status_code == 503
    assert b"database update" in setup.data
    assert b"Internal Server Error" not in setup.data

    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json["reason"] == "database_schema_outdated"
    assert ready.json["checks"]["database"]["missing_schema_items"] >= 1


def test_invalid_request_id_is_replaced(client):
    response = client.get("/health/live", headers={"X-Request-ID": "unsafe request id"})
    assert response.headers["X-Request-ID"] != "unsafe request id"
    assert len(response.headers["X-Request-ID"]) == 32


def test_safe_error_page_does_not_show_a_traceback(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert b"Not Found" in response.data
    assert b"Traceback" not in response.data


def test_session_version_revokes_an_existing_login(client, app):
    client.post(
        "/register",
        data={
            "email": "session@example.com",
            "display_name": "Session User",
            "password": "a-strong-session-password",
        },
    )
    with client.session_transaction() as session:
        assert ":" in session["_user_id"]

    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "session@example.com"))
        user.session_version += 1
        db.session.commit()

    response = client.get("/studio/", follow_redirects=True)
    assert response.status_code == 200
    assert b"Welcome back" in response.data
    assert b"Promoter Studio" not in response.data


def test_admin_recovery_revokes_old_sessions(app):
    with app.app_context():
        user, created = recover_admin_account(
            "recover@example.com", "first-administrator-password", actor="test"
        )
        assert created
        original_version = user.session_version
        user, created = recover_admin_account(
            "recover@example.com", "second-administrator-password", actor="test"
        )
        assert not created
        assert user.session_version == original_version + 1


def test_password_and_marketplace_input_boundaries(client):
    response = client.post(
        "/register",
        data={
            "email": "short@example.com",
            "display_name": "Short Password",
            "password": "only-eleven",
        },
    )
    assert b"12\xe2\x80\x93128 characters" in response.data
    assert detect_marketplace("https://shopee.ph:444/item") is None
    assert detect_marketplace("https://shopee.ph/item\nInjected") is None
    assert detect_marketplace("https://shopee.ph/" + "a" * 2049) is None


def test_deploy_release_migrates_and_seeds_a_fresh_database(tmp_path):
    database_path = tmp_path / "release-command.db"

    class ReleaseTestConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path.as_posix()}"
        UPLOAD_ROOT = tmp_path / "release-uploads"

    release_app = create_app(ReleaseTestConfig)
    runner = release_app.test_cli_runner()

    first_run = runner.invoke(args=["deploy-release"])
    assert first_run.exit_code == 0, first_run.output
    assert "Verifying application database schema..." in first_run.output
    assert "Database release completed successfully." in first_run.output

    with release_app.app_context():
        table_names = set(inspect(db.engine).get_table_names())
        assert "alembic_version" in table_names
        assert "platform_settings" in table_names
        assert "donation_tier" in table_names
        assert "password_reset_token" in table_names
        assert "product_metric_hourly" in table_names
        assert "commission_import" in table_names
        assert "commission_entry" in table_names
        assert "two_factor_recovery_code" in table_names
        user_columns = {column["name"] for column in inspect(db.engine).get_columns("user")}
        assert {"two_factor_secret_ciphertext", "two_factor_enabled_at", "two_factor_last_counter"} <= user_columns
        assert db.session.get(PlatformSettings, 1) is not None
        assert db.session.get(DonationTier, "coffee") is not None

    second_run = runner.invoke(args=["deploy-release"])
    assert second_run.exit_code == 0, second_run.output
