import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from decimal import Decimal, InvalidOperation

import requests
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import csrf, db, limiter
from app.models import Donation, DonationTier, PaymentSubmission, Plan, PlatformSettings, Shop, WalletReference, WebhookEvent
from app.services.billing import (
    active_subscription,
    dodo_endpoint,
    provider_datetime,
    sync_dodo_subscription,
    valid_dodo_subscription,
)
from app.services.storage import UploadError, delete_file, media_url, path_for, save_image

bp = Blueprint("payments", __name__)


@bp.post("/dodo/subscribe")
@login_required
@limiter.limit("6 per hour")
def dodo_subscribe():
    plan = db.session.get(Plan, "pro")
    shop = current_user.shop
    if active_subscription(shop):
        flash("Your unlimited access is already active. Manage the current plan before starting another checkout.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    if shop.subscription_source == "dodo" and shop.subscription_status == "on_hold":
        flash("Your Dodo subscription is on hold. Use the billing portal to update its payment method.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    if not plan or not plan.is_active or not current_app.config["DODO_PAYMENTS_API_KEY"] or not plan.dodo_product_id:
        flash("Automatic Pro billing is not configured yet. You can use the manual e-wallet option instead.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))

    payload = {
        "product_cart": [{"product_id": plan.dodo_product_id, "quantity": 1}],
        "customer": {"email": current_user.email, "name": current_user.display_name},
        "return_url": f"{current_app.config['PUBLIC_BASE_URL']}{url_for('promoter.dashboard')}?tab=billing&checkout=returned",
        "metadata": {
            "purpose": "sulitshelf_subscription",
            "shop_id": str(shop.id),
            "plan_key": "pro",
            "expected_amount_cents": str(plan.price_cents),
            "expected_product_id": plan.dodo_product_id,
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
        current_app.logger.exception("Dodo subscription checkout failed")
        flash("Unable to start the Pro checkout right now. No charge was created.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))


@bp.post("/dodo/customer-portal")
@login_required
@limiter.limit("10 per hour")
def dodo_customer_portal():
    shop = current_user.shop
    if (
        not current_app.config["DODO_PAYMENTS_API_KEY"]
        or not shop.subscription_customer_id
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", shop.subscription_customer_id)
    ):
        flash("The Dodo billing portal is not available for this account yet.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    try:
        response = requests.post(
            f"{dodo_endpoint()}/customers/{shop.subscription_customer_id}/customer-portal/session",
            headers={"Authorization": f"Bearer {current_app.config['DODO_PAYMENTS_API_KEY']}"},
            timeout=15,
        )
        response.raise_for_status()
        portal_url = response.json().get("link", "")
        if not portal_url.startswith("https://"):
            raise ValueError("Invalid portal URL")
        return redirect(portal_url)
    except (requests.RequestException, ValueError):
        current_app.logger.exception("Dodo customer portal failed")
        flash("Unable to open the billing portal right now. Please try again.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))


@bp.post("/wallet/subscribe")
@login_required
@limiter.limit("6 per hour")
def wallet_subscribe():
    plan = db.session.get(Plan, "pro")
    settings = db.session.get(PlatformSettings, 1)
    shop = current_user.shop
    reference = request.form.get("reference_number", "").strip().replace(" ", "").upper()
    if not plan or not plan.is_active:
        flash("Pro subscriptions are temporarily unavailable.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    if shop.owner.is_admin:
        flash("Administrator shops already have unlimited listings.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    if shop.subscription_source == "dodo" and shop.subscription_status in {"active", "on_hold"}:
        flash("Your automatic Dodo subscription is active. Use Manage automatic billing instead.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    if not settings or not (settings.gcash_number or settings.gcash_qr_name):
        flash("Manual e-wallet renewal is not configured yet.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    if not 6 <= len(reference) <= 40 or not reference.replace("-", "").isalnum():
        flash("Enter the reference number shown on your e-wallet receipt.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    pending = db.session.scalar(
        db.select(PaymentSubmission).where(PaymentSubmission.shop_id == shop.id, PaymentSubmission.status == "pending")
    )
    if pending:
        flash("You already have a Pro payment waiting for administrator review.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))

    receipt_name = None
    try:
        receipt_name = save_image(request.files.get("receipt"), "receipts", "subscription", private=True)
        submission = PaymentSubmission(
            shop=shop,
            plan_key="pro",
            amount_cents=plan.price_cents,
            reference_number=reference,
            receipt_name=receipt_name,
            status="pending",
        )
        db.session.add_all([WalletReference(reference=reference, purpose="subscription"), submission])
        db.session.commit()
    except UploadError as error:
        flash(str(error), "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    except IntegrityError:
        db.session.rollback()
        delete_file("receipts", receipt_name)
        flash("That e-wallet reference number was already submitted.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    except SQLAlchemyError:
        db.session.rollback()
        delete_file("receipts", receipt_name)
        current_app.logger.exception("Subscription receipt database write failed")
        flash("The receipt could not be saved. Please try again.", "error")
        return redirect(url_for("promoter.dashboard", tab="billing"))
    flash("Your Pro payment was submitted for administrator review.", "success")
    return redirect(url_for("promoter.dashboard", tab="billing"))


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
        db.session.add_all([WalletReference(reference=reference, purpose="donation"), donation])
        db.session.commit()
    except UploadError as error:
        flash(str(error), "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    except IntegrityError:
        db.session.rollback()
        delete_file("receipts", receipt_name)
        flash("That e-wallet reference number was already submitted.", "error")
        return redirect(url_for("promoter.dashboard", tab="support"))
    except SQLAlchemyError:
        db.session.rollback()
        delete_file("receipts", receipt_name)
        current_app.logger.exception("E-wallet donation database write failed")
        flash("The receipt could not be saved. Please try again.", "error")
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
    if not webhook_id or len(webhook_id) > 160:
        return jsonify(error="invalid webhook id"), 400
    if db.session.get(WebhookEvent, webhook_id):
        return jsonify(received=True, duplicate=True)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify(error="invalid json"), 400

    event_type = str(payload.get("type", "unknown"))[:80]
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return jsonify(error="invalid event data"), 400
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    donation = None
    if metadata.get("purpose") == "sulitshelf_donation":
        try:
            donation_id = int(metadata.get("donation_id", 0))
            shop_id = int(metadata.get("shop_id", 0))
            expected_amount_cents = int(metadata.get("expected_amount_cents", 0))
        except (TypeError, ValueError):
            donation_id = shop_id = expected_amount_cents = 0
        donation = db.session.scalar(db.select(Donation).where(Donation.id == donation_id).with_for_update())
        if donation and (donation.shop_id != shop_id or donation.amount_cents != expected_amount_cents):
            donation = None
    if donation and event_type == "payment.succeeded":
        donation.status = "completed"
        donation.dodo_payment_id = data.get("payment_id") or data.get("id")
    elif donation and event_type in {"payment.failed", "payment.cancelled"}:
        donation.status = "failed"

    if event_type.startswith("subscription."):
        subscription_id = data.get("subscription_id")
        shop = db.session.scalar(db.select(Shop).where(Shop.subscription_external_id == subscription_id).with_for_update()) if subscription_id else None
        if not shop and metadata.get("purpose") == "sulitshelf_subscription" and metadata.get("plan_key") == "pro":
            try:
                shop_id = int(metadata.get("shop_id", 0))
            except (TypeError, ValueError):
                shop_id = 0
            shop = db.session.scalar(db.select(Shop).where(Shop.id == shop_id).with_for_update())
        plan = db.session.get(Plan, "pro")
        event_at = provider_datetime(payload.get("timestamp")) or provider_datetime(data.get("updated_at")) or provider_datetime(data.get("created_at"))
        stale_event = bool(shop and shop.subscription_event_at and event_at and event_at < provider_datetime(shop.subscription_event_at.isoformat()))
        conflicting_subscription = bool(
            shop
            and (
                (shop.subscription_source == "manual" and active_subscription(shop))
                or (
                    shop.subscription_external_id
                    and subscription_id
                    and shop.subscription_external_id != subscription_id
                    and shop.subscription_status in {"active", "on_hold"}
                )
            )
        )
        if shop and not stale_event and not conflicting_subscription and valid_dodo_subscription(data, plan, shop):
            if event_type in {"subscription.active", "subscription.renewed"}:
                sync_dodo_subscription(shop, data, "active")
            elif event_type == "subscription.updated":
                provider_status = data.get("status")
                if provider_status == "active":
                    sync_dodo_subscription(shop, data, "active")
                elif provider_status in {"on_hold", "cancelled", "failed", "expired"}:
                    sync_dodo_subscription(shop, data, provider_status)
            elif event_type in {"subscription.on_hold", "subscription.cancelled", "subscription.failed", "subscription.expired"}:
                sync_dodo_subscription(shop, data, event_type.removeprefix("subscription."))
            shop.subscription_event_at = event_at or shop.subscription_event_at
        elif stale_event:
            current_app.logger.info("Ignored stale Dodo subscription event=%s shop_id=%s", event_type, shop.id)
        elif conflicting_subscription:
            current_app.logger.error("Rejected conflicting Dodo subscription event=%s shop_id=%s", event_type, shop.id)
        elif shop:
            current_app.logger.error("Rejected Dodo subscription entitlement event=%s shop_id=%s", event_type, shop.id)
    db.session.add(WebhookEvent(id=webhook_id, event_type=event_type))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(received=True, duplicate=True)
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
