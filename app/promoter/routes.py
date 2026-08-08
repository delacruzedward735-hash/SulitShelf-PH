import hashlib
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db, limiter
from app.models import (
    ClickEvent,
    CommissionEntry,
    CommissionImport,
    CRMMessage,
    Donation,
    DonationTier,
    PaymentSubmission,
    Plan,
    PlatformSettings,
    Product,
    ProductMetricHourly,
    Shop,
    utcnow,
)
from app.services.billing import active_subscription, can_activate, can_publish, capacity, sync_expired_subscription
from app.services.catalog import DEPARTMENTS, detect_marketplace, slugify
from app.services.growth import (
    BRANDING_THEMES,
    BULK_PRODUCT_TEMPLATE,
    BUILTIN_PRODUCT_IMAGE,
    COMMISSION_TEMPLATE,
    CSVImportError,
    parse_bulk_products,
    parse_commissions,
    scan_product_health,
)
from app.services.storage import UploadError, delete_file, save_image
from app.services.two_factor import remaining_recovery_codes

bp = Blueprint("promoter", __name__)


@bp.before_request
@login_required
def require_login():
    pass


@bp.get("/")
def dashboard():
    subscription_before = (current_user.shop.plan_key, current_user.shop.subscription_status)
    sync_expired_subscription(current_user.shop)
    if subscription_before != (current_user.shop.plan_key, current_user.shop.subscription_status):
        db.session.commit()
    is_pro = active_subscription(current_user.shop)
    products = list(db.session.scalars(db.select(Product).where(Product.shop_id == current_user.shop.id).order_by(Product.created_at.desc())))
    tiers = list(db.session.scalars(db.select(DonationTier).where(DonationTier.is_active.is_(True)).order_by(DonationTier.amount_cents)))
    donations = list(db.session.scalars(db.select(Donation).where(Donation.shop_id == current_user.shop.id).order_by(Donation.submitted_at.desc()).limit(20)))
    payment_submissions = list(db.session.scalars(db.select(PaymentSubmission).where(PaymentSubmission.shop_id == current_user.shop.id).order_by(PaymentSubmission.submitted_at.desc()).limit(20)))
    pro_plan = db.session.get(Plan, "pro")
    plan_capacity = capacity(current_user.shop)
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
    metric_since = utcnow() - timedelta(days=30)
    metric_summary = db.session.execute(
        db.select(
            db.func.coalesce(db.func.sum(ProductMetricHourly.impressions), 0),
            db.func.coalesce(db.func.sum(ProductMetricHourly.clicks), 0),
        ).where(
            ProductMetricHourly.shop_id == current_user.shop.id,
            ProductMetricHourly.period_start >= metric_since,
        )
    ).one()
    metric_impressions, metric_clicks = int(metric_summary[0]), int(metric_summary[1])
    metric_ctr = round(metric_clicks * 100 / metric_impressions, 1) if metric_impressions else 0
    device_rows = db.session.execute(
        db.select(
            ProductMetricHourly.device,
            db.func.sum(ProductMetricHourly.impressions),
            db.func.sum(ProductMetricHourly.clicks),
        )
        .where(
            ProductMetricHourly.shop_id == current_user.shop.id,
            ProductMetricHourly.period_start >= metric_since,
        )
        .group_by(ProductMetricHourly.device)
        .order_by(db.func.sum(ProductMetricHourly.clicks).desc())
    ).all()
    commission_since = date.today() - timedelta(days=90)
    commission_summary = db.session.execute(
        db.select(
            db.func.coalesce(db.func.sum(CommissionEntry.order_value_cents), 0),
            db.func.coalesce(db.func.sum(CommissionEntry.commission_cents), 0),
            db.func.count(CommissionEntry.id),
        ).where(
            CommissionEntry.shop_id == current_user.shop.id,
            CommissionEntry.occurred_on >= commission_since,
            CommissionEntry.status == "approved",
        )
    ).one()
    recent_commission_imports = list(
        db.session.scalars(
            db.select(CommissionImport)
            .where(CommissionImport.shop_id == current_user.shop.id)
            .order_by(CommissionImport.created_at.desc())
            .limit(10)
        )
    )
    crm_messages = list(
        db.session.scalars(
            db.select(CRMMessage)
            .where(CRMMessage.recipient_id == current_user.id)
            .order_by(CRMMessage.created_at.desc())
            .limit(100)
        )
    )
    crm_unread_count = db.session.scalar(
        db.select(db.func.count(CRMMessage.id)).where(
            CRMMessage.recipient_id == current_user.id,
            CRMMessage.read_at.is_(None),
        )
    ) or 0
    health_counts = {"healthy": 0, "needs_attention": 0, "unchecked": 0}
    for product in products:
        health_counts[product.health_status if product.health_status in health_counts else "unchecked"] += 1
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
        payment_submissions=payment_submissions,
        pro_plan=pro_plan,
        is_pro=is_pro,
        plan_capacity=plan_capacity,
        departments=DEPARTMENTS,
        settings=settings,
        clicks_7d=clicks_7d,
        source_rows=source_rows,
        marketplace_rows=marketplace_rows,
        top_products=top_products,
        onboarding=onboarding,
        onboarding_done=sum(1 for _, complete, _ in onboarding if complete),
        metric_impressions=metric_impressions,
        metric_clicks=metric_clicks,
        metric_ctr=metric_ctr,
        device_rows=device_rows,
        commission_order_cents=int(commission_summary[0]),
        commission_cents=int(commission_summary[1]),
        commission_rows=int(commission_summary[2]),
        recent_commission_imports=recent_commission_imports,
        crm_messages=crm_messages,
        crm_unread_count=crm_unread_count,
        health_counts=health_counts,
        branding_themes=sorted(BRANDING_THEMES),
        two_factor_recovery_remaining=(
            remaining_recovery_codes(current_user.id) if current_user.two_factor_enabled else 0
        ),
    )


