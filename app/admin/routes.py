import json
import re
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from app.extensions import db
from app.models import (
    AuditLog,
    Campaign,
    CampaignProduct,
    Donation,
    DonationTier,
    PlatformSettings,
    Product,
    ProductReport,
    Shop,
    User,
    utcnow,
)
from app.services.authz import admin_required
from app.services.billing import ensure_defaults
from app.services.catalog import slugify
from app.services.storage import UploadError, delete_file, media_url, path_for, save_image

bp = Blueprint("admin", __name__)


def audit(action, target_type, target_id, details=None):
    db.session.add(AuditLog(admin_email=current_user.email, action=action, target_type=target_type, target_id=str(target_id), details=json.dumps(details or {}, separators=(",", ":"))))


@bp.get("/")
@admin_required
def dashboard():
    ensure_defaults()
    tiers = list(db.session.scalars(db.select(DonationTier).order_by(DonationTier.amount_cents)))
    donations = list(db.session.scalars(db.select(Donation).order_by(Donation.submitted_at.desc()).limit(250)))
    promoters = list(db.session.scalars(db.select(User).where(User.role.in_(["promoter", "admin"])).order_by(User.created_at.desc()).limit(250)))
    campaigns = list(db.session.scalars(db.select(Campaign).order_by(Campaign.sort_order, Campaign.created_at)))
    products = list(db.session.scalars(db.select(Product).order_by(Product.created_at.desc()).limit(250)))
    reports = list(db.session.scalars(db.select(ProductReport).order_by(ProductReport.created_at.desc()).limit(250)))
    settings = db.session.get(PlatformSettings, 1)
    stats = {
        "promoters": db.session.scalar(db.select(db.func.count(User.id))) or 0,
        "listings": db.session.scalar(db.select(db.func.count(Product.id))) or 0,
        "clicks": db.session.scalar(db.select(db.func.coalesce(db.func.sum(Product.click_count), 0))) or 0,
        "pending": db.session.scalar(db.select(db.func.count(Donation.id)).where(Donation.status == "pending")) or 0,
        "donated_cents": db.session.scalar(db.select(db.func.coalesce(db.func.sum(Donation.amount_cents), 0)).where(Donation.status.in_(["approved", "completed"]))) or 0,
        "reports": db.session.scalar(db.select(db.func.count(ProductReport.id)).where(ProductReport.status == "pending")) or 0,
    }
    return render_template(
        "admin.html",
        tiers=tiers,
        donations=donations,
        promoters=promoters,
        campaigns=campaigns,
        products=products,
        reports=reports,
        settings=settings,
        stats=stats,
    )


def _campaign_product_ids(raw):
    values = []
    for item in re.split(r"[\s,]+", raw.strip()):
        if item.isdigit():
            values.append(int(item))
    return list(dict.fromkeys(values))[:100]


def _set_campaign_products(campaign, raw):
    product_ids = _campaign_product_ids(raw)
    products = list(db.session.scalars(db.select(Product).where(Product.id.in_(product_ids)))) if product_ids else []
    by_id = {product.id: product for product in products}
    campaign.product_links.clear()
    for position, product_id in enumerate(product_ids):
        if product_id in by_id:
            campaign.product_links.append(CampaignProduct(product=by_id[product_id], position=position))


@bp.post("/campaigns")
@admin_required
def create_campaign():
    title = request.form.get("title", "").strip()
    slug = slugify(request.form.get("slug", "") or title)
    eyebrow = request.form.get("eyebrow", "").strip() or "CURATED COLLECTION"
    description = request.form.get("description", "").strip()
    if not 3 <= len(title) <= 80 or not 3 <= len(slug) <= 64 or len(eyebrow) > 50 or not 12 <= len(description) <= 240:
        flash("Check the campaign title, URL, label, and description.", "error")
        return redirect(url_for("admin.dashboard", tab="campaigns"))
    if db.session.scalar(db.select(Campaign).where(Campaign.slug == slug)):
        flash("That campaign URL is already in use.", "error")
        return redirect(url_for("admin.dashboard", tab="campaigns"))
    campaign = Campaign(
        title=title,
        slug=slug,
        eyebrow=eyebrow,
        description=description,
        is_active=request.form.get("is_active") == "yes",
        sort_order=request.form.get("sort_order", "0", type=int),
    )
    db.session.add(campaign)
    _set_campaign_products(campaign, request.form.get("product_ids", ""))
    db.session.flush()
    audit("campaign.created", "campaign", campaign.id, {"slug": slug})
    db.session.commit()
    flash("Campaign collection created.", "success")
    return redirect(url_for("admin.dashboard", tab="campaigns"))


