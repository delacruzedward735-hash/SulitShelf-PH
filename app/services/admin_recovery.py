import json
from datetime import timedelta

from email_validator import EmailNotValidError, validate_email

from app.extensions import db
from app.models import AuditLog, Shop, User, utcnow
from app.services.two_factor import clear_two_factor


def recover_admin_account(email, password, *, actor="local-recovery"):
    """Create or recover an administrator using trusted local database access."""

    try:
        normalized_email = validate_email(
            (email or "").strip(), check_deliverability=False
        ).normalized.lower()
    except EmailNotValidError as error:
        raise ValueError("Enter a valid administrator email address.") from error
    if not 12 <= len(password or "") <= 128:
        raise ValueError("The recovery password must contain 12–128 characters.")

    user = db.session.scalar(db.select(User).where(User.email == normalized_email))
    created = user is None
    if created:
        user = User(
            email=normalized_email,
            display_name="SulitShelf Administrator",
            role="admin",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

    if not user.shop:
        slug = "sulitshelf-admin"
        suffix = 1
        while db.session.scalar(db.select(Shop).where(Shop.slug == slug)):
            suffix += 1
            slug = f"sulitshelf-admin-{suffix}"
        user.shop = Shop(
            name="SulitShelf Admin Picks",
            slug=slug,
            plan_key="free",
            subscription_status="free",
            subscription_source="open_source",
            subscription_ends_at=utcnow() + timedelta(days=36500),
            is_verified=True,
        )

    two_factor_was_enabled = user.two_factor_enabled
    clear_two_factor(user)
    user.role = "admin"
    user.is_active_account = True
    if not created:
        user.session_version += 1
    user.set_password(password)
    user.shop.plan_key = "free"
    user.shop.subscription_status = "free"
    user.shop.subscription_source = "open_source"
    user.shop.is_verified = True
    db.session.flush()
    db.session.add(
        AuditLog(
            admin_email=normalized_email,
            action="admin.account_recovered",
            target_type="user",
            target_id=str(user.id),
            details=json.dumps(
                {
                    "actor": actor,
                    "created": created,
                    "reactivated": True,
                    "two_factor_reset": two_factor_was_enabled,
                },
                separators=(",", ":"),
            ),
        )
    )
    db.session.commit()
    return user, created


def reset_two_factor_account(email, *, actor="local-two-factor-recovery"):
    """Disable 2FA through trusted host access and revoke every active session."""

    try:
        normalized_email = validate_email(
            (email or "").strip(), check_deliverability=False
        ).normalized.lower()
    except EmailNotValidError as error:
        raise ValueError("Enter a valid account email address.") from error
    user = db.session.scalar(db.select(User).where(User.email == normalized_email))
    if not user:
        raise ValueError("No SulitShelf account exists for that email address.")
    was_enabled = user.two_factor_enabled
    clear_two_factor(user)
    user.session_version += 1
    db.session.add(
        AuditLog(
            admin_email=normalized_email,
            action="security.two_factor_recovered",
            target_type="user",
            target_id=str(user.id),
            details=json.dumps(
                {"actor": actor, "was_enabled": was_enabled, "sessions_revoked": True},
                separators=(",", ":"),
            ),
        )
    )
    db.session.commit()
    return user, was_enabled