@bp.post("/messages/<int:message_id>/read")
def read_crm_message(message_id):
    message = db.session.scalar(
        db.select(CRMMessage).where(
            CRMMessage.id == message_id,
            CRMMessage.recipient_id == current_user.id,
        )
    )
    if not message:
        abort(404)
    if message.read_at is None:
        message.read_at = utcnow()
        db.session.commit()
    return redirect(url_for("promoter.dashboard", tab="inbox"))


@bp.post("/messages/read-all")
def read_all_crm_messages():
    result = db.session.execute(
        update(CRMMessage)
        .where(
            CRMMessage.recipient_id == current_user.id,
            CRMMessage.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    if result.rowcount:
        db.session.commit()
    flash("All inbox messages marked as read.", "success")
    return redirect(url_for("promoter.dashboard", tab="inbox"))


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
    shop = current_user.shop
    pro_branding = active_subscription(shop)
    new_logo = None
    new_banner = None
    old_logo = shop.branding_logo_name
    old_banner = shop.branding_banner_name
    if pro_branding:
        theme = request.form.get("branding_theme", "orange")
        tagline = request.form.get("branding_tagline", "").strip()
        if theme not in BRANDING_THEMES or len(tagline) > 120:
            flash("Check the Pro branding theme and tagline.", "error")
            return redirect(url_for("promoter.dashboard", tab="settings"))
        try:
            if request.files.get("branding_logo") and request.files["branding_logo"].filename:
                new_logo = save_image(request.files["branding_logo"], "branding", "shop-logo")
            if request.files.get("branding_banner") and request.files["branding_banner"].filename:
                new_banner = save_image(request.files["branding_banner"], "branding", "shop-banner")
        except UploadError as error:
            delete_file("branding", new_logo)
            flash(str(error), "error")
            return redirect(url_for("promoter.dashboard", tab="settings"))
        shop.branding_theme = theme
        shop.branding_tagline = tagline
        shop.hide_platform_branding = request.form.get("hide_platform_branding") == "yes"
        if new_logo:
            shop.branding_logo_name = new_logo
        elif request.form.get("remove_logo") == "yes":
            shop.branding_logo_name = None
        if new_banner:
            shop.branding_banner_name = new_banner
        elif request.form.get("remove_banner") == "yes":
            shop.branding_banner_name = None
    shop.name, shop.bio, shop.slug = name, bio, proposed_slug
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        delete_file("branding", new_logo)
        delete_file("branding", new_banner)
        current_app.logger.exception("Shop settings database write failed")
        flash("Shop settings could not be saved. Please try again.", "error")
        return redirect(url_for("promoter.dashboard", tab="settings"))
    if new_logo or (pro_branding and request.form.get("remove_logo") == "yes"):
        delete_file("branding", old_logo)
    if new_banner or (pro_branding and request.form.get("remove_banner") == "yes"):
        delete_file("branding", old_banner)
    flash("Shop settings updated.", "success")
    return redirect(url_for("promoter.dashboard", tab="settings"))


@bp.post("/products")
@limiter.limit(
    "60 per hour",
    key_func=lambda: f"product-create:{current_user.get_id()}",
    override_defaults=True,
)
def add_product():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    why_sulit = request.form.get("why_sulit", "").strip()
    best_for = request.form.get("best_for", "").strip()
    department = request.form.get("department", "")
    affiliate_url = request.form.get("affiliate_url", "").strip()
    marketplace = detect_marketplace(affiliate_url)
    validation_errors = []
    validation_codes = []
    if not 3 <= len(name) <= 100:
        validation_errors.append("Use a product name with 3–100 characters.")
        validation_codes.append("name")
    if not 12 <= len(description) <= 500:
        validation_errors.append("Write a description with 12–500 characters.")
        validation_codes.append("description")
    if not 8 <= len(why_sulit) <= 240:
        validation_errors.append("Explain why it is sulit using 8–240 characters.")
        validation_codes.append("why_sulit")
    if len(best_for) > 160:
        validation_errors.append("Keep ‘Best for’ within 160 characters.")
        validation_codes.append("best_for")
    if department not in DEPARTMENTS:
        validation_errors.append("Choose one of the listed departments.")
        validation_codes.append("department")
    if not marketplace:
        validation_errors.append("Use a direct HTTPS Shopee, Lazada, or TikTok Shop link.")
        validation_codes.append("affiliate_url")
    if request.form.get("rights_confirmed") != "yes":
        validation_errors.append("Confirm that the link and product image are approved for your use.")
        validation_codes.append("rights_confirmed")
    if validation_errors:
        current_app.logger.info(
            "product create rejected reason=validation user_id=%s fields=%s",
            current_user.id,
            ",".join(validation_codes),
        )
        flash("Product not published: " + " ".join(validation_errors), "error")
        return redirect(url_for("promoter.dashboard", tab="add"))

    try:
        price = Decimal(request.form.get("price", ""))
        price_in_cents = price * 100
        if (
            not price.is_finite()
            or not 0 <= price <= 1_000_000
            or price_in_cents != price_in_cents.to_integral_value()
        ):
            raise InvalidOperation
        price_cents = int(price_in_cents)
    except (InvalidOperation, ValueError):
        flash("Product not published: enter a price from ₱0.00 to ₱1,000,000.00 using no more than two decimal places.", "error")
        return redirect(url_for("promoter.dashboard", tab="add"))

    # Validate and store the remote image before taking a PostgreSQL row lock.
    # A slow Cloudinary response must never block other operations for the shop.
    try:
        image_name = save_image(request.files.get("image"), "products", "product")
    except UploadError as error:
        flash(f"Product not published: {error}", "error")
        return redirect(url_for("promoter.dashboard", tab="add"))

    try:
        shop = db.session.scalar(
            db.select(Shop).where(Shop.id == current_user.shop.id).with_for_update()
        )
        sync_expired_subscription(shop)
        if not can_publish(shop):
            db.session.commit()
            delete_file("products", image_name)
            flash("Your Free plan already has 50 products. Upgrade to Pro for unlimited uploads, or delete a product first.", "error")
            return redirect(url_for("promoter.dashboard", tab="billing"))

        existing = db.session.scalar(
            db.select(Product).where(
                Product.shop_id == shop.id,
                Product.affiliate_url == affiliate_url,
            ).order_by(Product.id.asc())
        )
        if existing:
            db.session.commit()
            delete_file("products", image_name)
            flash("That exact affiliate link is already on your shelf, so the existing product was kept.", "info")
            return redirect(url_for("promoter.dashboard", product_created="duplicate"))

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
            health_status="healthy",
            health_checked_at=utcnow(),
        )
        db.session.add(product)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        delete_file("products", image_name)
        current_app.logger.exception("Product database write failed")
        flash("The product could not be saved. Please try again.", "error")
        return redirect(url_for("promoter.dashboard", tab="add"))
    flash("Product published to the public mall.", "success")
    return redirect(url_for("promoter.dashboard", product_created="yes"))


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
    scan_product_health(current_user.shop)
    db.session.commit()
    flash("Price and last-checked date updated.", "success")
    return redirect(url_for("promoter.dashboard"))


def _require_growth_pro(tab="growth"):
    if active_subscription(current_user.shop):
        return None
    flash("This growth tool is included with Pro. Upgrade for ₱49/month to use it.", "error")
    return redirect(url_for("promoter.dashboard", tab=tab))


@bp.get("/products/import-template.csv")
def bulk_product_template():
    response = Response(BULK_PRODUCT_TEMPLATE, mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=sulitshelf-products-template.csv"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/products/bulk")
@limiter.limit("10 per hour")
def bulk_add_products():
    denial = _require_growth_pro()
    if denial:
        return denial
    if request.form.get("rights_confirmed") != "yes":
        flash("Confirm that every affiliate link and product image entry is approved for your use.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    try:
        rows, _filename = parse_bulk_products(request.files.get("csv"))
    except CSVImportError as error:
        flash(str(error), "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    shop = db.session.scalar(db.select(Shop).where(Shop.id == current_user.shop.id).with_for_update())
    sync_expired_subscription(shop)
    if not active_subscription(shop):
        db.session.commit()
        return _require_growth_pro()
    existing_urls = set(db.session.scalars(db.select(Product.affiliate_url).where(Product.shop_id == shop.id)))
    created = 0
    for row in rows:
        if row["affiliate_url"] in existing_urls:
            continue
        existing_urls.add(row["affiliate_url"])
        db.session.add(
            Product(
                shop=shop,
                image_name=BUILTIN_PRODUCT_IMAGE,
                price_checked_at=utcnow(),
                health_status="needs_attention",
                health_checked_at=utcnow(),
                health_note="Add a real product image",
                **row,
            )
        )
        created += 1
    if not created:
        db.session.rollback()
        flash("Every affiliate URL in this CSV already exists in your shop.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Bulk product import database write failed")
        flash("The products could not be imported. Check for duplicate links and try again.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    flash(f"Imported {created} products. Add real images from Product health before promoting them.", "success")
    return redirect(url_for("promoter.dashboard", tab="growth"))


@bp.get("/commissions/import-template.csv")
def commission_template():
    response = Response(COMMISSION_TEMPLATE, mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=sulitshelf-commissions-template.csv"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/commissions/import")
@limiter.limit("10 per hour")
def import_commissions():
    denial = _require_growth_pro()
    if denial:
        return denial
    marketplace = request.form.get("marketplace", "")
    shop = db.session.scalar(db.select(Shop).where(Shop.id == current_user.shop.id).with_for_update())
    sync_expired_subscription(shop)
    if not active_subscription(shop):
        db.session.commit()
        return _require_growth_pro()
    try:
        data, parsed, filename = parse_commissions(request.files.get("csv"), marketplace, shop)
    except CSVImportError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    content_hash = hashlib.sha256(data).hexdigest()
    if db.session.scalar(
        db.select(CommissionImport.id).where(
            CommissionImport.shop_id == shop.id,
            CommissionImport.marketplace == marketplace,
            CommissionImport.content_hash == content_hash,
        )
    ):
        db.session.rollback()
        flash("This exact commission file was already imported.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    row_hashes = [item["row_hash"] for item in parsed]
    existing_hashes = set(
        db.session.scalars(
            db.select(CommissionEntry.row_hash).where(
                CommissionEntry.shop_id == shop.id,
                CommissionEntry.row_hash.in_(row_hashes),
            )
        )
    )
    new_rows = [item for item in parsed if item["row_hash"] not in existing_hashes]
    if not new_rows:
        db.session.rollback()
        flash("All rows in this report were imported previously.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    commission_import = CommissionImport(
        shop=shop,
        marketplace=marketplace,
        original_filename=filename,
        content_hash=content_hash,
        row_count=len(new_rows),
        skipped_count=len(parsed) - len(new_rows),
        total_order_value_cents=sum(item["order_value_cents"] for item in new_rows if item["status"] == "approved"),
        total_commission_cents=sum(item["commission_cents"] for item in new_rows if item["status"] == "approved"),
        period_start=min(item["occurred_on"] for item in new_rows),
        period_end=max(item["occurred_on"] for item in new_rows),
    )
    db.session.add(commission_import)
    for item in new_rows:
        db.session.add(
            CommissionEntry(
                commission_import=commission_import,
                shop=shop,
                product=item["product"],
                marketplace=marketplace,
                row_hash=item["row_hash"],
                occurred_on=item["occurred_on"],
                product_name=item["product_name"],
                order_value_cents=item["order_value_cents"],
                commission_cents=item["commission_cents"],
                status=item["status"],
            )
        )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Commission import database write failed")
        flash("The report could not be imported. It may overlap with another recent import.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    flash(f"Imported {len(new_rows)} commission rows. Order references were stored only as hashes.", "success")
    return redirect(url_for("promoter.dashboard", tab="growth"))


@bp.post("/products/health-scan")
@limiter.limit("20 per hour")
def product_health_scan():
    summary = scan_product_health(current_user.shop)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Product health scan database write failed")
        flash("Product health could not be saved. Please try again.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    flash(
        f"Product health refreshed: {summary['healthy']} healthy, {summary['needs_attention']} need attention.",
        "success",
    )
    return redirect(url_for("promoter.dashboard", tab="growth"))


@bp.post("/products/<int:product_id>/image")
@limiter.limit("30 per hour")
def replace_product_image(product_id):
    product = db.session.scalar(
        db.select(Product).where(Product.id == product_id, Product.shop_id == current_user.shop.id)
    )
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    try:
        new_image = save_image(request.files.get("image"), "products", "product")
    except UploadError as error:
        flash(str(error), "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    old_image = product.image_name
    product.image_name = new_image
    scan_product_health(current_user.shop)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        delete_file("products", new_image)
        flash("The product image could not be updated.", "error")
        return redirect(url_for("promoter.dashboard", tab="growth"))
    if old_image != BUILTIN_PRODUCT_IMAGE:
        delete_file("products", old_image)
    flash("Product image updated.", "success")
    return redirect(url_for("promoter.dashboard", tab="growth"))


@bp.post("/products/<int:product_id>/status")
def product_status(product_id):
    shop = db.session.scalar(db.select(Shop).where(Shop.id == current_user.shop.id).with_for_update())
    sync_expired_subscription(shop)
    product = db.session.scalar(db.select(Product).where(Product.id == product_id, Product.shop_id == shop.id).with_for_update())
    if not product:
        flash("Product not found.", "error")
    elif product.status != "active" and not can_activate(shop):
        flash("Your Free plan already has 50 active products. Pause another listing or upgrade to Pro.", "error")
    else:
        product.status = "paused" if product.status == "active" else "active"
        flash("Product status updated.", "success")
    db.session.commit()
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