@bp.post("/campaigns/<int:campaign_id>")
@admin_required
def update_campaign(campaign_id):
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        abort(404)
    title = request.form.get("title", "").strip()
    slug = slugify(request.form.get("slug", "") or title)
    eyebrow = request.form.get("eyebrow", "").strip() or "CURATED COLLECTION"
    description = request.form.get("description", "").strip()
    conflict = db.session.scalar(db.select(Campaign).where(Campaign.slug == slug, Campaign.id != campaign.id))
    if conflict or not 3 <= len(title) <= 80 or not 3 <= len(slug) <= 64 or len(eyebrow) > 50 or not 12 <= len(description) <= 240:
        flash("Check the campaign fields; its URL must be unique.", "error")
        return redirect(url_for("admin.dashboard", tab="campaigns"))
    campaign.title = title
    campaign.slug = slug
    campaign.eyebrow = eyebrow
    campaign.description = description
    campaign.is_active = request.form.get("is_active") == "yes"
    campaign.sort_order = request.form.get("sort_order", "0", type=int)
    _set_campaign_products(campaign, request.form.get("product_ids", ""))
    audit("campaign.updated", "campaign", campaign.id, {"slug": slug, "active": campaign.is_active})
    db.session.commit()
    flash("Campaign collection updated.", "success")
    return redirect(url_for("admin.dashboard", tab="campaigns"))


@bp.post("/campaigns/<int:campaign_id>/delete")
@admin_required
def delete_campaign(campaign_id):
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign:
        abort(404)
    audit("campaign.deleted", "campaign", campaign.id, {"slug": campaign.slug})
    db.session.delete(campaign)
    db.session.commit()
    flash("Campaign collection deleted.", "success")
    return redirect(url_for("admin.dashboard", tab="campaigns"))


@bp.post("/shops/<int:shop_id>/verify")
@admin_required
def toggle_shop_verification(shop_id):
    shop = db.session.get(Shop, shop_id)
    if not shop:
        abort(404)
    shop.is_verified = not shop.is_verified
    audit("shop.verification_toggled", "shop", shop.id, {"verified": shop.is_verified})
    db.session.commit()
    flash("Promoter verification updated.", "success")
    return redirect(url_for("admin.dashboard", tab="promoters"))


@bp.post("/products/<int:product_id>/sponsored")
@admin_required
def toggle_sponsored_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    product.is_sponsored = not product.is_sponsored
    audit("product.sponsored_toggled", "product", product.id, {"sponsored": product.is_sponsored})
    db.session.commit()
    flash("Sponsored placement updated. It will always be labeled publicly.", "success")
    return redirect(url_for("admin.dashboard", tab="campaigns"))


@bp.post("/reports/<int:report_id>/<decision>")
@admin_required
def review_report(report_id, decision):
    if decision not in {"dismiss", "pause"}:
        abort(400)
    report = db.session.scalar(db.select(ProductReport).where(ProductReport.id == report_id).with_for_update())
    if not report:
        abort(404)
    if report.status != "pending":
        flash("That report has already been reviewed.", "error")
        return redirect(url_for("admin.dashboard", tab="reports"))
    report.status = "resolved" if decision == "pause" else "dismissed"
    report.reviewed_at = utcnow()
    report.reviewed_by = current_user.email
    if decision == "pause":
        report.product.status = "paused"
    audit(f"product_report.{decision}", "product_report", report.id, {"product_id": report.product_id})
    db.session.commit()
    flash("Report reviewed and product paused." if decision == "pause" else "Report dismissed.", "success")
    return redirect(url_for("admin.dashboard", tab="reports"))


