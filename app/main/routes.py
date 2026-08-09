from datetime import timedelta
from io import BytesIO
from urllib.parse import urlparse

import qrcode
from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import update

from app.extensions import db, limiter
from app.models import Campaign, ClickEvent, Plan, Product, ProductReport, Shop, User, utcnow
from app.services.catalog import DEPARTMENTS, detect_marketplace, php
from app.services.billing import active_subscription, sync_all_expired_subscriptions
from app.services.growth import BUILTIN_PRODUCT_IMAGE, device_bucket, record_hourly_metric
from app.services.storage import media_url, path_for

bp = Blueprint("main", __name__)

TRACKING_SOURCES = {
    "campaign", "direct", "facebook", "instagram", "messenger", "qr", "shop",
    "shared", "tiktok", "youtube", "mall", "admin-picks", "sponsored", "trending", "pwa", "saved", "android",
}
REPORT_REASONS = {
    "broken_link": "Broken or unavailable link",
    "wrong_details": "Incorrect product details",
    "misleading": "Misleading listing",
    "unsafe": "Unsafe or prohibited product",
    "image_rights": "Possible image-rights issue",
    "other": "Other concern",
}


@bp.app_context_processor
def template_globals():
    return {"departments": DEPARTMENTS, "php": php, "report_reasons": REPORT_REASONS}


def _active_products():
    return (
        db.select(Product)
        .join(Shop, Product.shop_id == Shop.id)
        .join(User, Shop.owner_id == User.id)
        .where(Product.status == "active", User.is_active_account.is_(True))
    )


def _is_public_product(product):
    return bool(
        product
        and product.status == "active"
        and product.shop
        and product.shop.owner
        and product.shop.owner.is_active_account
    )


def _source_from_request():
    supplied = request.args.get("source", "").strip().lower()[:24]
    if supplied in TRACKING_SOURCES:
        return supplied
    try:
        host = (urlparse(request.referrer or "").hostname or "").lower()
    except ValueError:
        host = ""
    if "tiktok" in host:
        return "tiktok"
    if "facebook" in host or "fb.com" in host:
        return "facebook"
    if "instagram" in host:
        return "instagram"
    if "youtube" in host or "youtu.be" in host:
        return "youtube"
    return "direct"


@bp.get("/")
def home():
    sync_all_expired_subscriptions()
    pro_plan = db.session.get(Plan, "pro")
    query = request.args.get("q", "").strip()[:100]
    department = request.args.get("department", "")
    marketplace = request.args.get("marketplace", "")
    incoming_source = request.args.get("source", "").strip().lower()[:24]
    home_source = incoming_source if incoming_source in {"pwa", "shared", "qr"} else "mall"
    statement = _active_products()
    if query:
        statement = statement.where(Product.name.ilike(f"%{query}%"))
    if department in DEPARTMENTS:
        statement = statement.where(Product.department == department)
    if marketplace in {"shopee", "lazada", "tiktok"}:
        statement = statement.where(Product.marketplace == marketplace)
    products = list(db.session.scalars(statement.order_by(Product.created_at.desc()).limit(240)).unique())

    campaigns = list(
        db.session.scalars(
            db.select(Campaign)
            .where(Campaign.is_active.is_(True))
            .order_by(Campaign.sort_order, Campaign.created_at)
            .limit(12)
        )
    )
    trending_since = utcnow() - timedelta(days=7)
    trending = list(
        db.session.scalars(
            _active_products()
            .join(ClickEvent, ClickEvent.product_id == Product.id)
            .where(ClickEvent.occurred_at >= trending_since)
            .group_by(Product.id)
            .order_by(db.func.count(ClickEvent.id).desc(), Product.updated_at.desc())
            .limit(8)
        ).unique()
    )
    sponsored = list(
        db.session.scalars(
            _active_products()
            .where(Product.is_sponsored.is_(True))
            .order_by(Product.updated_at.desc())
            .limit(8)
        ).unique()
    )
    admin_picks = list(
        db.session.scalars(
            _active_products()
            .where(User.role == "admin")
            .order_by(Product.updated_at.desc())
            .limit(8)
        ).unique()
    )
    return render_template(
        "home.html",
        products=products,
        campaigns=campaigns,
        trending=trending,
        sponsored=sponsored,
        admin_picks=admin_picks,
        query=query,
        selected_department=department,
        selected_marketplace=marketplace,
        pro_plan=pro_plan,
        home_source=home_source,
    )


@bp.get("/favicon.ico")
def favicon():
    """Keep the conventional crawler/browser favicon URL stable."""
    return current_app.send_static_file("images/favicon.ico")


