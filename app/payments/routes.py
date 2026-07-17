import base64
import binascii
import hashlib
import hmac
import json
import time
from decimal import Decimal, InvalidOperation

import requests
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import csrf, db, limiter
from app.models import Donation, DonationTier, PlatformSettings, WebhookEvent
from app.services.billing import dodo_endpoint
from app.services.storage import UploadError, delete_file, media_url, path_for, save_image

bp = Blueprint("payments", __name__)


@bp.post("/dodo/donate/<tier_key>")
@login_required
@limiter.limit("10 per hour")
def dodo_donate(tier_key):
    tier = db.session.get(DonationTier, tier_key)
    if not tier or not tier.is_active:
        abort(404)
    if not current_app.config["DODO_PAYMENTS_API_KEY"] or not tier.dodo_product_id:
        flash("Dodo one-time donations are not configured for that amount yet.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))

    donation = Donation(shop=current_user.shop, tier=tier, method="dodo", amount_cents=tier.amount_cents, status="initiated")
    db.session.add(donation)
    db.session.commit()
    payload = {
        "product_cart": [{"product_id": tier.dodo_product_id, "quantity": 1}],
        "customer": {"email": current_user.email, "name": current_user.display_name},
        "return_url": f"{current_app.config['PUBLIC_BASE_URL']}{url_for('promoter.dashboard')}?tab=support&donation=returned",
        "metadata": {
            "purpose": "sulitshelf_donation",
            "donation_id": str(donation.id),
            "shop_id": str(current_user.shop.id),
            "tier": tier.key,
            "expected_amount_cents": str(tier.amount_cents),
        },
    }
    try:
        response = requests.post(
            f"{dodo_endpoint()}/checkouts",
            json=payload,
            headers={"Authorization": f"Bearer {current_app.config['DODO_PAYMENTS_API_KEY']}"},
            timeout=15,
        )
        response.raise_for_status()
        checkout_url = response.json().get("checkout_url", "")
        if not checkout_url.startswith("https://"):
            raise ValueError("Invalid checkout URL")
        return redirect(checkout_url)
    except (requests.RequestException, ValueError):
        donation.status = "failed"
        db.session.commit()
        current_app.logger.exception("Dodo donation checkout failed")
        flash("Unable to start the donation checkout right now.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))


@bp.post("/wallet/donate")
@bp.post("/gcash/submit")
@login_required
@limiter.limit("6 per hour")
def wallet_donate():
    tier_key = request.form.get("tier_key", "")
    tier = db.session.get(DonationTier, tier_key) if tier_key != "custom" else None
    settings = db.session.get(PlatformSettings, 1)
    reference = request.form.get("reference_number", "").strip().replace(" ", "").upper()
    if not settings or not (settings.gcash_number or settings.gcash_qr_name):
        flash("E-wallet donations are not configured yet.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    if tier_key == "custom":
        try:
            amount_cents = int(Decimal(request.form.get("custom_amount", "0")) * 100)
            if not 1000 <= amount_cents <= 10_000_000:
                raise ValueError
        except (InvalidOperation, ValueError):
            flash("Enter a custom donation between ₱10 and ₱100,000.", "error")
            return redirect(url_for("promoter.dashboard", tab="support"))
    elif not tier or not tier.is_active:
        flash("Choose a valid donation amount.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    else:
        amount_cents = tier.amount_cents
    if not 6 <= len(reference) <= 40 or not reference.replace("-", "").isalnum():
        flash("Enter the reference number shown on your e-wallet receipt.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    pending = db.session.scalar(db.select(Donation).where(Donation.shop_id == current_user.shop.id, Donation.method == "wallet", Donation.status == "pending"))
    if pending:
        flash("You already have an e-wallet donation waiting for review.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    receipt_name = None
    try:
        receipt_name = save_image(request.files.get("receipt"), "receipts", "donation", private=True)
        donation = Donation(
            shop=current_user.shop,
            tier=tier,
            method="wallet",
            amount_cents=amount_cents,
            reference_number=reference,
            receipt_name=receipt_name,
            status="pending",
        )
        db.session.add(donation)
        db.session.commit()
    except UploadError as error:
        flash(str(error), "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    except IntegrityError:
        db.session.rollback()
        delete_file("receipts", receipt_name)
        flash("That e-wallet reference number was already submitted.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    flash("Thank you! Your donation receipt was submitted for review.", "success")
    return redirect(url_for("promoter.dashboard", tab="support"))


def _decode(value):
    return base64.b64decode(value.replace("-", "+").replace("_", "/") + "=" * (-len(value) % 4))


def _valid_signature(raw):
    secret = current_app.config["DODO_PAYMENTS_WEBHOOK_KEY"]
    webhook_id = request.headers.get("webhook-id", "")
    timestamp = request.headers.get("webhook-timestamp", "")
    signatures = request.headers.get("webhook-signature", "")
    try:
        if not secret or not webhook_id or abs(time.time() - int(timestamp)) > 300:
            return False
        key = _decode(secret.removeprefix("whsec_"))
        expected = hmac.new(key, f"{webhook_id}.{timestamp}.".encode() + raw, hashlib.sha256).digest()
        return any(hmac.compare_digest(expected, _decode(item.split(",", 1)[-1])) for item in signatures.split())
    except (ValueError, TypeError, binascii.Error):
        return False


@bp.post("/dodo/webhook")
@csrf.exempt
@limiter.exempt
def dodo_webhook():
    raw = request.get_data(cache=False)
    if not _valid_signature(raw):
        return jsonify(error="invalid signature"), 401
    webhook_id = request.headers.get("webhook-id")
    if db.session.get(WebhookEvent, webhook_id):
        return jsonify(received=True, duplicate=True)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify(error="invalid json"), 400

    event_type = payload.get("type", "unknown")
    data = payload.get("data") or {}
    metadata = data.get("metadata") or {}
    donation = None
    if metadata.get("purpose") == "sulitshelf_donation":
        try:
            donation_id = int(metadata.get("donation_id", 0))
            shop_id = int(metadata.get("shop_id", 0))
        except (TypeError, ValueError):
            donation_id = shop_id = 0
        donation = db.session.scalar(db.select(Donation).where(Donation.id == donation_id).with_for_update())
        if donation and donation.shop_id != shop_id:
            donation = None
    if donation and event_type == "payment.succeeded":
        donation.status = "completed"
        donation.dodo_payment_id = data.get("payment_id") or data.get("id")
    elif donation and event_type in {"payment.failed", "payment.cancelled"}:
        donation.status = "failed"
    db.session.add(WebhookEvent(id=webhook_id, event_type=event_type))
    db.session.commit()
    return jsonify(received=True)


@bp.get("/wallet/qr")
@bp.get("/gcash/qr")
def wallet_qr():
    settings = db.session.get(PlatformSettings, 1)
    remote_url = media_url(settings.gcash_qr_name if settings else "")
    if remote_url:
        return redirect(remote_url, code=302)
    path = path_for("settings", settings.gcash_qr_name if settings else "")
    if not path:
        abort(404)
    return send_file(path, conditional=True, max_age=300)
