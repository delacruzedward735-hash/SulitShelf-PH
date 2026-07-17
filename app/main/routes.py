from datetime import timedelta
from io import BytesIO
from urllib.parse import urlparse

import qrcode
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import update

from app.extensions import db, limiter
from app.models import Campaign, ClickEvent, Product, ProductReport, Shop, User, utcnow
from app.services.billing import ensure_defaults
from app.services.catalog import DEPARTMENTS, detect_marketplace, php
from app.services.storage import media_url, path_for

bp = Blueprint("main", __name__)

TRACKING_SOURCES = {
    "campaign", "direct", "facebook", "instagram", "messenger", "qr", "shop",
    "shared", "tiktok", "youtube", "mall", "admin-picks", "sponsored", "trending",
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
    return db.select(Product).join(Shop).where(Product.status == "active")


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
    ensure_defaults()
    query = request.args.get("q", "").strip()[:100]
    department = request.args.get("department", "")
    marketplace = request.args.get("marketplace", "")
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
            .join(User, Shop.owner_id == User.id)
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
    )


@bp.get("/campaign/<slug>")
def campaign(slug):
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
    shop = db.session.scalar(db.select(Shop).where(Shop.slug == slug))
    if not shop:
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
    )


@bp.get("/product/<int:product_id>")
def product_detail(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.status != "active":
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
    product = db.session.get(Product, product_id)
    if not product or product.status != "active" or not detect_marketplace(product.affiliate_url):
        return redirect(url_for("main.home", link="unavailable"))
    try:
        db.session.add(
            ClickEvent(
                product=product,
                shop=product.shop,
                marketplace=product.marketplace,
                source=_source_from_request(),
            )
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


@bp.post("/products/<int:product_id>/report")
@limiter.limit("5 per hour")
def report_product(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.status != "active":
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
    product = db.session.get(Product, product_id)
    if not product or product.status != "active":
        abort(404)
    return _qr_response(
        url_for("main.product_detail", product_id=product.id, source="qr", _external=True),
        f"sulitshelf-product-{product.id}.png",
    )


@bp.get("/qr/shop/<slug>.png")
@limiter.limit("60 per minute")
def shop_qr(slug):
    shop = db.session.scalar(db.select(Shop).where(Shop.slug == slug))
    if not shop:
        abort(404)
    return _qr_response(
        url_for("main.public_shop", slug=shop.slug, source="qr", _external=True),
        f"sulitshelf-{shop.slug}.png",
    )


@bp.get("/media/product/<name>")
def product_image(name):
    remote_url = media_url(name)
    if remote_url:
        return redirect(remote_url, code=302)
    path = path_for("products", name)
    if not path:
        abort(404)
    return send_file(path, conditional=True, max_age=86400)
