from datetime import datetime, timedelta, timezone
import re

from flask import current_app

from app.extensions import db
from app.models import Campaign, DonationTier, Plan, PlatformSettings, Product, Shop, utcnow


FREE_PRODUCT_LIMIT = 50
PRO_PRICE_CENTS = 4900
MANUAL_PRO_DAYS = 30

DEFAULT_PLANS = {
    "free": ("Free", 0, FREE_PRODUCT_LIMIT),
    "pro": ("Pro", PRO_PRICE_CENTS, 0),
}

DEFAULT_DONATION_TIERS = {
    "coffee": ("Buy us a coffee", "A small thank-you that keeps development moving.", 4900),
    "supporter": ("Project supporter", "Help with hosting, maintenance, and improvements.", 14900),
    "sponsor": ("Open-source sponsor", "Make a larger contribution to SulitShelf's future.", 49900),
}

DEFAULT_CAMPAIGNS = {
    "student-essentials-under-500": (
        "Student Essentials Under ₱500",
        "SULIT FOR STUDENTS",
        "Affordable study, desk, and everyday tech finds selected for Filipino students.",
        10,
    ),
    "brownout-survival-kit": (
        "Brownout Survival Kit",
        "READY WHEN POWER IS OUT",
        "Rechargeable lights, fans, power banks, and practical emergency essentials.",
        20,
    ),
    "budget-desk-setup": (
        "Budget Desk Setup",
        "WORK AND STUDY BETTER",
        "Useful accessories for a clean, comfortable setup without the premium price.",
        30,
    ),
}


def ensure_defaults():
    changed = False
    for key, (name, price_cents, product_limit) in DEFAULT_PLANS.items():
        if not db.session.get(Plan, key):
            db.session.add(
                Plan(
                    key=key,
                    name=name,
                    price_cents=price_cents,
                    product_limit=product_limit,
                )
            )
            changed = True
    for key, (name, description, amount) in DEFAULT_DONATION_TIERS.items():
        if not db.session.get(DonationTier, key):
            db.session.add(DonationTier(key=key, name=name, description=description, amount_cents=amount))
            changed = True
    if not db.session.get(PlatformSettings, 1):
        db.session.add(PlatformSettings(id=1))
        changed = True
    for slug, (title, eyebrow, description, sort_order) in DEFAULT_CAMPAIGNS.items():
        if not db.session.scalar(db.select(Campaign).where(Campaign.slug == slug)):
            db.session.add(
                Campaign(
                    title=title,
                    slug=slug,
                    eyebrow=eyebrow,
                    description=description,
                    sort_order=sort_order,
                )
            )
            changed = True
    if changed:
        db.session.commit()


def aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def provider_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return aware(parsed)


def active_subscription(shop):
    """Return whether the shop currently has unlimited Pro access."""
    if not shop:
        return False
    if shop.owner and shop.owner.is_admin:
        return True
    end = aware(shop.subscription_ends_at)
    return (
        shop.plan_key == "pro"
        and shop.subscription_status == "active"
        and end is not None
        and end > utcnow()
    )


def active_product_count(shop):
    return db.session.scalar(
        db.select(db.func.count(Product.id)).where(Product.shop_id == shop.id, Product.status == "active")
    ) or 0


def product_count(shop):
    return db.session.scalar(db.select(db.func.count(Product.id)).where(Product.shop_id == shop.id)) or 0


def product_limit(shop):
    if active_subscription(shop):
        return None
    plan = db.session.get(Plan, "free")
    return plan.product_limit if plan and plan.product_limit > 0 else FREE_PRODUCT_LIMIT


def capacity(shop):
    used = product_count(shop)
    active = active_product_count(shop)
    limit = product_limit(shop)
    return {"used": used, "active": active, "limit": limit, "remaining": None if limit is None else max(limit - used, 0)}


def can_publish(shop):
    usage = capacity(shop)
    return usage["limit"] is None or usage["used"] < usage["limit"]


def can_activate(shop):
    limit = product_limit(shop)
    return limit is None or active_product_count(shop) < limit