@bp.get("/campaign/<slug>")
def campaign(slug):
    sync_all_expired_subscriptions()
    campaign_item = db.session.scalar(
        db.select(Campaign).where(Campaign.slug == slug, Campaign.is_active.is_(True))
    )
    if not campaign_item:
        abort(404)
    return render_template("campaign.html", campaign=campaign_item, products=campaign_item.products)


@bp.get("/promoter-services")
def promoter_services():
    return render_template(
        "services.html",
        contact_email=current_app.config.get("SERVICE_CONTACT_EMAIL", ""),
    )


@bp.get("/shop/<slug>")
def public_shop(slug):
    sync_all_expired_subscriptions()
    shop = db.session.scalar(db.select(Shop).where(Shop.slug == slug))
    if not shop or not shop.owner or not shop.owner.is_active_account:
        abort(404)
    products = list(
        db.session.scalars(
            db.select(Product)
            .where(Product.shop_id == shop.id, Product.status == "active")
            .order_by(Product.created_at.desc())
        )
    )
    incoming_source = request.args.get("source", "").strip().lower()[:24]
    return render_template(
        "shop.html",
        shop=shop,
        products=products,
        shop_source=incoming_source if incoming_source in TRACKING_SOURCES else "shop",
        custom_branding=active_subscription(shop),
    )


@bp.get("/product/<int:product_id>")
def product_detail(product_id):
    sync_all_expired_subscriptions()
    product = db.session.get(Product, product_id)
    if not _is_public_product(product):
        abort(404)
    related = list(
        db.session.scalars(
            _active_products()
            .where(Product.department == product.department, Product.id != product.id)
            .order_by(Product.click_count.desc(), Product.created_at.desc())
            .limit(4)
        ).unique()
    )
    return render_template(
        "product.html",
        product=product,
        related=related,
        share_url=url_for("main.product_detail", product_id=product.id, source="shared", _external=True),
        detail_source=(
            request.args.get("source", "").strip().lower()[:24]
            if request.args.get("source", "").strip().lower()[:24] in TRACKING_SOURCES
            else "shared"
        ),
    )


