"""JSON REST API for native clients (the Android app).

Read endpoints are public (same data the public storefront already renders)
and are cache-friendly so the app and any CDN/OkHttp cache in front of it can
avoid refetching unchanged catalog pages. Write/auth endpoints reuse the same
password hashing, rate limiting, and click-tracking behavior as the web app.
"""
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request, url_for
from sqlalchemy import update
from sqlalchemy.orm import joinedload

from app.api.auth import issue_token, require_api_token
from app.extensions import db, limiter
from app.main.routes import _is_public_product, _source_from_request
from app.models import ApiToken, ClickEvent, Product, Shop, User, utcnow
from app.services.billing import active_subscription, sync_all_expired_subscriptions
from app.services.catalog import DEPARTMENTS, detect_marketplace, php
from app.services.growth import device_bucket, record_hourly_metric

bp = Blueprint("api", __name__)

MAX_PAGE_SIZE = 40
DEFAULT_PAGE_SIZE = 20


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _product_image_url(product):
    return url_for("main.product_image", name=product.image_name, _external=True)


def _shop_logo_url(shop):
    if not shop.branding_logo_name:
        return None
    return url_for("main.branding_image", name=shop.branding_logo_name, _external=True)


def serialize_shop_summary(shop):
    return {
        "id": shop.id,
        "slug": shop.slug,
        "name": shop.name,
        "bio": shop.bio,
        "is_verified": shop.is_verified,
        "branding_theme": shop.branding_theme,
        "branding_tagline": shop.branding_tagline,
        "logo_url": _shop_logo_url(shop),
        "hide_platform_branding": shop.hide_platform_branding,
    }


def serialize_product_summary(product):
    return {
        "id": product.id,
        "name": product.name,
        "department": product.department,
        "marketplace": product.marketplace,
        "marketplace_label": "TikTok Shop" if product.marketplace == "tiktok" else product.marketplace.title(),
        "price_cents": product.price_cents,
        "price_display": php(product.price_cents),
        "image_url": _product_image_url(product),
        "badge": product.badge,
        "is_sponsored": product.is_sponsored,
        "click_count": product.click_count,
        "shop_slug": product.shop.slug,
        "shop_name": product.shop.name,
    }


def serialize_product_detail(product):
    payload = serialize_product_summary(product)
    payload.update(
        {
            "description": product.description,
            "why_sulit": product.why_sulit,
            "best_for": product.best_for,
            "price_checked_at": product.price_checked_at.isoformat() if product.price_checked_at else None,
            "health_status": product.health_status,
            "shop": serialize_shop_summary(product.shop),
        }
    )
    return payload


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _pagination_args():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("page_size", DEFAULT_PAGE_SIZE))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    page_size = max(1, min(MAX_PAGE_SIZE, page_size))
    return page, page_size