def pause_products_above_free_limit(shop):
    """Pause overflow listings without deleting products or uploaded images."""
    active_products = list(
        db.session.scalars(
            db.select(Product)
            .where(Product.shop_id == shop.id, Product.status == "active")
            .order_by(Product.created_at.asc(), Product.id.asc())
        )
    )
    for product in active_products[FREE_PRODUCT_LIMIT:]:
        product.status = "paused"
    return max(len(active_products) - FREE_PRODUCT_LIMIT, 0)


def downgrade_to_free(shop, status="free"):
    shop.plan_key = "free"
    shop.subscription_status = status
    paused = pause_products_above_free_limit(shop)
    return paused


def sync_expired_subscription(shop):
    if shop.plan_key != "pro" or shop.subscription_status != "active":
        return 0
    end = aware(shop.subscription_ends_at)
    if end and end > utcnow():
        return 0
    return downgrade_to_free(shop, status="expired")


def sync_all_expired_subscriptions():
    expired_shops = list(
        db.session.scalars(
            db.select(Shop).where(
                Shop.plan_key == "pro",
                Shop.subscription_status == "active",
                Shop.subscription_ends_at <= utcnow(),
            ).with_for_update(skip_locked=True)
        )
    )
    for shop in expired_shops:
        downgrade_to_free(shop, status="expired")
    if expired_shops:
        db.session.commit()
    return len(expired_shops)


def grant_manual_pro(shop, price_cents=None):
    current_end = aware(shop.subscription_ends_at)
    start = current_end if active_subscription(shop) and shop.subscription_source == "manual" else utcnow()
    shop.plan_key = "pro"
    shop.subscription_status = "active"
    shop.subscription_source = "manual"
    shop.subscription_external_id = None
    shop.subscription_customer_id = None
    shop.subscription_product_id = None
    plan = db.session.get(Plan, "pro")
    shop.subscription_price_cents = price_cents if price_cents is not None else plan.price_cents
    shop.subscription_event_at = utcnow()
    shop.subscription_ends_at = start + timedelta(days=MANUAL_PRO_DAYS)
    return shop.subscription_ends_at


def sync_dodo_subscription(shop, data, status):
    shop.subscription_source = "dodo"
    shop.subscription_external_id = data.get("subscription_id")
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    shop.subscription_customer_id = customer.get("customer_id") or shop.subscription_customer_id
    shop.subscription_product_id = data.get("product_id") or shop.subscription_product_id
    shop.subscription_price_cents = int(data.get("recurring_pre_tax_amount"))
    if status == "active":
        next_billing = provider_datetime(data.get("next_billing_date"))
        if not next_billing or next_billing <= utcnow():
            raise ValueError("Dodo subscription has no valid future billing date")
        shop.plan_key = "pro"
        shop.subscription_status = "active"
        shop.subscription_source = "dodo"
        shop.subscription_ends_at = next_billing
        return 0
    return downgrade_to_free(shop, status=status)


def valid_dodo_subscription(data, plan, shop=None):
    """Verify the exact offer before changing entitlements."""
    if not plan:
        return False
    try:
        amount = int(data.get("recurring_pre_tax_amount"))
        trial_days = int(data.get("trial_period_days", 0))
    except (TypeError, ValueError):
        return False
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    try:
        expected_amount = int(metadata.get("expected_amount_cents", 0))
    except (TypeError, ValueError):
        expected_amount = 0
    known_subscription = bool(shop and shop.subscription_external_id == data.get("subscription_id"))
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    expected_product = shop.subscription_product_id if known_subscription and shop.subscription_product_id else metadata.get("expected_product_id")
    expected_price = shop.subscription_price_cents if known_subscription and shop.subscription_price_cents is not None else expected_amount
    return bool(
        plan
        and (known_subscription or (metadata.get("purpose") == "sulitshelf_subscription" and metadata.get("plan_key") == "pro"))
        and data.get("product_id") == expected_product
        and data.get("currency") == "PHP"
        and amount == expected_price
        and (known_subscription or expected_amount > 0)
        and trial_days == 0
        and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(data.get("subscription_id", "")))
        and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(customer.get("customer_id", "")))
        and 1 <= len(str(data.get("product_id", ""))) <= 120
    )


def dodo_endpoint():
    mode = current_app.config["DODO_PAYMENTS_ENVIRONMENT"]
    return "https://live.dodopayments.com" if mode == "live_mode" else "https://test.dodopayments.com"
