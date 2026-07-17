import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _database_url():
    value = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'sulitshelf.db'}")
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgres://")
    elif value.startswith("postgresql://") and "+psycopg" not in value:
        value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", BASE_DIR / "instance" / "uploads"))
    ADMIN_EMAILS = {item.strip().lower() for item in os.getenv("ADMIN_EMAILS", "delacruzedward0735@gmail.com").split(",") if item.strip()}
    SERVICE_CONTACT_EMAIL = os.getenv("SERVICE_CONTACT_EMAIL", "").strip().lower()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    DODO_PAYMENTS_API_KEY = os.getenv("DODO_PAYMENTS_API_KEY", "")
    DODO_PAYMENTS_WEBHOOK_KEY = os.getenv("DODO_PAYMENTS_WEBHOOK_KEY", "")
    DODO_PAYMENTS_ENVIRONMENT = os.getenv("DODO_PAYMENTS_ENVIRONMENT", "test_mode")
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")
    OAUTH_REDIRECT_BASE_URL = os.getenv("OAUTH_REDIRECT_BASE_URL", PUBLIC_BASE_URL).rstrip("/")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
    FACEBOOK_CLIENT_ID = os.getenv("FACEBOOK_CLIENT_ID", "")
    FACEBOOK_CLIENT_SECRET = os.getenv("FACEBOOK_CLIENT_SECRET", "")
    FACEBOOK_GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "v25.0")
    IMAGE_STORAGE_BACKEND = os.getenv("IMAGE_STORAGE_BACKEND", "auto").lower()
    CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")
    CLOUDINARY_FOLDER = os.getenv("CLOUDINARY_FOLDER", "sulitshelf")
    IMAGE_WEBP_QUALITY = int(os.getenv("IMAGE_WEBP_QUALITY", "82"))
    IMAGE_MAX_DIMENSION = int(os.getenv("IMAGE_MAX_DIMENSION", "1600"))


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    RATELIMIT_ENABLED = False
    SECRET_KEY = "test-secret"