@bp.post("/donation-tiers/<tier_key>")
@admin_required
def update_donation_tier(tier_key):
    tier = db.session.get(DonationTier, tier_key)
    if not tier:
        abort(404)
    try:
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        amount_cents = int(Decimal(request.form.get("amount", "0")) * 100)
        dodo_product_id = request.form.get("dodo_product_id", "").strip()
        if not 3 <= len(name) <= 50 or not 8 <= len(description) <= 160 or not 1000 <= amount_cents <= 10_000_000 or len(dodo_product_id) > 120:
            raise ValueError
    except (InvalidOperation, ValueError):
        flash("Check the donation name, description, amount, and Dodo product ID.", "error")
        return redirect(url_for("admin.dashboard", tab="tiers"))
    tier.name = name
    tier.description = description
    tier.amount_cents = amount_cents
    tier.dodo_product_id = dodo_product_id or None
    tier.is_active = request.form.get("is_active") == "yes"
    tier.updated_by = current_user.email
    audit("donation_tier.updated", "donation_tier", tier.key, {"amount_cents": amount_cents, "dodo_product_id": bool(dodo_product_id), "active": tier.is_active})
    db.session.commit()
    flash(f"{tier.name} updated.", "success")
    return redirect(url_for("admin.dashboard", tab="tiers"))


@bp.post("/settings/donations")
@bp.post("/settings/gcash")
@admin_required
def update_donation_settings():
    settings = db.session.get(PlatformSettings, 1)
    provider = request.form.get("wallet_provider", "").strip()
    account_name = request.form.get("account_name", "").strip()
    number = request.form.get("number", "").strip().replace(" ", "")
    message = request.form.get("donation_message", "").strip()
    if not 2 <= len(provider) <= 40 or len(account_name) > 80 or len(number) > 30 or len(message) > 240:
        flash("Check the e-wallet provider, account details, and support message.", "error")
        return redirect(url_for("admin.dashboard", tab="wallet"))
    old_qr = settings.gcash_qr_name
    new_qr = None
    if request.files.get("qr") and request.files["qr"].filename:
        try:
            new_qr = save_image(request.files["qr"], "settings", "donation-wallet-qr")
        except UploadError as error:
            flash(str(error), "error")
            return redirect(url_for("admin.dashboard", tab="wallet"))
    settings.wallet_provider = provider
    settings.gcash_account_name = account_name
    settings.gcash_number = number
    settings.donation_message = message or "Your support helps keep SulitShelf free and open source."
    if new_qr:
        settings.gcash_qr_name = new_qr
    settings.updated_by = current_user.email
    audit("donation_wallet.updated", "settings", 1, {"provider": provider, "account_configured": bool(number), "qr_updated": bool(new_qr)})
    db.session.commit()
    if new_qr:
        delete_file("settings", old_qr)
    flash("Donation e-wallet settings updated.", "success")
    return redirect(url_for("admin.dashboard", tab="wallet"))


@bp.post("/donations/<int:donation_id>/<decision>")
@admin_required
def review_donation(donation_id, decision):
    if decision not in {"approved", "rejected"}:
        abort(400)
    donation = db.session.scalar(db.select(Donation).where(Donation.id == donation_id).with_for_update())
    if not donation:
        abort(404)
    if donation.method != "wallet" or donation.status != "pending":
        flash("That donation cannot be reviewed or was already processed.", "error")
        return redirect(url_for("admin.dashboard", tab="donations"))
    donation.status = decision
    donation.reviewed_at = utcnow()
    donation.reviewed_by = current_user.email
    donation.review_note = request.form.get("note", "").strip()[:300] or None
    audit(f"donation.{decision}", "donation", donation.id, {"shop_id": donation.shop_id, "method": donation.method, "amount_cents": donation.amount_cents})
    db.session.commit()
    flash("Donation confirmed. Thank you to the supporter!" if decision == "approved" else "Donation submission rejected.", "success")
    return redirect(url_for("admin.dashboard", tab="donations"))


@bp.get("/donations/<int:donation_id>/receipt")
@admin_required
def donation_receipt(donation_id):
    donation = db.session.get(Donation, donation_id)
    remote_url = media_url(donation.receipt_name if donation else "", private=True)
    if remote_url:
        response = redirect(remote_url, code=302)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    path = path_for("receipts", donation.receipt_name if donation else "")
    if not path:
        abort(404)
    response = send_file(path, conditional=True, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/users/<int:user_id>/toggle")
@admin_required
def toggle_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.id == current_user.id:
        abort(400)
    user.is_active_account = not user.is_active_account
    audit("user.toggled", "user", user.id, {"active": user.is_active_account})
    db.session.commit()
    flash("Promoter account status updated.", "success")
    return redirect(url_for("admin.dashboard", tab="promoters"))