@bp.get("/out/<int:product_id>")
@limiter.limit("120 per minute")
def outbound(product_id):
    sync_all_expired_subscriptions()
    product = db.session.get(Product, product_id)
    if not _is_public_product(product) or not detect_marketplace(product.affiliate_url):
        return redirect(url_for("main.home", link="unavailable"))
    try:
        source = _source_from_request()
        db.session.add(
            ClickEvent(
                product=product,
                shop=product.shop,
                marketplace=product.marketplace,
                source=source,
            )
        )
        record_hourly_metric(
            product,
            source,
            device_bucket(request.headers.get("User-Agent", "")),
            clicks=1,
        )
        db.session.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(click_count=Product.click_count + 1)
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Affiliate click tracking failed")
    response = redirect(product.affiliate_url, code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/events/impressions")
@limiter.limit("120 per minute")
def product_impressions():
    payload = request.get_json(silent=True) or {}
    supplied_ids = payload.get("product_ids")
    if not isinstance(supplied_ids, list):
        return {"recorded": 0}, 400
    product_ids = []
    for value in supplied_ids[:50]:
        try:
            product_id = int(value)
        except (TypeError, ValueError):
            continue
        if product_id > 0 and product_id not in product_ids:
            product_ids.append(product_id)
    if not product_ids:
        return {"recorded": 0}
    source = str(payload.get("source", "")).strip().lower()[:24]
    if source not in TRACKING_SOURCES:
        source = _source_from_request()
    products = list(
        db.session.scalars(
            _active_products().where(Product.id.in_(product_ids))
        )
    )
    device = device_bucket(request.headers.get("User-Agent", ""))
    try:
        for product in products:
            record_hourly_metric(product, source, device, impressions=1)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Product impression aggregation failed")
        return {"recorded": 0}, 503
    return {"recorded": len(products)}


@bp.get("/saved")
def saved_products():
    return render_template("saved.html")


@bp.get("/api/products")
@limiter.limit("120 per minute")
def product_api():
    product_ids = []
    for value in request.args.get("ids", "").split(",")[:30]:
        try:
            product_id = int(value)
        except ValueError:
            continue
        if product_id > 0 and product_id not in product_ids:
            product_ids.append(product_id)
    if not product_ids:
        return {"products": []}
    supplied_source = request.args.get("source", "").strip().lower()[:24]
    detail_source = supplied_source if supplied_source in TRACKING_SOURCES else "shared"
    products = list(
        db.session.scalars(
            _active_products().where(Product.id.in_(product_ids))
        )
    )
    by_id = {product.id: product for product in products}
    ordered = [by_id[item] for item in product_ids if item in by_id]
    return {
        "products": [
            {
                "id": product.id,
                "name": product.name,
                "marketplace": "TikTok Shop" if product.marketplace == "tiktok" else product.marketplace.title(),
                "department": product.department,
                "price": php(product.price_cents),
                "image_url": url_for("main.product_image", name=product.image_name),
                "detail_url": url_for("main.product_detail", product_id=product.id, source=detail_source),
                "shop_name": product.shop.name,
            }
            for product in ordered
        ]
    }


@bp.get("/offline")
def offline():
    return render_template("offline.html")


@bp.get("/service-worker.js")
@limiter.exempt
def service_worker():
    response = current_app.send_static_file("service-worker.js")
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@bp.get("/robots.txt")
@limiter.exempt
def robots():
    body = (
        "User-agent: *\nAllow: /\n"
        "Disallow: /admin/\nDisallow: /studio/\nDisallow: /oauth/\n"
        "Disallow: /payments/\nDisallow: /login\nDisallow: /register\nDisallow: /saved\n"
        f"Sitemap: {url_for('main.sitemap', _external=True)}\n"
    )
    return Response(body, mimetype="text/plain")


@bp.get("/sitemap.xml")
@limiter.exempt
def sitemap():
    products = list(db.session.scalars(_active_products().order_by(Product.updated_at.desc()).limit(45_000)).unique())
    shops = list(
        db.session.scalars(
            db.select(Shop)
            .join(Product, Product.shop_id == Shop.id)
            .join(User, Shop.owner_id == User.id)
            .where(Product.status == "active", User.is_active_account.is_(True))
            .distinct()
            .order_by(Shop.updated_at.desc())
            .limit(4_000)
        ).unique()
    )
    campaigns = list(db.session.scalars(db.select(Campaign).where(Campaign.is_active.is_(True)).order_by(Campaign.updated_at.desc()).limit(500)))
    response = Response(
        render_template("sitemap.xml", products=products, shops=shops, campaigns=campaigns),
        mimetype="application/xml",
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@bp.post("/products/<int:product_id>/report")
@limiter.limit("5 per hour")
def report_product(product_id):
    product = db.session.get(Product, product_id)
    if not _is_public_product(product):
        abort(404)
    reason = request.form.get("reason", "")
    details = request.form.get("details", "").strip()[:300]
    if reason not in REPORT_REASONS:
        flash("Choose a valid reason for the report.", "error")
    else:
        db.session.add(ProductReport(product=product, reason=reason, details=details))
        db.session.commit()
        flash("Thank you. The administrator will review this listing.", "success")
    return redirect(url_for("main.product_detail", product_id=product.id, _anchor="report"))


def _qr_response(target, filename):
    image = qrcode.make(target)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    response = send_file(output, mimetype="image/png", download_name=filename, max_age=3600)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@bp.get("/qr/product/<int:product_id>.png")
@limiter.limit("60 per minute")
def product_qr(product_id):
    sync_all_expired_subscriptions()
    product = db.session.get(Product, product_id)
    if not _is_public_product(product):
        abort(404)
    return _qr_response(
        url_for("main.product_detail", product_id=product.id, source="qr", _external=True),
        f"sulitshelf-product-{product.id}.png",
    )


@bp.get("/qr/shop/<slug>.png")
@limiter.limit("60 per minute")
def shop_qr(slug):
    shop = db.session.scalar(db.select(Shop).where(Shop.slug == slug))
    if not shop or not shop.owner or not shop.owner.is_active_account:
        abort(404)
    return _qr_response(
        url_for("main.public_shop", slug=shop.slug, source="qr", _external=True),
        f"sulitshelf-{shop.slug}.png",
    )


@bp.get("/media/product/<name>")
def product_image(name):
    if name == BUILTIN_PRODUCT_IMAGE:
        return redirect(url_for("static", filename="images/product-placeholder.svg"), code=302)
    remote_url = media_url(name)
    if remote_url:
        return redirect(remote_url, code=302)
    path = path_for("products", name)
    if not path:
        abort(404)
    return send_file(path, conditional=True, max_age=86400)


@bp.get("/media/branding/<name>")
def branding_image(name):
    remote_url = media_url(name)
    if remote_url:
        return redirect(remote_url, code=302)
    path = path_for("branding", name)
    if not path:
        abort(404)
    return send_file(path, conditional=True, max_age=86400)
