import time
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlparse

from flask import current_app
from redis import Redis
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import PlatformSettings, Product, Shop, User


PROCESS_STARTED_AT = datetime.now(timezone.utc)
PROCESS_STARTED_MONOTONIC = time.monotonic()
SCHEMA_CACHE_SECONDS = 60


@lru_cache(maxsize=4)
def _redis_client(url):
    return Redis.from_url(
        url,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        health_check_interval=30,
    )


def liveness_report():
    return {
        "status": "ok",
        "version": current_app.config["APP_VERSION"],
        "code_release": current_app.config["CODE_RELEASE"],
        "started_at": PROCESS_STARTED_AT.isoformat(),
        "uptime_seconds": round(time.monotonic() - PROCESS_STARTED_MONOTONIC, 3),
    }


def database_schema_issues(*, refresh=False):
    """Return missing application tables/columns without querying mapped rows.

    SQLAlchemy model queries can fail with a low-level ``UndefinedColumn`` error
    when a deployment starts against a database that has not reached the latest
    Alembic revision. Inspecting the catalog first gives release commands,
    readiness checks, and security routes one consistent fail-closed signal.
    """
    now = time.monotonic()
    cache = current_app.extensions.get("sulitshelf_schema_check")
    if (
        not refresh
        and cache
        and now - cache["checked_at"] < SCHEMA_CACHE_SECONDS
    ):
        return list(cache["issues"])

    inspector = inspect(db.engine)
    actual_tables = set(inspector.get_table_names())
    issues = []
    for table_name, table in db.metadata.tables.items():
        if table_name not in actual_tables:
            issues.append(f"missing table: {table_name}")
            continue
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name in sorted(set(table.columns.keys()) - actual_columns):
            issues.append(f"missing column: {table_name}.{column_name}")

    current_app.extensions["sulitshelf_schema_check"] = {
        "checked_at": now,
        "issues": tuple(issues),
    }
    return issues


def assert_database_schema_current():
    issues = database_schema_issues(refresh=True)
    if issues:
        detail = "; ".join(issues[:12])
        if len(issues) > 12:
            detail += f"; and {len(issues) - 12} more"
        raise RuntimeError(
            "Database schema is behind the application. Run `python -m flask "
            f"--app run.py db upgrade`. Detected: {detail}"
        )


def readiness_report():
    checks = {}
    reason = None

    started = time.monotonic()
    try:
        db.session.execute(text("SELECT 1"))
        schema_issues = database_schema_issues(refresh=True)
        if schema_issues:
            reason = "database_schema_outdated"
            checks["database"] = {
                "status": "failed",
                "reason": reason,
                "missing_schema_items": len(schema_issues),
                "latency_ms": _elapsed_ms(started),
            }
            current_app.logger.error(
                "health dependency failed dependency=database reason=%s issues=%s",
                reason,
                "; ".join(schema_issues[:12]),
            )
        elif not db.session.get(PlatformSettings, 1):
            reason = "database_not_seeded"
            checks["database"] = {
                "status": "failed",
                "reason": reason,
                "latency_ms": _elapsed_ms(started),
            }
        else:
            checks["database"] = {"status": "ok", "latency_ms": _elapsed_ms(started)}
    except Exception as error:
        db.session.rollback()
        reason = "database_unavailable"
        checks["database"] = {
            "status": "failed",
            "reason": reason,
            "latency_ms": _elapsed_ms(started),
        }
        current_app.logger.warning(
            "health dependency failed dependency=database error_type=%s",
            type(error).__name__,
        )

    redis_url = current_app.config["RATELIMIT_STORAGE_URI"]
    if urlparse(redis_url).scheme in {"redis", "rediss"}:
        started = time.monotonic()
        try:
            _redis_client(redis_url).ping()
            checks["redis"] = {"status": "ok", "latency_ms": _elapsed_ms(started)}
        except Exception as error:
            reason = reason or "redis_unavailable"
            checks["redis"] = {
                "status": "failed",
                "reason": "redis_unavailable",
                "latency_ms": _elapsed_ms(started),
            }
            current_app.logger.warning(
                "health dependency failed dependency=redis error_type=%s",
                type(error).__name__,
            )
    else:
        checks["redis"] = {"status": "skipped", "reason": "not_configured"}

    report = liveness_report()
    report["checks"] = checks
    if reason:
        report["status"] = "not_ready"
        report["reason"] = reason
        return report, 503
    return report, 200


