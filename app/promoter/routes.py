from datetime import timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ClickEvent, Donation, DonationTier, PlatformSettings, Product, Shop, utcnow
from app.services.billing import ensure_defaults
from app.services.catalog import DEPARTMENTS, detect_marketplace, slugify
from app.services.storage import UploadError, delete_file, save_image

bp = Blueprint("promoter", __name__)


@bp.before_request
@login_required
def require_login():
    pass


@bp.get("/")
def dashboard():
    ensure_defaults()
    products = list(db.session.scalars(db.select(Product).where(Product.shop_id == current_user.shop.id).order_by(Product.created_at.desc())))
    tiers = list(db.session.scalars(db.select(DonationTier).where(DonationTier.is_active.is_(True)).order_by(DonationTier.amount_cents)))
    donations = list(db.session.scalars(db.select(Donation).where(Donation.shop_id == current_user.shop.id).order_by(Donation.submitted_at.desc()).limit(20)))
    settings = db.session.get(PlatformSettings, 1)
    since = utcnow() - timedelta(days=7)
    clicks_7d = db.session.scalar(
        db.select(db.func.count(ClickEvent.id)).where(
            ClickEvent.shop_id == current_user.shop.id,
            ClickEvent.occurred_at >= since,
        )
    ) or 0
    source_rows = db.session.execute(
        db.select(ClickEvent.source, db.func.count(ClickEvent.id).label("total"))
        .where(ClickEvent.shop_id == current_user.shop.id, ClickEvent.occurred_at >= since)
        .group_by(ClickEvent.source)
        .order_by(db.desc("total"))
    ).all()
    marketplace_rows = db.session.execute(
        db.select(ClickEvent.marketplace, db.func.count(ClickEvent.id).label("total"))
        .where(ClickEvent.shop_id == current_user.shop.id, ClickEvent.occurred_at >= since)
        .group_by(ClickEvent.marketplace)
        .order_by(db.desc("total"))
    ).all()
    top_products = sorted(products, key=lambda item: item.click_count, reverse=True)[:5]
    onboarding = [
        ("Complete your shop profile", bool(current_user.shop.bio and current_user.shop.bio != "Curated finds worth checking out."), "settings"),
        ("Publish your first product", bool(products), "add"),
        ("Add a strong ‘Why it’s sulit’ reason", any(product.why_sulit for product in products), "overview"),
        ("Share your shop link or QR code", clicks_7d > 0, "growth"),
    ]
    return render_template(
        "studio.html",
        products=products,
        tiers=tiers,
        donations=donations,
        departments=DEPARTMENTS,
        settings=settings,
        clicks_7d=clicks_7d,
        source_rows=source_rows,
        marketplace_rows=marketplace_rows,
        top_products=top_products,
        onboarding=onboarding,
        onboarding_done=sum(1 for _, complete, _ in onboarding if complete),
    )


@bp.post("/shop")
def update_shop():
    name = request.form.get("name", "").strip()
    bio = request.form.get("bio", "").strip()
    proposed_slug = slugify(request.form.get("slug", "") or name)
    if not 3 <= len(name) <= 80 or len(bio) > 240 or len(proposed_slug) < 3:
        flash("Check the shop name, URL, and description.", "error")
        return redirect(url_for("promoter.dashboard", tab="settings"))
    conflict = db.session.scalar(db.select(Shop).where(Shop.slug == proposed_slug, Shop.id != current_user.shop.id))
    if conflict:
        flash("That shop URL is already taken.", "error")
        return redirect(url_for("promoter.dashboard", tab="settings"))
    current_user.shop.name, current_user.shop.bio, current_user.shop.slug = name, bio, proposed_slug
    db.session.commit()
    flash("Shop settings updated.", "success")
    return redirect(url_for("promoter.dashboard", tab="settings"))


@bp.post("/products")
def add_product():
    shop = current_user.shop
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    why_sulit = request.form.get("why_sulit", "").strip()
    best_for = request.form.get("best_for", "").strip()
    department = request.form.get("department", "")
    affiliate_url = request.form.get("affiliate_url", "").strip()
    marketplace = detect_marketplace(affiliate_url)
    if not 3 <= len(name) <= 100 or not 12 <= len(description) <= 500 or not 8 <= len(why_sulit) <= 240 or len(best_for) > 160 or department not in DEPARTMENTS or not marketplace or request.form.get("rights_confirmed") != "yes":
        flash("Check all product details and confirm your publishing rights.", "error")
        return redirect(url_for("promoter.dashboard", tab="add"))
    try:
        price_cents = int(Decimal(request.form.get("price", "0")) * 100)
        if not 0 <= price_cents <= 100_000_000:
            raise InvalidOperation
        image_name = save_image(request.files.get("image"), "products", "product")
    except (InvalidOperation, ValueError, UploadError) as error:
        flash(str(error) or "Enter a valid product price and image.", "error")
        return redirect(url_for("promoter.dashboard", tab="add"))
    product = Product(
        shop=shop,
        name=name,
        description=description,
        why_sulit=why_sulit,
        best_for=best_for,
        department=department,
        marketplace=marketplace,
        affiliate_url=affiliate_url,
        price_cents=price_cents,
        price_checked_at=utcnow(),
        image_name=image_name,
        badge=request.form.get("badge", "").strip()[:32] or None,
    )
    db.session.add(product)
    db.session.commit()
    flash("Product published to the public mall.", "success")
    return redirect(url_for("promoter.dashboard"))


@bp.post("/products/<int:product_id>/price-check")
def refresh_product_price(product_id):
    product = db.session.scalar(
        db.select(Product).where(Product.id == product_id, Product.shop_id == current_user.shop.id)
    )
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("promoter.dashboard"))
    try:
        price_cents = int(Decimal(request.form.get("price", "0")) * 100)
        if not 0 <= price_cents <= 100_000_000:
            raise ValueError
    except (InvalidOperation, ValueError):
        flash("Enter a valid current price.", "error")
        return redirect(url_for("promoter.dashboard"))
    product.price_cents = price_cents
    product.price_checked_at = utcnow()
    db.session.commit()
    flash("Price and last-checked date updated.", "success")
    return redirect(url_for("promoter.dashboard"))


@bp.post("/products/<int:product_id>/status")
def product_status(product_id):
    product = db.session.scalar(db.select(Product).where(Product.id == product_id, Product.shop_id == current_user.shop.id))
    if not product:
        flash("Product not found.", "error")
    else:
        product.status = "paused" if product.status == "active" else "active"
        db.session.commit()
        flash("Product status updated.", "success")
    return redirect(url_for("promoter.dashboard"))


@bp.post("/products/<int:product_id>/delete")
def delete_product(product_id):
    product = db.session.scalar(db.select(Product).where(Product.id == product_id, Product.shop_id == current_user.shop.id))
    if product:
        image_name = product.image_name
        db.session.delete(product)
        db.session.commit()
        delete_file("products", image_name)
        flash("Product deleted.", "success")
    return redirect(url_for("promoter.dashboard"))
