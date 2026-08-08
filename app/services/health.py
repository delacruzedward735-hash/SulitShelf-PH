import time
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlparse

from flask import current_app
from redis import Redis
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import PlatformSettings


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


def _elapsed_ms(started):
    return round((time.monotonic() - started) * 1000, 3)