def _cacheable(response, max_age=60):
    response.headers["Cache-Control"] = f"public, max-age={max_age}"
    return response


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@bp.get("/health")
@limiter.exempt
def health():
    """Cheap connectivity probe the Android app can call on cold start."""
    return jsonify(status="ok", server_time=datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@bp.post("/auth/login")
@limiter.limit("10 per 15 minutes")
def login():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    device_label = payload.get("device_label") or "Android device"

    user = db.session.scalar(db.select(User).where(User.email == email))
    if not user or not user.check_password(password) or not user.is_active_account:
        return jsonify(error="invalid_credentials", message="Incorrect email or password."), 401
    if user.two_factor_enabled:
        # The mobile client doesn't implement the 2FA challenge flow yet;
        # refuse cleanly rather than silently skipping a security control.
        return jsonify(
            error="two_factor_required",
            message="This account has two-factor authentication enabled. Sign in on the web to continue.",
        ), 403

    raw_token, record = issue_token(user, device_label=device_label)
    db.session.commit()
    return jsonify(
        token=raw_token,
        token_type="Bearer",
        expires_at=record.expires_at.isoformat(),
        user={"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role},
    )


@bp.post("/auth/logout")
@require_api_token
def logout():
    g.api_token.revoked_at = utcnow()
    db.session.commit()
    return jsonify(status="ok")


@bp.get("/me")
@require_api_token
def me():
    user = g.api_user
    sync_all_expired_subscriptions()
    shop = user.shop
    return jsonify(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        shop=serialize_shop_summary(shop) if shop else None,
    )


# ---------------------------------------------------------------------------
# Public catalog (mirrors the public storefront/product pages)
# ---------------------------------------------------------------------------

@bp.get("/departments")
@limiter.limit("120 per minute")
def departments():
    return _cacheable(jsonify(departments=DEPARTMENTS), max_age=3600)


@bp.get("/shops/<slug>")
@limiter.limit("120 per minute")
def shop_detail(slug):
    shop = db.session.scalar(
        db.select(Shop).options(joinedload(Shop.owner)).where(Shop.slug == slug)
    )
    if not shop or not shop.owner or not shop.owner.is_active_account:
        return jsonify(error="not_found"), 404
    return _cacheable(jsonify(**serialize_shop_summary(shop)))


@bp.get("/shops/<slug>/products")
@limiter.limit("120 per minute")
def shop_products(slug):
    shop = db.session.scalar(db.select(Shop).options(joinedload(Shop.owner)).where(Shop.slug == slug))
    if not shop or not shop.owner or not shop.owner.is_active_account:
        return jsonify(error="not_found"), 404

    page, page_size = _pagination_args()
    department = request.args.get("department") or None
    marketplace = request.args.get("marketplace") or None

    statement = (
        db.select(Product)
        .options(joinedload(Product.shop))
        .where(Product.shop_id == shop.id, Product.status == "active")
    )
    if department:
        statement = statement.where(Product.department == department)
    if marketplace:
        statement = statement.where(Product.marketplace == marketplace)
    statement = statement.order_by(Product.created_at.desc()).offset((page - 1) * page_size).limit(page_size + 1)

    rows = list(db.session.scalars(statement).unique())
    has_next = len(rows) > page_size
    items = [serialize_product_summary(p) for p in rows[:page_size]]
    return _cacheable(jsonify(items=items, page=page, page_size=page_size, has_next=has_next))


@bp.get("/products/<int:product_id>")
@limiter.limit("180 per minute")
def product_detail(product_id):
    product = db.session.scalar(
        db.select(Product).options(joinedload(Product.shop).joinedload(Shop.owner)).where(Product.id == product_id)
    )
    if not _is_public_product(product):
        return jsonify(error="not_found"), 404
    return _cacheable(jsonify(**serialize_product_detail(product)))


@bp.post("/products/<int:product_id>/click")
@limiter.limit("120 per minute")
def product_click(product_id):
    """Record an outbound-click event and hand back the affiliate URL.

    Unlike the web /out/<id> route (which 302-redirects the browser), the
    Android app opens the URL itself in a Chrome Custom Tab, so this returns
    JSON instead of issuing a redirect.
    """
    sync_all_expired_subscriptions()
    product = db.session.get(Product, product_id)
    if not _is_public_product(product) or not detect_marketplace(product.affiliate_url):
        return jsonify(error="unavailable"), 404

    try:
        source = _source_from_request() if request.args.get("source") else "android"
        db.session.add(
            ClickEvent(product=product, shop=product.shop, marketplace=product.marketplace, source=source)
        )
        record_hourly_metric(product, source, device_bucket("android-app"), clicks=1)
        db.session.execute(
            update(Product).where(Product.id == product.id).values(click_count=Product.click_count + 1)
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Mobile click tracking failed")

    return jsonify(affiliate_url=product.affiliate_url)


# ---------------------------------------------------------------------------
# Promoter dashboard (authenticated)
# ---------------------------------------------------------------------------

@bp.get("/promoter/dashboard")
@require_api_token
def promoter_dashboard():
    user = g.api_user
    shop = user.shop
    if not shop:
        return jsonify(error="no_shop"), 404

    sync_all_expired_subscriptions()
    product_count = db.session.scalar(
        db.select(db.func.count(Product.id)).where(Product.shop_id == shop.id, Product.status == "active")
    ) or 0
    total_clicks = db.session.scalar(
        db.select(db.func.count(ClickEvent.id)).where(ClickEvent.shop_id == shop.id)
    ) or 0
    subscription = active_subscription(shop)

    return jsonify(
        shop=serialize_shop_summary(shop),
        product_count=product_count,
        total_clicks=total_clicks,
        plan_key=shop.plan_key,
        subscription_status=shop.subscription_status,
        subscription_active=bool(subscription),
        subscription_ends_at=shop.subscription_ends_at.isoformat() if shop.subscription_ends_at else None,
    )


@bp.get("/promoter/tokens")
@require_api_token
def list_tokens():
    """Lets the app show 'signed in on: Pixel 8, iPad, ...' and revoke others."""
    tokens = db.session.scalars(
        db.select(ApiToken)
        .where(ApiToken.user_id == g.api_user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return jsonify(
        tokens=[
            {
                "id": t.id,
                "device_label": t.device_label,
                "created_at": t.created_at.isoformat(),
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                "expires_at": t.expires_at.isoformat(),
                "is_current": t.id == g.api_token.id,
            }
            for t in tokens
        ]
    )