def admin_health_report():
    # Administrator-safe operational snapshot. Never return URLs with
    # credentials, API keys, bearer tokens, passwords, or provider secrets.
    report, readiness_status = readiness_report()
    checks = dict(report.get("checks", {}))
    counts = {
        "users_total": 0,
        "active_promoters": 0,
        "shops_total": 0,
        "products_total": 0,
        "public_products": 0,
        "paused_products": 0,
    }
    recommendations = []

    try:
        counts["users_total"] = db.session.scalar(db.select(db.func.count(User.id))) or 0
        counts["active_promoters"] = (
            db.session.scalar(
                db.select(db.func.count(User.id)).where(
                    User.role == "promoter",
                    User.is_active_account.is_(True),
                )
            )
            or 0
        )
        counts["shops_total"] = db.session.scalar(db.select(db.func.count(Shop.id))) or 0
        counts["products_total"] = db.session.scalar(db.select(db.func.count(Product.id))) or 0
        counts["paused_products"] = (
            db.session.scalar(db.select(db.func.count(Product.id)).where(Product.status != "active"))
            or 0
        )
        counts["public_products"] = (
            db.session.scalar(
                db.select(db.func.count(Product.id))
                .join(Shop, Product.shop_id == Shop.id)
                .join(User, Shop.owner_id == User.id)
                .where(
                    Product.status == "active",
                    User.is_active_account.is_(True),
                )
            )
            or 0
        )
        catalog_status = "ok" if counts["public_products"] else "warning"
        checks["catalog"] = {
            "status": catalog_status,
            "public_products": counts["public_products"],
            "products_total": counts["products_total"],
            "paused_products": counts["paused_products"],
        }
        if catalog_status == "warning":
            recommendations.append(
                "No public products are visible. Publish or restore genuine active listings before promoting the mall."
            )
    except Exception as error:
        db.session.rollback()
        checks["catalog"] = {"status": "failed", "reason": "catalog_query_failed"}
        recommendations.append(
            "Catalog diagnostics failed. Check the application logs and database schema before publishing changes."
        )
        current_app.logger.warning(
            "admin health catalog check failed error_type=%s",
            type(error).__name__,
        )

    requested_storage = current_app.config["IMAGE_STORAGE_BACKEND"]
    cloudinary_configured = bool(current_app.config["CLOUDINARY_URL"])
    if requested_storage == "auto":
        effective_storage = "cloudinary" if cloudinary_configured else "local"
    else:
        effective_storage = requested_storage

    storage_status = "ok"
    storage_reason = None
    if requested_storage == "cloudinary" and not cloudinary_configured:
        storage_status = "failed"
        storage_reason = "cloudinary_not_configured"
        recommendations.append(
            "Cloudinary is selected but not configured. Set CLOUDINARY_URL before accepting image uploads."
        )
    elif current_app.config["IS_PRODUCTION"] and effective_storage == "local":
        storage_status = "warning"
        storage_reason = "local_storage_in_production"
        recommendations.append(
            "Image storage is local in production. Use Cloudinary or verified persistent storage to prevent upload loss."
        )
    checks["storage"] = {
        "status": storage_status,
        "backend": effective_storage,
        "reason": storage_reason,
    }

    redis_status = checks.get("redis", {}).get("status")
    if current_app.config["IS_PRODUCTION"] and redis_status == "skipped":
        recommendations.append(
            "Redis is not configured. Use Redis for shared rate-limit state before scaling to multiple web instances."
        )

    heartbeat_configured = bool(current_app.config["HEARTBEAT_TOKEN"])
    if current_app.config["IS_PRODUCTION"] and not heartbeat_configured:
        recommendations.append(
            "HEARTBEAT_TOKEN is not configured. Add one for authenticated external uptime monitoring."
        )

    database_backend = db.engine.url.get_backend_name()
    if current_app.config["IS_PRODUCTION"] and database_backend != "postgresql":
        recommendations.append(
            "Production is not using PostgreSQL. Confirm DATABASE_URL points to the intended production database."
        )

    public_url = urlparse(current_app.config["PUBLIC_BASE_URL"])
    public_host = public_url.netloc or public_url.path or "local"
    email_configured = bool(
        current_app.config["RESEND_API_KEY"] or current_app.config["MAILERSEND_API_TOKEN"]
    )

    critical = readiness_status != 200 or any(
        checks.get(name, {}).get("status") == "failed"
        for name in ("database", "redis", "catalog", "storage")
    )
    warning = any(
        checks.get(name, {}).get("status") == "warning"
        for name in ("catalog", "storage")
    ) or (
        current_app.config["IS_PRODUCTION"]
        and (redis_status == "skipped" or not heartbeat_configured)
    )

    status = "critical" if critical else ("warning" if warning else "healthy")
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "app": {
            "version": report.get("version"),
            "code_release": report.get("code_release"),
            "started_at": report.get("started_at"),
            "uptime_seconds": report.get("uptime_seconds"),
        },
        "checks": checks,
        "counts": counts,
        "configuration": {
            "environment": current_app.config["APP_ENV"],
            "public_host": public_host,
            "database_backend": database_backend,
            "heartbeat_configured": heartbeat_configured,
            "password_reset_enabled": bool(current_app.config["PASSWORD_RESET_ENABLED"]),
            "email_configured": email_configured,
            "storage_backend": effective_storage,
        },
        "recommendations": recommendations,
    }


def _elapsed_ms(started):
    return round((time.monotonic() - started) * 1000, 3)
