from urllib.parse import urlparse


def _safe_url(value):
    """Parse configuration URLs without leaking low-level parser failures."""
    try:
        parsed = urlparse(str(value or ""))
        # Accessing these properties performs additional validation (notably
        # invalid IPv6 brackets and non-numeric ports).
        _ = parsed.hostname
        _ = parsed.port
        return parsed
    except (TypeError, ValueError):
        return None


def production_issues(config):
    if config.get("APP_ENV") != "production":
        return []

    issues = []
    secret = config.get("SECRET_KEY", "")
    if (
        len(secret) < 32
        or len(set(secret)) < 12
        or any(marker in secret.lower() for marker in ("replace", "change-me", "example"))
    ):
        issues.append("SECRET_KEY must be a unique random value of at least 32 characters.")
    commission_hash_key = config.get("COMMISSION_HASH_KEY", "")
    if (
        len(commission_hash_key) < 32
        or len(set(commission_hash_key)) < 12
        or any(marker in commission_hash_key.lower() for marker in ("replace", "change-me", "example"))
        or commission_hash_key == secret
    ):
        issues.append("COMMISSION_HASH_KEY must be a separate random value of at least 32 characters.")
    two_factor_key = config.get("TWO_FACTOR_ENCRYPTION_KEY", "")
    if (
        len(two_factor_key) < 32
        or len(set(two_factor_key)) < 12
        or any(marker in two_factor_key.lower() for marker in ("replace", "change-me", "example"))
        or two_factor_key in {secret, commission_hash_key}
    ):
        issues.append("TWO_FACTOR_ENCRYPTION_KEY must be a separate random value of at least 32 characters.")
    database_uri = str(config.get("SQLALCHEMY_DATABASE_URI", ""))
    database_url = _safe_url(database_uri)
    if (
        not database_url
        or database_url.scheme != "postgresql+psycopg"
        or not database_url.hostname
        or not database_url.username
        or not database_url.password
        or database_url.path in {"", "/"}
        or any(marker in database_uri.lower() for marker in ("replace", "change-me", "example"))
    ):
        issues.append("DATABASE_URL must use PostgreSQL through postgresql+psycopg://.")
    if not config.get("TRUSTED_HOSTS") or "*" in config.get("TRUSTED_HOSTS", []):
        issues.append("TRUSTED_HOSTS must contain the production domain.")
    if not config.get("SESSION_COOKIE_SECURE") or not config.get("REMEMBER_COOKIE_SECURE"):
        issues.append("SESSION_COOKIE_SECURE and REMEMBER_COOKIE_SECURE must be true.")

    public_url = _safe_url(config.get("PUBLIC_BASE_URL", ""))
    oauth_url = _safe_url(config.get("OAUTH_REDIRECT_BASE_URL", ""))
    if not public_url or public_url.scheme != "https" or not public_url.hostname:
        issues.append("PUBLIC_BASE_URL must be the public HTTPS origin.")
    elif public_url.hostname == "example.com" or public_url.hostname.endswith(".example.com"):
        issues.append("PUBLIC_BASE_URL must not use a placeholder example.com domain.")
    if not oauth_url or not public_url or oauth_url.scheme != "https" or oauth_url.netloc != public_url.netloc:
        issues.append("OAUTH_REDIRECT_BASE_URL must match the public HTTPS origin.")
    if public_url and public_url.hostname and config.get("TRUSTED_HOSTS"):
        trusted = config["TRUSTED_HOSTS"]
        if not any(
            public_url.hostname == item or (item.startswith(".") and public_url.hostname.endswith(item))
            for item in trusted
        ):
            issues.append("PUBLIC_BASE_URL host must be included in TRUSTED_HOSTS.")

    if any(config.get(key, 0) < 1 for key in ("PROXY_FIX_X_FOR", "PROXY_FIX_X_PROTO", "PROXY_FIX_X_HOST")):
        issues.append("Configure PROXY_FIX_X_FOR, PROXY_FIX_X_PROTO, and PROXY_FIX_X_HOST for the trusted TLS proxy.")
    if any(config.get(key, 0) > 5 for key in (
        "PROXY_FIX_X_FOR", "PROXY_FIX_X_PROTO", "PROXY_FIX_X_HOST", "PROXY_FIX_X_PORT", "PROXY_FIX_X_PREFIX"
    )):
        issues.append("ProxyFix hop counts above 5 are not accepted; configure the exact trusted proxy chain.")

    storage_uri = str(config.get("RATELIMIT_STORAGE_URI", ""))
    storage_url = _safe_url(storage_uri)
    if not storage_url or storage_url.scheme not in {"redis", "rediss"} or not storage_url.hostname:
        issues.append("RATELIMIT_STORAGE_URI must use shared Redis in production.")
    heartbeat_token = config.get("HEARTBEAT_TOKEN", "")
    if heartbeat_token and (
        len(heartbeat_token) < 32
        or len(set(heartbeat_token)) < 12
        or any(marker in heartbeat_token.lower() for marker in ("replace", "change-me", "example"))
    ):
        issues.append("HEARTBEAT_TOKEN must be a unique random value of at least 32 characters when enabled.")
    cloudinary_url = _safe_url(config.get("CLOUDINARY_URL", ""))
    if (
        config.get("IMAGE_STORAGE_BACKEND") != "cloudinary"
        or not cloudinary_url
        or cloudinary_url.scheme != "cloudinary"
        or not cloudinary_url.hostname
        or not cloudinary_url.username
        or not cloudinary_url.password
        or "replace" in config.get("CLOUDINARY_URL", "").lower()
    ):
        issues.append("Production image storage must use Cloudinary with CLOUDINARY_URL configured.")
    if not config.get("ADMIN_EMAILS") or any(
        "@" not in email or email.lower().endswith("@example.com") for email in config.get("ADMIN_EMAILS", [])
    ):
        issues.append("ADMIN_EMAILS must contain at least one real administrator email address.")
    if config.get("DODO_PAYMENTS_API_KEY"):
        if config.get("DODO_PAYMENTS_ENVIRONMENT") != "live_mode":
            issues.append("Dodo credentials in production must use DODO_PAYMENTS_ENVIRONMENT=live_mode.")
        if not config.get("DODO_PAYMENTS_WEBHOOK_KEY"):
            issues.append("DODO_PAYMENTS_WEBHOOK_KEY is required when Dodo payments are enabled.")
    email_is_configured = bool(config.get("RESEND_API_KEY") or config.get("MAILERSEND_API_TOKEN"))
    if config.get("PASSWORD_RESET_ENABLED") or email_is_configured:
        provider = config.get("EMAIL_PROVIDER", "auto")
        resend_configured = bool(config.get("RESEND_API_KEY"))
        mailersend_configured = bool(config.get("MAILERSEND_API_TOKEN"))
        if provider not in {"auto", "resend", "mailersend"}:
            issues.append("EMAIL_PROVIDER must be auto, resend, or mailersend.")
        if config.get("PASSWORD_RESET_ENABLED") and not resend_configured and not mailersend_configured:
            issues.append("RESEND_API_KEY or MAILERSEND_API_TOKEN is required when password reset is enabled.")
        if (
            provider in {"resend", "mailersend"}
            and not config.get("EMAIL_FAILOVER_ENABLED", True)
            and not (resend_configured if provider == "resend" else mailersend_configured)
        ):
            issues.append(f"{provider.title()} credentials are required for the selected EMAIL_PROVIDER.")
        sender = config.get("MAIL_FROM_EMAIL", "")
        if not sender or "@" not in sender or sender.startswith("@") or sender.endswith("@"):
            issues.append("MAIL_FROM_EMAIL must be a valid verified sender when transactional email is enabled.")
    return issues


def validate_production_config(config):
    issues = production_issues(config)
    if issues:
        formatted = "\n".join(f" - {item}" for item in issues)
        raise RuntimeError(f"Production configuration is unsafe:\n{formatted}")
