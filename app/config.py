import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default, minimum=0):
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error


def _env_csv(name):
    return [item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip()]


def _database_url():
    value = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'sulitshelf.db'}").strip()
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgres://")
    elif value.startswith("postgresql://") and "+psycopg" not in value:
        value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def _public_base_url():
    """Prefer an explicit canonical URL, then Render's trusted service URL."""
    value = (
        os.getenv("PUBLIC_BASE_URL", "").strip()
        or os.getenv("RENDER_EXTERNAL_URL", "").strip()
        or "http://localhost:5000"
    )
    return value.rstrip("/")


def _engine_options(database_url):
    options = {"pool_pre_ping": True}
    if database_url.startswith("postgresql+"):
        options.update(
            pool_size=_env_int("DB_POOL_SIZE", 5, 1),
            max_overflow=_env_int("DB_MAX_OVERFLOW", 5, 0),
            pool_timeout=_env_int("DB_POOL_TIMEOUT", 30, 1),
            pool_recycle=_env_int("DB_POOL_RECYCLE", 1800, 60),
        )
    return options


DATABASE_URL = _database_url()
CANONICAL_PUBLIC_BASE_URL = _public_base_url()
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"


class Config:
    CODE_RELEASE = "2026.07.19-signout.28"
    APP_ENV = APP_ENV
    IS_PRODUCTION = IS_PRODUCTION
    APP_VERSION = os.getenv("APP_VERSION", "dev").strip()[:64]
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(DATABASE_URL)
    SQLALCHEMY_RECORD_QUERIES = False

    # Keep one MiB for multipart boundaries and text fields. The original image
    # itself remains capped at eight MiB in the storage service.
    IMAGE_UPLOAD_MAX_BYTES = 8 * 1024 * 1024
    MAX_CONTENT_LENGTH = IMAGE_UPLOAD_MAX_BYTES + (1024 * 1024)
    MAX_FORM_MEMORY_SIZE = 500_000
    MAX_FORM_PARTS = 100
    UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", BASE_DIR / "instance" / "uploads"))

    # Used only as the default target for trusted administrator CLI recovery.
    # Web and OAuth registrations are always promoter accounts.
    ADMIN_EMAILS = set(_env_csv("ADMIN_EMAILS") or ([] if IS_PRODUCTION else ["admin@sulitshelf.local"]))
    SERVICE_CONTACT_EMAIL = os.getenv("SERVICE_CONTACT_EMAIL", "").strip().lower()
    TRUSTED_HOSTS = _env_csv("TRUSTED_HOSTS") or None

    PROXY_FIX_X_FOR = _env_int("PROXY_FIX_X_FOR", 0)
    PROXY_FIX_X_PROTO = _env_int("PROXY_FIX_X_PROTO", 0)
    PROXY_FIX_X_HOST = _env_int("PROXY_FIX_X_HOST", 0)
    PROXY_FIX_X_PORT = _env_int("PROXY_FIX_X_PORT", 0)
    PROXY_FIX_X_PREFIX = _env_int("PROXY_FIX_X_PREFIX", 0)

    SESSION_COOKIE_NAME = "__Host-sulitshelf_session" if IS_PRODUCTION else "sulitshelf_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
    SESSION_COOKIE_PATH = "/"
    SESSION_REFRESH_EACH_REQUEST = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_env_int("SESSION_LIFETIME_HOURS", 12, 1))
    REMEMBER_COOKIE_NAME = "__Host-sulitshelf_remember" if IS_PRODUCTION else "sulitshelf_remember"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _env_bool("REMEMBER_COOKIE_SECURE", IS_PRODUCTION)
    REMEMBER_COOKIE_DURATION = timedelta(days=_env_int("REMEMBER_COOKIE_DAYS", 14, 1))

    WTF_CSRF_TIME_LIMIT = 3600
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://").strip()
    RATELIMIT_HEADERS_ENABLED = True
    HEARTBEAT_TOKEN = os.getenv("HEARTBEAT_TOKEN", "").strip()

    PUBLIC_BASE_URL = CANONICAL_PUBLIC_BASE_URL
    GOOGLE_SITE_VERIFICATION = os.getenv("GOOGLE_SITE_VERIFICATION", "").strip()
    OAUTH_REDIRECT_BASE_URL = os.getenv("OAUTH_REDIRECT_BASE_URL", PUBLIC_BASE_URL).rstrip("/")
    PREFERRED_URL_SCHEME = "https" if IS_PRODUCTION else "http"

    DODO_PAYMENTS_API_KEY = os.getenv("DODO_PAYMENTS_API_KEY", "")
    DODO_PAYMENTS_WEBHOOK_KEY = os.getenv("DODO_PAYMENTS_WEBHOOK_KEY", "")
    DODO_PAYMENTS_ENVIRONMENT = os.getenv("DODO_PAYMENTS_ENVIRONMENT", "test_mode")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
    FACEBOOK_CLIENT_ID = os.getenv("FACEBOOK_CLIENT_ID", "")
    FACEBOOK_CLIENT_SECRET = os.getenv("FACEBOOK_CLIENT_SECRET", "")
    FACEBOOK_GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "v25.0")

    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
    MAILERSEND_API_TOKEN = os.getenv("MAILERSEND_API_TOKEN", "").strip()
    MAIL_FROM_EMAIL = os.getenv("MAIL_FROM_EMAIL", SERVICE_CONTACT_EMAIL).strip().lower()
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "SulitShelf PH").strip()[:80]
    EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "auto").strip().lower()
    EMAIL_FAILOVER_ENABLED = _env_bool("EMAIL_FAILOVER_ENABLED", True)
    EMAIL_TIMEOUT_SECONDS = min(_env_int("EMAIL_TIMEOUT_SECONDS", 10, 3), 30)
    PASSWORD_RESET_TOKEN_MINUTES = min(_env_int("PASSWORD_RESET_TOKEN_MINUTES", 30, 10), 60)
    PASSWORD_RESET_ENABLED = _env_bool(
        "PASSWORD_RESET_ENABLED",
        bool(RESEND_API_KEY or MAILERSEND_API_TOKEN),
    )
    TWO_FACTOR_ENCRYPTION_KEY = os.getenv("TWO_FACTOR_ENCRYPTION_KEY", SECRET_KEY).strip()
    TWO_FACTOR_CHALLENGE_MINUTES = min(_env_int("TWO_FACTOR_CHALLENGE_MINUTES", 5, 2), 15)

    IMAGE_STORAGE_BACKEND = os.getenv("IMAGE_STORAGE_BACKEND", "auto").lower()
    CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")
    CLOUDINARY_FOLDER = os.getenv("CLOUDINARY_FOLDER", "sulitshelf")
    CLOUDINARY_UPLOAD_TIMEOUT_SECONDS = min(_env_int("CLOUDINARY_UPLOAD_TIMEOUT_SECONDS", 20, 5), 60)
    IMAGE_WEBP_QUALITY = _env_int("IMAGE_WEBP_QUALITY", 82, 1)
    IMAGE_MAX_DIMENSION = _env_int("IMAGE_MAX_DIMENSION", 1600, 320)

    CSV_IMPORT_MAX_BYTES = min(_env_int("CSV_IMPORT_MAX_BYTES", 2_000_000, 100_000), 8_000_000)
    BULK_PRODUCT_MAX_ROWS = min(_env_int("BULK_PRODUCT_MAX_ROWS", 250, 1), 1000)
    COMMISSION_MAX_ROWS = min(_env_int("COMMISSION_MAX_ROWS", 5000, 1), 20_000)
    PRODUCT_STALE_DAYS = min(_env_int("PRODUCT_STALE_DAYS", 30, 1), 365)
    COMMISSION_HASH_KEY = os.getenv("COMMISSION_HASH_KEY", SECRET_KEY).strip()

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO" if IS_PRODUCTION else "DEBUG").upper()
    SECURITY_HSTS_SECONDS = _env_int("SECURITY_HSTS_SECONDS", 31_536_000 if IS_PRODUCTION else 0)


class TestConfig(Config):
    APP_ENV = "test"
    IS_PRODUCTION = False
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    RATELIMIT_ENABLED = False
    SECRET_KEY = "test-secret"
    TWO_FACTOR_ENCRYPTION_KEY = "test-two-factor-encryption-key"
    TRUSTED_HOSTS = None
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    SECURITY_HSTS_SECONDS = 0
    PROXY_FIX_X_FOR = 0
    PROXY_FIX_X_PROTO = 0
    PROXY_FIX_X_HOST = 0
    PROXY_FIX_X_PORT = 0
    PROXY_FIX_X_PREFIX = 0
    PASSWORD_RESET_ENABLED = False
