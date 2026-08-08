import base64
import hashlib
import hmac
import json
import re
import time
from datetime import date, timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import cloudinary.uploader
import pytest
from PIL import Image

from app.extensions import db
from app.models import (
    AuditLog,
    Campaign,
    CampaignProduct,
    ClickEvent,
    CommissionEntry,
    CommissionImport,
    CRMMessage,
    Donation,
    OAuthIdentity,
    PaymentSubmission,
    PasswordResetToken,
    Plan,
    PlatformSettings,
    Product,
    ProductMetricHourly,
    ProductReport,
    Shop,
    TwoFactorRecoveryCode,
    User,
    WalletReference,
    utcnow,
)
from app.services.catalog import detect_marketplace
from app.services.billing import aware
from app.services.storage import UploadError, media_url, path_for, save_image
from app.services import email as email_service
from app.services.growth import BUILTIN_PRODUCT_IMAGE
from app.services.two_factor import decrypt_secret, encrypt_secret, generate_secret, replace_recovery_codes, totp_code


def register(client, email="promoter@example.com", password="very-secure-password"):
    return client.post(
        "/register",
        data={"email": email, "display_name": "Test Promoter", "password": password},
        follow_redirects=True,
    )


def login(client, email="promoter@example.com", password="very-secure-password"):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=True)


def enable_two_factor_for_user(app, email="promoter@example.com"):
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == email))
        secret = generate_secret()
        user.two_factor_secret_ciphertext = encrypt_secret(secret)
        user.two_factor_enabled_at = utcnow()
        user.two_factor_last_counter = None
        recovery_codes = replace_recovery_codes(user)
        db.session.commit()
        return secret, recovery_codes


def png_upload(name="receipt.png", size=(32, 32)):
    output = BytesIO()
    Image.new("RGB", size, "#ee4d2d").save(output, format="PNG")
    output.seek(0)
    output.filename = name
    return output, name


def signed_dodo_post(client, app, webhook_id, payload, signing_key=b"test-dodo-subscription-key"):
    app.config["DODO_PAYMENTS_WEBHOOK_KEY"] = "whsec_" + base64.urlsafe_b64encode(signing_key).decode().rstrip("=")
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = base64.b64encode(
        hmac.new(signing_key, f"{webhook_id}.{timestamp}.".encode() + raw, hashlib.sha256).digest()
    ).decode()
    return client.post(
        "/payments/dodo/webhook",
        data=raw,
        content_type="application/json",
        headers={
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": f"v1,{signature}",
        },
    )


def test_home_and_registration_are_free(client, app):
    response = client.get("/")
    assert response.status_code == 200
    assert b"50 products" in response.data
    assert b"49" in response.data

    response = register(client)
    assert response.status_code == 200
    assert b"Promoter Studio" in response.data
    assert b"Free" in response.data
    assert b"50 products" in response.data
    assert b"sidebar-account-signout" in response.data
    assert b"Sign out of SulitShelf" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        assert user.shop.plan_key == "free"
        assert user.shop.subscription_status == "free"


def test_auth_pages_use_polished_access_layout_and_hide_unconfigured_oauth(client, app):
    register_page = client.get("/register")
    login_page = client.get("/login")
    assert register_page.status_code == 200
    assert login_page.status_code == 200
    assert b"auth-access-frame" in register_page.data
    assert b"Turn your links into a shelf people remember" in register_page.data
    assert b"50 product slots" in register_page.data
    assert b"data-password-strength-input" in register_page.data
    assert b"setup needed" not in register_page.data
    assert b"auth-access-frame" in login_page.data
    assert b"Your shelf is ready when you are" in login_page.data
    assert b"data-password-toggle" in login_page.data

    app.config["GOOGLE_CLIENT_ID"] = "test-client"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    configured = client.get("/register")
    assert b"Join with Google" in configured.data
    assert b"GitHub setup needed" not in configured.data


def test_failed_login_preserves_email_but_never_password(client):
    response = client.post(
        "/login",
        data={"email": "remember-this@example.com", "password": "do-not-render-this-password"},
    )
    assert response.status_code == 401
    assert b'remember-this@example.com' in response.data
    assert b'do-not-render-this-password' not in response.data


def test_authenticator_two_factor_setup_login_replay_and_recovery(client, app):
    register(client)
    setup = client.get("/security/2fa/setup")
    assert setup.status_code == 200
    assert b"Add a second lock to your account" in setup.data
    with client.session_transaction() as browser_session:
        pending_ciphertext = browser_session["two_factor_setup"]["ciphertext"]
    with app.app_context():
        secret = decrypt_secret(pending_ciphertext)

    qr = client.get("/security/2fa/setup/qr")
    assert qr.status_code == 200
    assert qr.mimetype == "image/png"
    assert qr.data.startswith(b"\x89PNG")
    assert qr.headers["Cache-Control"] == "private, no-store"

    enabled = client.post(
        "/security/2fa/setup",
        data={"code": totp_code(secret)},
    )
    assert enabled.status_code == 200
    assert b"TWO-FACTOR AUTHENTICATION IS ON" in enabled.data
    recovery_codes = list(dict.fromkeys(re.findall(rb"[2-9A-HJ-NP-Z]{4}(?:-[2-9A-HJ-NP-Z]{4}){3}", enabled.data)))
    assert len(recovery_codes) == 10
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        stored_codes = list(db.session.scalars(db.select(TwoFactorRecoveryCode).where(TwoFactorRecoveryCode.user_id == user.id)))
        assert user.two_factor_enabled
        assert secret not in user.two_factor_secret_ciphertext
        assert len(stored_codes) == 10
        assert all(code.decode().replace("-", "") not in item.code_hash for code, item in zip(recovery_codes, stored_codes))

    client.post("/logout")
    challenged = login(client)
    assert b"One more secure step" in challenged.data
    with client.session_transaction() as browser_session:
        assert "_user_id" not in browser_session
        assert browser_session["two_factor_preauth"]["user_id"]

    next_code = totp_code(secret, for_time=time.time() + 30)
    verified = client.post("/two-factor/challenge", data={"code": next_code}, follow_redirects=True)
    assert verified.status_code == 200
    assert b"PROMOTER WORKSPACE" in verified.data

    client.post("/logout")
    login(client)
    replay = client.post("/two-factor/challenge", data={"code": next_code})
    assert replay.status_code == 401
    assert b"One more secure step" in replay.data

    recovered = client.post(
        "/two-factor/challenge",
        data={"code": recovery_codes[0].decode()},
        follow_redirects=True,
    )
    assert recovered.status_code == 200
    assert b"A recovery code was used" in recovered.data
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count(TwoFactorRecoveryCode.id)).where(TwoFactorRecoveryCode.used_at.is_not(None))
        ) == 1


def test_two_factor_management_requires_current_code_and_revokes_sessions(client, app):
    register(client)
    _secret, recovery_codes = enable_two_factor_for_user(app)
    invalid = client.post("/security/2fa/disable", data={"code": "000000"}, follow_redirects=True)
    assert b"before disabling" in invalid.data
    with app.app_context():
        assert db.session.scalar(db.select(User).where(User.email == "promoter@example.com")).two_factor_enabled

    disabled = client.post(
        "/security/2fa/disable",
        data={"code": recovery_codes[0]},
        follow_redirects=True,
    )
    assert b"Two-factor authentication was disabled" in disabled.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        assert not user.two_factor_enabled
        assert db.session.scalar(db.select(db.func.count(TwoFactorRecoveryCode.id))) == 0
    assert client.get("/studio/").status_code == 302


def test_two_factor_setup_requires_fresh_identity_confirmation(client):
    register(client)
    with client.session_transaction() as browser_session:
        browser_session["_fresh"] = False
    response = client.get("/security/2fa/setup", follow_redirects=True)
    assert response.status_code == 200
    assert b"Confirm it" in response.data
    confirmed = client.post(
        "/reauthenticate?next=/security/2fa/setup",
        data={"password": "very-secure-password"},
        follow_redirects=True,
    )
    assert confirmed.status_code == 200
    assert b"Add a second lock to your account" in confirmed.data


def test_marketplace_allowlist_rejects_lookalikes():
    assert detect_marketplace("https://shopee.ph/item") == "shopee"
    assert detect_marketplace("https://evilshopee.ph/item") is None
    assert detect_marketplace("http://shopee.ph/item") is None
    assert detect_marketplace("https://shopee.ph.evil.example/item") is None


def test_totp_generation_matches_the_rfc_sha1_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, for_time=59) == "287082"


def test_promoter_cannot_toggle_another_users_product(client, app):
    register(client)
    with app.app_context():
        other = User(email="other@example.com", display_name="Other", role="promoter")
        other.set_password("another-secure-password")
        other.shop = Shop(
            name="Other Shelf",
            slug="other",
            subscription_ends_at=utcnow() + timedelta(days=7),
        )
        db.session.add(other)
        db.session.flush()
        product = Product(
            shop=other.shop,
            name="Other product",
            description="A valid product description",
            department="Tech & Gadgets",
            marketplace="shopee",
            affiliate_url="https://shopee.ph/item",
            price_cents=10000,
            image_name="missing.jpg",
        )
        db.session.add(product)
        db.session.commit()
        product_id = product.id

    client.post(f"/studio/products/{product_id}/status")
    with app.app_context():
        assert db.session.get(Product, product_id).status == "active"


def test_non_admin_cannot_open_admin(client):
    register(client)
    assert client.get("/admin/").status_code == 403
    assert client.post(
        "/admin/crm/messages",
        data={"recipient": "all", "subject": "Unauthorized", "body": "This must not be saved."},
    ).status_code == 403


def test_reserved_admin_email_never_grants_web_registration_privileges(client, app):
    reserved_email = "reserved-admin@sulitshelf.ph"
    app.config["ADMIN_EMAILS"] = {reserved_email}

    response = register(client, email=reserved_email)
    assert response.status_code == 200
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == reserved_email))
        assert user.role == "promoter"
    assert client.get("/admin/").status_code == 403


def test_admin_email_configuration_cannot_promote_an_existing_session(client, app):
    register(client)
    app.config["ADMIN_EMAILS"] = {"promoter@example.com"}
    assert client.get("/admin/").status_code == 403


def test_admin_crm_direct_message_is_private_and_can_be_marked_read(client, app):
    register(client)
    with app.app_context():
        promoter = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        promoter_id = promoter.id
    client.post("/logout")
    register(client, email="other@example.com")
    with app.app_context():
        other_id = db.session.scalar(db.select(User.id).where(User.email == "other@example.com"))
    client.post("/logout")
    app.test_cli_runner().invoke(
        args=["seed-admin", "--email", "admin@example.com", "--password", "administrator-password"]
    )
    login(client, "admin@example.com", "administrator-password")
    response = client.post(
        "/admin/crm/messages",
        data={
            "recipient": str(promoter_id),
            "subject": "Welcome to your private inbox",
            "body": "This account update is visible only inside your promoter workspace.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Message delivered to 1 promoter" in response.data
    assert b"Welcome to your private inbox" in response.data
    assert b"signout-28" in response.data
    assert b"data-crm-composer" in response.data
    assert b"data-crm-character-count" in response.data
    client.post(
        "/admin/crm/messages",
        data={
            "recipient": str(other_id),
            "subject": "Another promoter's private notice",
            "body": "Only the other promoter should be able to open this message.",
        },
    )
    with app.app_context():
        message = db.session.scalar(db.select(CRMMessage).where(CRMMessage.recipient_id == promoter_id))
        other_message = db.session.scalar(db.select(CRMMessage).where(CRMMessage.recipient_id == other_id))
        assert message.recipient_id == promoter_id
        assert message.email_status == "not_requested"
        message_id = message.id
        other_message_id = other_message.id

    client.post("/logout")
    login(client)
    inbox = client.get("/studio/?tab=inbox")
    assert inbox.status_code == 200
    assert b"Welcome to your private inbox" in inbox.data
    assert b"This account update is visible only" in inbox.data
    assert b"Another promoter's private notice" not in inbox.data
    assert b"admin@example.com" not in inbox.data
    assert b'class="inbox-count">1' in inbox.data
    assert client.post(f"/studio/messages/{other_message_id}/read").status_code == 404
    response = client.post(f"/studio/messages/{message_id}/read", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(CRMMessage, message_id).read_at is not None
        assert db.session.get(CRMMessage, other_message_id).read_at is None


def test_admin_crm_broadcast_is_in_app_and_direct_email_records_delivery(client, app, monkeypatch):
    register(client, email="first@example.com")
    client.post("/logout")
    register(client, email="second@example.com")
    client.post("/logout")
    app.test_cli_runner().invoke(
        args=["seed-admin", "--email", "admin@example.com", "--password", "administrator-password"]
    )
    email_calls = []
    monkeypatch.setattr(
        "app.admin.routes.send_crm_message_email",
        lambda message, inbox_url: email_calls.append((message.id, inbox_url)) or "resend",
    )
    login(client, "admin@example.com", "administrator-password")
    response = client.post(
        "/admin/crm/messages",
        data={
            "recipient": "all",
            "subject": "Platform announcement",
            "body": "A short update for every active promoter account.",
            "send_email": "yes",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Message delivered to 2 promoters" in response.data
    assert b"Broadcast email copies are disabled" in response.data
    with app.app_context():
        messages = list(db.session.scalars(db.select(CRMMessage).order_by(CRMMessage.recipient_id)))
        assert len(messages) == 2
        assert {message.recipient.email for message in messages} == {"first@example.com", "second@example.com"}
        assert all(message.email_status == "not_requested" for message in messages)
        audit_entry = db.session.scalar(db.select(AuditLog).where(AuditLog.action == "crm.broadcast_sent"))
        assert audit_entry is not None
        first_id = db.session.scalar(db.select(User.id).where(User.email == "first@example.com"))
    assert email_calls == []

    response = client.post(
        "/admin/crm/messages",
        data={
            "recipient": str(first_id),
            "subject": "Direct support follow-up",
            "body": "This one-to-one message also receives an email copy.",
            "send_email": "yes",
        },
        follow_redirects=True,
    )
    assert b"Email accepted for 1 of 1" in response.data
    assert len(email_calls) == 1
    with app.app_context():
        direct = db.session.scalar(db.select(CRMMessage).where(CRMMessage.subject == "Direct support follow-up"))
        assert direct.email_status == "sent"
        assert direct.email_provider == "resend"


def test_seeded_admin_has_free_shop_and_can_open_cms(client, app):
    result = app.test_cli_runner().invoke(
        args=["seed-admin", "--email", "admin@example.com", "--password", "administrator-password"]
    )
    assert result.exit_code == 0
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@example.com"))
        assert admin.role == "admin"
        assert admin.shop.plan_key == "free"
        assert admin.shop.subscription_status == "free"

    login(client, "admin@example.com", "administrator-password")
    response = client.get("/admin/")
    assert response.status_code == 200
    assert b"Administrator CMS" in response.data
    assert b"Donation amounts" in response.data


def test_duplicate_wallet_reference_is_unique(app):
    with app.app_context():
        user = User(email="one@example.com", display_name="One")
        user.set_password("secure-password-1")
        user.shop = Shop(name="One", slug="one", subscription_ends_at=utcnow() + timedelta(days=7))
        db.session.add(user)
        db.session.flush()
        db.session.add(
            Donation(
                shop=user.shop,
                method="wallet",
                amount_cents=4900,
                reference_number="ABC12345",
                receipt_name="one.png",
            )
        )
        db.session.commit()
        db.session.add(
            Donation(
                shop=user.shop,
                method="wallet",
                amount_cents=4900,
                reference_number="ABC12345",
                receipt_name="two.png",
            )
        )
        try:
            db.session.commit()
            raised = False
        except Exception:
            db.session.rollback()
            raised = True
        assert raised


def test_dodo_webhook_is_signed_idempotent_and_matches_expected_amount(client, app):
    register(client)
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        donation = Donation(shop=user.shop, method="dodo", amount_cents=4900, status="initiated")
        db.session.add(donation)
        db.session.commit()
        donation_id = donation.id
        shop_id = user.shop.id

    signing_key = b"test-dodo-webhook-signing-key"
    app.config["DODO_PAYMENTS_WEBHOOK_KEY"] = "whsec_" + base64.urlsafe_b64encode(signing_key).decode().rstrip("=")

    def signed_post(webhook_id, expected_amount):
        payload = {
            "type": "payment.succeeded",
            "data": {
                "payment_id": "pay_test_123",
                "metadata": {
                    "purpose": "sulitshelf_donation",
                    "donation_id": str(donation_id),
                    "shop_id": str(shop_id),
                    "expected_amount_cents": str(expected_amount),
                },
            },
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = base64.b64encode(
            hmac.new(signing_key, f"{webhook_id}.{timestamp}.".encode() + raw, hashlib.sha256).digest()
        ).decode()
        return client.post(
            "/payments/dodo/webhook",
            data=raw,
            content_type="application/json",
            headers={
                "webhook-id": webhook_id,
                "webhook-timestamp": timestamp,
                "webhook-signature": f"v1,{signature}",
            },
        )

    assert signed_post("event-mismatch", 9900).status_code == 200
    with app.app_context():
        assert db.session.get(Donation, donation_id).status == "initiated"

    response = signed_post("event-valid", 4900)
    assert response.status_code == 200
    assert response.json == {"received": True}
    with app.app_context():
        assert db.session.get(Donation, donation_id).status == "completed"

    duplicate = signed_post("event-valid", 4900)
    assert duplicate.status_code == 200
    assert duplicate.json == {"duplicate": True, "received": True}


def test_wallet_donation_requires_reference_and_receipt(client, app):
    register(client)
    with app.app_context():
        settings = db.session.get(PlatformSettings, 1)
        settings.wallet_provider = "Maya"
        settings.gcash_account_name = "SulitShelf Maintainer"
        settings.gcash_number = "09123456789"
        db.session.commit()

    response = client.post(
        "/payments/wallet/donate",
        data={"tier_key": "coffee", "reference_number": "ABC12345"},
        follow_redirects=True,
    )
    assert b"Choose an image to upload" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Donation.id))) == 0

    response = client.post(
        "/payments/wallet/donate",
        data={
            "tier_key": "coffee",
            "reference_number": "ABC12345",
            "receipt": png_upload(),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"donation receipt was submitted" in response.data
    with app.app_context():
        donation = db.session.scalar(db.select(Donation))
        assert donation.status == "pending"
        assert donation.amount_cents == 4900
        assert donation.receipt_name.endswith(".webp")


def test_free_access_does_not_expire_or_hide_products(client, app):
    register(client)
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        user.shop.subscription_ends_at = utcnow() - timedelta(days=30)
        product = Product(
            shop=user.shop,
            name="Always public",
            description="A product that remains public without payment.",
            department="Tech & Gadgets",
            marketplace="shopee",
            affiliate_url="https://shopee.ph/item",
            price_cents=25000,
            image_name="missing.jpg",
        )
        db.session.add(product)
        db.session.commit()
        slug = user.shop.slug

    response = client.get(f"/shop/{slug}")
    assert response.status_code == 200
    assert b"Always public" in response.data


def _fill_active_products(app, total):
    with app.app_context():
        shop = db.session.scalar(db.select(User).where(User.email == "promoter@example.com")).shop
        for index in range(total):
            db.session.add(
                Product(
                    shop=shop,
                    name=f"Product {index + 1}",
                    description="A valid product description for a useful affiliate listing.",
                    why_sulit="A practical and affordable option for everyday use.",
                    department="Tech & Gadgets",
                    marketplace="shopee",
                    affiliate_url=f"https://shopee.ph/item-{index + 1}",
                    price_cents=9900,
                    image_name="missing.webp",
                )
            )
        db.session.commit()


def _product_form():
    return {
        "name": "Product Fifty One",
        "description": "A useful product description with enough detail for shoppers.",
        "why_sulit": "It offers useful value at an affordable listed price.",
        "best_for": "students and commuters",
        "department": "Tech & Gadgets",
        "affiliate_url": "https://shopee.ph/product-fifty-one",
        "price": "49.00",
        "rights_confirmed": "yes",
        "image": png_upload(name="product.png"),
    }


def test_free_plan_enforces_50_total_products_server_side(client, app):
    register(client)
    _fill_active_products(app, 50)
    with app.app_context():
        first_product = db.session.scalar(db.select(Product).order_by(Product.id))
        first_product.status = "paused"
        db.session.commit()
    response = client.post(
        "/studio/products",
        data=_product_form(),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"already has 50 products" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 50


def test_active_pro_can_publish_more_than_50_products(client, app):
    register(client)
    _fill_active_products(app, 50)
    with app.app_context():
        shop = db.session.scalar(db.select(User).where(User.email == "promoter@example.com")).shop
        shop.plan_key = "pro"
        shop.subscription_status = "active"
        shop.subscription_source = "manual"
        shop.subscription_ends_at = utcnow() + timedelta(days=30)
        db.session.commit()
    response = client.post(
        "/studio/products",
        data=_product_form(),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Product published" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 51


def test_expired_pro_keeps_products_but_pauses_overflow(client, app):
    register(client)
    _fill_active_products(app, 51)
    with app.app_context():
        shop = db.session.scalar(db.select(User).where(User.email == "promoter@example.com")).shop
        shop.plan_key = "pro"
        shop.subscription_status = "active"
        shop.subscription_source = "manual"
        shop.subscription_ends_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    response = client.get("/")
    assert response.status_code == 200
    with app.app_context():
        shop = db.session.scalar(db.select(User).where(User.email == "promoter@example.com")).shop
        assert shop.plan_key == "free"
        assert shop.subscription_status == "expired"
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 51
        assert db.session.scalar(db.select(db.func.count(Product.id)).where(Product.status == "active")) == 50


def test_manual_pro_payment_requires_receipt_and_admin_approval(client, app):
    register(client)
    with app.app_context():
        settings = db.session.get(PlatformSettings, 1)
        settings.wallet_provider = "Maya"
        settings.gcash_account_name = "SulitShelf PH"
        settings.gcash_number = "09123456789"
        db.session.commit()

    response = client.post(
        "/payments/wallet/subscribe",
        data={"reference_number": "PROREF123"},
        follow_redirects=True,
    )
    assert b"Choose an image to upload" in response.data
    response = client.post(
        "/payments/wallet/subscribe",
        data={"reference_number": "PROREF123", "receipt": png_upload()},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"submitted for administrator review" in response.data
    with app.app_context():
        payment = db.session.scalar(db.select(PaymentSubmission))
        assert payment.amount_cents == 4900
        assert payment.status == "pending"
        assert payment.receipt_name.endswith(".webp")
        assert db.session.scalar(db.select(WalletReference).where(WalletReference.reference == "PROREF123"))
        payment_id = payment.id

    client.post("/logout")
    app.test_cli_runner().invoke(args=["seed-admin", "--email", "admin@example.com", "--password", "administrator-password"])
    login(client, "admin@example.com", "administrator-password")
    response = client.post(f"/admin/subscriptions/{payment_id}/approved", follow_redirects=True)
    assert b"Pro access activated for 30 days" in response.data
    with app.app_context():
        payment = db.session.get(PaymentSubmission, payment_id)
        assert payment.status == "approved"
        assert payment.shop.plan_key == "pro"
        assert payment.shop.subscription_source == "manual"
        assert aware(payment.shop.subscription_ends_at) > utcnow() + timedelta(days=29)


def test_wallet_reference_cannot_be_reused_between_subscription_and_donation(client, app):
    register(client)
    with app.app_context():
        settings = db.session.get(PlatformSettings, 1)
        settings.gcash_number = "09123456789"
        db.session.commit()
    client.post(
        "/payments/wallet/subscribe",
        data={"reference_number": "ONETRANS123", "receipt": png_upload()},
        content_type="multipart/form-data",
    )
    response = client.post(
        "/payments/wallet/donate",
        data={"tier_key": "coffee", "reference_number": "ONETRANS123", "receipt": png_upload()},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"reference number was already submitted" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Donation.id))) == 0


def test_dodo_subscription_webhook_verifies_offer_and_ignores_stale_events(client, app):
    register(client)
    with app.app_context():
        plan = db.session.get(Plan, "pro")
        plan.dodo_product_id = "pdt_pro_49"
        shop_id = db.session.scalar(db.select(User).where(User.email == "promoter@example.com")).shop.id
        db.session.commit()

    now = utcnow()
    base_data = {
        "subscription_id": "sub_pro_123",
        "product_id": "pdt_pro_49",
        "currency": "PHP",
        "recurring_pre_tax_amount": 4900,
        "trial_period_days": 0,
        "status": "active",
        "created_at": now.isoformat(),
        "next_billing_date": (now + timedelta(days=30)).isoformat(),
        "customer": {"customer_id": "cus_123", "email": "promoter@example.com", "name": "Test Promoter"},
        "metadata": {"purpose": "sulitshelf_subscription", "shop_id": str(shop_id), "plan_key": "pro", "expected_amount_cents": "4900", "expected_product_id": "pdt_pro_49"},
    }
    mismatch = {"type": "subscription.active", "timestamp": now.isoformat(), "data": {**base_data, "recurring_pre_tax_amount": 9900}}
    assert signed_dodo_post(client, app, "sub-mismatch", mismatch).status_code == 200
    with app.app_context():
        assert db.session.get(Shop, shop_id).plan_key == "free"

    active_payload = {"type": "subscription.active", "timestamp": now.isoformat(), "data": base_data}
    assert signed_dodo_post(client, app, "sub-active", active_payload).status_code == 200
    with app.app_context():
        shop = db.session.get(Shop, shop_id)
        assert shop.plan_key == "pro"
        assert shop.subscription_status == "active"
        assert shop.subscription_source == "dodo"
        assert shop.subscription_external_id == "sub_pro_123"
        assert shop.subscription_customer_id == "cus_123"
        assert shop.subscription_price_cents == 4900

    stale_payload = {"type": "subscription.expired", "timestamp": (now - timedelta(days=1)).isoformat(), "data": {**base_data, "status": "expired"}}
    assert signed_dodo_post(client, app, "sub-stale", stale_payload).status_code == 200
    with app.app_context():
        assert db.session.get(Shop, shop_id).plan_key == "pro"


def test_dodo_checkout_uses_recurring_product_without_trial_override(client, app, monkeypatch):
    register(client)
    app.config["DODO_PAYMENTS_API_KEY"] = "test_api_key"
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"checkout_url": "https://checkout.dodopayments.test/session"}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    with app.app_context():
        plan = db.session.get(Plan, "pro")
        plan.dodo_product_id = "pdt_pro_49"
        db.session.commit()
    monkeypatch.setattr("app.payments.routes.requests.post", fake_post)
    response = client.post("/payments/dodo/subscribe")
    assert response.status_code == 302
    assert response.location.startswith("https://checkout.dodopayments.test/")
    assert captured["json"]["product_cart"] == [{"product_id": "pdt_pro_49", "quantity": 1}]
    assert captured["json"]["metadata"]["expected_amount_cents"] == "4900"
    assert captured["json"]["metadata"]["expected_product_id"] == "pdt_pro_49"
    assert "subscription_data" not in captured["json"]


class FakeGoogleClient:
    def __init__(self, email="social@example.com", subject="google-123"):
        self.email = email
        self.subject = subject

    def authorize_access_token(self):
        return {
            "userinfo": {
                "sub": self.subject,
                "email": self.email,
                "email_verified": True,
                "name": "Social Promoter",
            }
        }


def test_google_oauth_creates_separate_identity_without_storing_token(client, app, monkeypatch):
    app.config["GOOGLE_CLIENT_ID"] = "test-client"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    app.config["ADMIN_EMAILS"] = {"social@example.com"}
    monkeypatch.setattr("app.auth.routes.oauth.create_client", lambda provider: FakeGoogleClient())

    response = client.get("/oauth/google/callback", follow_redirects=True)
    assert response.status_code == 200
    assert b"Signed in securely with Google" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "social@example.com"))
        identity = db.session.scalar(db.select(OAuthIdentity).where(OAuthIdentity.user_id == user.id))
        assert not user.has_usable_password
        assert user.role == "promoter"
        assert identity.provider == "google"
        assert identity.provider_user_id == "google-123"
        assert not hasattr(identity, "access_token")


def test_oauth_login_cannot_bypass_enabled_two_factor(client, app, monkeypatch):
    register(client)
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        db.session.add(
            OAuthIdentity(
                user=user,
                provider="google",
                provider_user_id="google-two-factor",
                email_at_link=user.email,
                last_used_at=utcnow(),
            )
        )
        db.session.commit()
    secret, _codes = enable_two_factor_for_user(app)
    client.post("/logout")
    app.config["GOOGLE_CLIENT_ID"] = "test-client"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    monkeypatch.setattr(
        "app.auth.routes.oauth.create_client",
        lambda provider: FakeGoogleClient(email="promoter@example.com", subject="google-two-factor"),
    )
    with client.session_transaction() as browser_session:
        browser_session["oauth_google_mode"] = "login"

    challenged = client.get("/oauth/google/callback", follow_redirects=True)
    assert challenged.status_code == 200
    assert b"One more secure step" in challenged.data
    with client.session_transaction() as browser_session:
        assert "_user_id" not in browser_session

    verified = client.post(
        "/two-factor/challenge",
        data={"code": totp_code(secret)},
        follow_redirects=True,
    )
    assert b"Signed in securely with Google" in verified.data
    assert b"PROMOTER WORKSPACE" in verified.data


def test_logout_clears_authentication_and_oauth_session_state(client):
    register(client)
    with client.session_transaction() as session:
        session["oauth_google_mode"] = "link"
        session["oauth_google_next"] = "/studio/?tab=settings"
    client.post("/logout")
    with client.session_transaction() as session:
        assert "_user_id" not in session
        assert "oauth_google_mode" not in session
        assert "oauth_google_next" not in session


def test_oauth_does_not_auto_link_an_existing_email(client, app, monkeypatch):
    register(client)
    client.post("/logout")
    app.config["GOOGLE_CLIENT_ID"] = "test-client"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    monkeypatch.setattr(
        "app.auth.routes.oauth.create_client",
        lambda provider: FakeGoogleClient(email="promoter@example.com", subject="different-google-account"),
    )

    response = client.get("/oauth/google/callback", follow_redirects=True)
    assert b"Sign in with its password" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(OAuthIdentity.id))) == 0


def test_logged_in_user_can_explicitly_link_oauth(client, app, monkeypatch):
    register(client)
    app.config["GOOGLE_CLIENT_ID"] = "test-client"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    monkeypatch.setattr("app.auth.routes.oauth.create_client", lambda provider: FakeGoogleClient())
    with client.session_transaction() as session:
        session["oauth_google_mode"] = "link"

    response = client.get("/oauth/google/callback", follow_redirects=True)
    assert b"Google is now connected" in response.data
    with app.app_context():
        identity = db.session.scalar(db.select(OAuthIdentity))
        assert identity.user.email == "promoter@example.com"


def test_local_upload_is_real_resized_webp(app):
    with app.app_context():
        app.config["IMAGE_STORAGE_BACKEND"] = "local"
        app.config["IMAGE_MAX_DIMENSION"] = 100
        name = save_image(png_upload(size=(500, 250))[0], "products", "product")
        stored = path_for("products", name)
        assert name.endswith(".webp")
        with Image.open(stored) as image:
            assert image.format == "WEBP"
            assert image.size == (100, 50)


def test_spoofed_image_header_is_rejected(app):
    fake = BytesIO(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    fake.filename = "fake.png"
    with app.app_context(), pytest.raises(UploadError):
        save_image(fake, "products", "product")


def test_private_cloudinary_upload_uses_authenticated_webp(app, monkeypatch):
    captured = {}

    def fake_upload(file, **options):
        captured.update(options)
        captured["bytes"] = file.read()
        return {"public_id": "sulitshelf/receipts/donation-test"}

    monkeypatch.setattr(cloudinary.uploader, "upload", fake_upload)
    monkeypatch.setattr(
        "app.services.storage.cloudinary.utils.private_download_url",
        lambda *args, **kwargs: "https://example.cloudinary.test/private-receipt",
    )
    with app.app_context():
        app.config["IMAGE_STORAGE_BACKEND"] = "cloudinary"
        app.config["CLOUDINARY_URL"] = "cloudinary://key:secret@test-cloud"
        name = save_image(png_upload()[0], "receipts", "donation", private=True)
        assert name.startswith("cld:authenticated:webp:")
        assert captured["type"] == "authenticated"
        assert captured["format"] == "webp"
        assert captured["timeout"] == 20
        assert captured["bytes"][:4] == b"RIFF"
        assert captured["bytes"][8:12] == b"WEBP"
        assert media_url(name) is None
        assert media_url(name, private=True) == "https://example.cloudinary.test/private-receipt"


def create_product(app, *, shop=None, name="Sulit Power Bank"):
    with app.app_context():
        if shop is None:
            user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
            shop = user.shop
        product = Product(
            shop=shop,
            name=name,
            description="A compact backup battery with practical everyday capacity.",
            why_sulit="Useful backup power at an affordable listed price.",
            best_for="students and commuters",
            department="Tech & Gadgets",
            marketplace="shopee",
            affiliate_url="https://shopee.ph/sulit-power-bank?affiliate_id=test",
            price_cents=49900,
            image_name="missing.webp",
        )
        db.session.add(product)
        db.session.commit()
        return product.id


def test_growth_campaigns_are_seeded_and_public(client, app):
    response = client.get("/")
    assert response.status_code == 200
    assert b"QUICK COLLECTIONS" in response.data
    assert b"Student Essentials Under" in response.data
    response = client.get("/campaign/student-essentials-under-500")
    assert response.status_code == 200
    assert b"This collection is ready for its first products" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Campaign.id))) == 3


def test_product_publish_requires_honest_sulit_reason(client, app):
    register(client)
    base_data = {
        "name": "Budget Keyboard",
        "description": "A compact keyboard intended for study and home desk setups.",
        "department": "Tech & Gadgets",
        "affiliate_url": "https://shopee.ph/budget-keyboard",
        "price": "399.00",
        "rights_confirmed": "yes",
        "image": png_upload(name="keyboard.png"),
    }
    response = client.post("/studio/products", data=base_data, content_type="multipart/form-data", follow_redirects=True)
    assert b"Explain why it is sulit using 8" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 0

    base_data["why_sulit"] = "It provides the essentials for a low-cost student desk."
    base_data["best_for"] = "students and shared workspaces"
    base_data["image"] = png_upload(name="keyboard.png")
    response = client.post("/studio/products", data=base_data, content_type="multipart/form-data", follow_redirects=True)
    assert b"Product published" in response.data
    with app.app_context():
        product = db.session.scalar(db.select(Product))
        assert product.why_sulit.startswith("It provides")
        assert product.image_name.endswith(".webp")


def test_product_publish_rejects_unsupported_marketplace_with_precise_feedback(client, app):
    register(client)
    form = _product_form()
    form["affiliate_url"] = "https://example.com/not-an-affiliate-link"
    response = client.post(
        "/studio/products",
        data=form,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"direct HTTPS Shopee, Lazada, or TikTok Shop link" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 0


def test_duplicate_affiliate_link_is_idempotent(client, app):
    register(client)
    first = client.post(
        "/studio/products",
        data=_product_form(),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Product published" in first.data
    duplicate = client.post(
        "/studio/products",
        data=_product_form(),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert duplicate.status_code == 200
    assert b"exact affiliate link is already on your shelf" in duplicate.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 1


def test_oversized_product_request_returns_to_form_with_clear_feedback(client, app):
    register(client)
    form = _product_form()
    form["image"] = (BytesIO(b"x" * (app.config["MAX_CONTENT_LENGTH"] + 1)), "too-large.png")
    response = client.post(
        "/studio/products",
        data=form,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"image smaller than 8 MB" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 0


def test_outbound_click_records_real_source_without_visitor_identity(client, app):
    register(client)
    product_id = create_product(app)
    response = client.get(f"/out/{product_id}?source=tiktok")
    assert response.status_code == 302
    assert response.location.startswith("https://shopee.ph/")
    with app.app_context():
        event = db.session.scalar(db.select(ClickEvent))
        assert event.source == "tiktok"
        assert event.marketplace == "shopee"
        assert db.session.get(Product, product_id).click_count == 1
        assert "ip" not in ClickEvent.__table__.columns


def test_product_detail_and_qr_are_public(client, app):
    register(client)
    product_id = create_product(app)
    client.post("/logout")
    response = client.get(f"/product/{product_id}?source=qr")
    assert response.status_code == 200
    assert b"WHY IT" in response.data
    assert b"Check current price on Shopee" in response.data
    response = client.get(f"/qr/product/{product_id}.png")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG")


def test_disabled_promoter_is_removed_from_every_public_product_surface(client, app):
    register(client)
    product_id = create_product(app, name="Disabled Seller Product")
    with app.app_context():
        product = db.session.get(Product, product_id)
        promoter_id = product.shop.owner_id
        shop_slug = product.shop.slug
        campaign = db.session.scalar(db.select(Campaign).order_by(Campaign.id))
        campaign.product_links.append(CampaignProduct(product=product, position=0))
        campaign_slug = campaign.slug
        db.session.commit()

    client.post("/logout")
    app.test_cli_runner().invoke(
        args=["seed-admin", "--email", "admin@example.com", "--password", "administrator-password"]
    )
    login(client, "admin@example.com", "administrator-password")
    assert client.post(f"/admin/users/{promoter_id}/toggle").status_code == 302
    client.post("/logout")

    assert b"Disabled Seller Product" not in client.get("/").data
    assert client.get(f"/shop/{shop_slug}").status_code == 404
    assert client.get(f"/product/{product_id}").status_code == 404
    outbound = client.get(f"/out/{product_id}")
    assert outbound.status_code == 302
    assert "link=unavailable" in outbound.location
    assert client.get(f"/api/products?ids={product_id}").json == {"products": []}
    assert client.post(
        "/events/impressions",
        json={"product_ids": [product_id], "source": "mall"},
    ).json == {"recorded": 0}
    assert client.post(
        f"/products/{product_id}/report",
        data={"reason": "broken_link"},
    ).status_code == 404
    assert client.get(f"/qr/product/{product_id}.png").status_code == 404
    assert client.get(f"/qr/shop/{shop_slug}.png").status_code == 404
    assert b"Disabled Seller Product" not in client.get(f"/campaign/{campaign_slug}").data
    sitemap = client.get("/sitemap.xml")
    assert f"/product/{product_id}".encode() not in sitemap.data
    assert f"/shop/{shop_slug}".encode() not in sitemap.data

    login(client, "admin@example.com", "administrator-password")
    assert client.post(f"/admin/users/{promoter_id}/toggle").status_code == 302
    client.post("/logout")
    assert client.get(f"/shop/{shop_slug}").status_code == 200
    assert client.get(f"/product/{product_id}").status_code == 200
    assert client.get(f"/out/{product_id}").location.startswith("https://shopee.ph/")


def test_shop_keeps_allowlisted_campaign_source_and_services_are_public(client, app):
    register(client)
    product_id = create_product(app)
    with app.app_context():
        slug = db.session.get(Product, product_id).shop.slug
    client.post("/logout")
    response = client.get(f"/shop/{slug}?source=tiktok")
    assert f"/out/{product_id}?source=tiktok".encode() in response.data
    response = client.get("/promoter-services")
    assert response.status_code == 200
    assert b"Pay only if you want help" in response.data


def test_public_report_requires_admin_to_pause_product(client, app):
    register(client)
    product_id = create_product(app)
    client.post("/logout")
    response = client.post(
        f"/products/{product_id}/report",
        data={"reason": "broken_link", "details": "The marketplace says this item is unavailable."},
        follow_redirects=True,
    )
    assert b"administrator will review" in response.data
    with app.app_context():
        report = db.session.scalar(db.select(ProductReport))
        assert report.status == "pending"
        report_id = report.id

    register(client, email="second@example.com")
    assert client.post(f"/admin/reports/{report_id}/pause").status_code == 403
    client.post("/logout")
    result = app.test_cli_runner().invoke(
        args=["seed-admin", "--email", "admin@example.com", "--password", "administrator-password"]
    )
    assert result.exit_code == 0
    login(client, "admin@example.com", "administrator-password")
    response = client.post(f"/admin/reports/{report_id}/pause", follow_redirects=True)
    assert b"product paused" in response.data
    with app.app_context():
        assert db.session.get(Product, product_id).status == "paused"
        assert db.session.get(ProductReport, report_id).status == "resolved"


def test_admin_can_curate_campaign_verify_promoter_and_label_sponsor(client, app):
    register(client)
    product_id = create_product(app)
    with app.app_context():
        promoter = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        shop_id = promoter.shop.id
    client.post("/logout")
    app.test_cli_runner().invoke(
        args=["seed-admin", "--email", "admin@example.com", "--password", "administrator-password"]
    )
    login(client, "admin@example.com", "administrator-password")
    response = client.post(
        "/admin/campaigns",
        data={
            "title": "Payday Student Finds",
            "slug": "payday-student-finds",
            "eyebrow": "PAYDAY PICKS",
            "description": "A focused collection of affordable products for student payday budgets.",
            "product_ids": str(product_id),
            "sort_order": "5",
            "is_active": "yes",
        },
        follow_redirects=True,
    )
    assert b"Campaign collection created" in response.data
    with app.app_context():
        campaign_id = db.session.scalar(db.select(Campaign.id).where(Campaign.slug == "payday-student-finds"))
    response = client.post(
        f"/admin/campaigns/{campaign_id}",
        data={
            "title": "Updated Payday Student Finds",
            "slug": "payday-student-finds",
            "eyebrow": "PAYDAY PICKS",
            "description": "An updated collection of affordable products for student payday budgets.",
            "product_ids": str(product_id),
            "sort_order": "5",
            "is_active": "yes",
        },
        follow_redirects=True,
    )
    assert b"Campaign collection updated" in response.data
    client.post(f"/admin/shops/{shop_id}/verify")
    client.post(f"/admin/products/{product_id}/sponsored")
    response = client.get("/campaign/payday-student-finds")
    assert b"Sulit Power Bank" in response.data
    response = client.get("/")
    assert b"Sponsored" in response.data
    assert b'title="Verified promoter"' in response.data
    with app.app_context():
        assert db.session.get(Shop, shop_id).is_verified is True
        assert db.session.get(Product, product_id).is_sponsored is True


def test_recover_admin_reactivates_and_resets_existing_account(client, app):
    register(client, email="locked-admin@example.com", password="old-secure-password")
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "locked-admin@example.com"))
        user.role = "promoter"
        user.is_active_account = False
        user.shop.is_verified = False
        db.session.commit()

    result = app.test_cli_runner().invoke(
        args=[
            "recover-admin",
            "--email",
            "locked-admin@example.com",
            "--password",
            "new-recovery-password-2026",
        ]
    )
    assert result.exit_code == 0
    assert "Administrator recovered" in result.output
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "locked-admin@example.com"))
        assert user.role == "admin"
        assert user.is_active_account is True
        assert user.check_password("new-recovery-password-2026")
        assert user.shop.is_verified is True
        log = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "admin.account_recovered")
        )
        assert log is not None
        assert "new-recovery-password-2026" not in log.details


def test_recover_admin_rejects_short_password_and_can_create_account(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "recover-admin",
            "--email",
            "recovered@example.com",
            "--password",
            "too-short",
        ]
    )
    assert result.exit_code != 0
    assert "12" in result.output
    with app.app_context():
        assert db.session.scalar(
            db.select(User).where(User.email == "recovered@example.com")
        ) is None

    result = runner.invoke(
        args=[
            "recover-admin",
            "--email",
            "recovered@example.com",
            "--password",
            "strong-recovery-password",
        ]
    )
    assert result.exit_code == 0
    assert "Administrator created" in result.output
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "recovered@example.com"))
        assert user.role == "admin"
        assert user.shop.name == "SulitShelf Admin Picks"


def test_trusted_cli_can_reset_lost_two_factor_and_revoke_sessions(client, app):
    register(client, email="lost-device@example.com")
    enable_two_factor_for_user(app, email="lost-device@example.com")
    with app.app_context():
        original_version = db.session.scalar(
            db.select(User.session_version).where(User.email == "lost-device@example.com")
        )

    result = app.test_cli_runner().invoke(
        args=["reset-two-factor", "--email", "lost-device@example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "Two-factor authentication is disabled" in result.output
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "lost-device@example.com"))
        assert not user.two_factor_enabled
        assert user.session_version == original_version + 1
        assert db.session.scalar(db.select(db.func.count(TwoFactorRecoveryCode.id))) == 0
        assert db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "security.two_factor_recovered")
        ) is not None


def test_forgot_password_is_generic_and_stores_only_a_token_digest(client, app, monkeypatch):
    register(client, email="recovery@example.com", password="original-password-2026")
    client.post("/logout")
    app.config["PASSWORD_RESET_ENABLED"] = True
    delivered = []

    def capture_email(user, reset_url, reset_id):
        delivered.append((user.email, reset_url, reset_id))
        return "resend"

    monkeypatch.setattr("app.auth.routes.send_password_reset_email", capture_email)
    known = client.post("/forgot-password", data={"email": "recovery@example.com"})
    unknown = client.post("/forgot-password", data={"email": "unknown@example.com"})

    assert known.status_code == 200
    assert unknown.status_code == 200
    assert b"If an active SulitShelf account matches" in known.data
    assert b"If an active SulitShelf account matches" in unknown.data
    assert len(delivered) == 1
    raw_token = parse_qs(urlparse(delivered[0][1]).query)["token"][0]
    with app.app_context():
        reset = db.session.scalar(db.select(PasswordResetToken))
        assert reset.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert raw_token not in reset.token_hash


def test_password_reset_is_single_use_and_revokes_existing_sessions(client, app, monkeypatch):
    register(client, email="reset@example.com", password="original-password-2026")
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "reset@example.com"))
        original_version = user.session_version
    client.post("/logout")
    app.config["PASSWORD_RESET_ENABLED"] = True
    delivered = []
    monkeypatch.setattr(
        "app.auth.routes.send_password_reset_email",
        lambda _user, reset_url, _reset_id: delivered.append(reset_url) or "resend",
    )

    client.post("/forgot-password", data={"email": "reset@example.com"})
    token = parse_qs(urlparse(delivered[0]).query)["token"][0]
    response = client.post(
        "/reset-password",
        data={
            "token": token,
            "password": "replacement-password-2026",
            "password_confirmation": "replacement-password-2026",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Your password was changed" in response.data
    assert client.get(f"/reset-password?token={token}").status_code == 400
    assert client.post(
        "/login",
        data={"email": "reset@example.com", "password": "original-password-2026"},
    ).status_code == 401
    assert client.post(
        "/login",
        data={"email": "reset@example.com", "password": "replacement-password-2026"},
    ).status_code == 302
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "reset@example.com"))
        assert user.session_version == original_version + 1
        assert all(item.used_at is not None for item in user.password_reset_tokens)


def test_expired_password_reset_token_is_rejected(client, app):
    app.config["PASSWORD_RESET_ENABLED"] = True
    raw_token = "A" * 43
    with app.app_context():
        user = User(email="expired@example.com", display_name="Expired User")
        user.set_password("original-password-2026")
        user.shop = Shop(
            name="Expired Shelf",
            slug="expired-shelf",
            subscription_ends_at=utcnow() + timedelta(days=36500),
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            PasswordResetToken(
                user=user,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=utcnow() - timedelta(minutes=1),
            )
        )
        db.session.commit()

    response = client.get(f"/reset-password?token={raw_token}")
    assert response.status_code == 400
    assert b"This reset link is invalid" in response.data


def test_email_provider_fails_over_from_resend_to_mailersend(app, monkeypatch):
    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(503 if url == email_service.RESEND_ENDPOINT else 202)

    with app.app_context():
        app.config.update(
            RESEND_API_KEY="re_test",
            MAILERSEND_API_TOKEN="mlsn.test",
            MAIL_FROM_EMAIL="no-reply@sulitshelf.ph",
            MAIL_FROM_NAME="SulitShelf PH",
            EMAIL_PROVIDER="auto",
            EMAIL_FAILOVER_ENABLED=True,
        )
        user = User(email="delivery@example.com", display_name="Delivery User")
        monkeypatch.setattr(email_service.requests, "post", fake_post)
        provider = email_service.send_password_reset_email(
            user,
            "https://sulitshelf.ph/reset-password?token=secret-test-token",
            42,
        )

    assert provider == "mailersend"
    assert [call[0] for call in calls] == [email_service.RESEND_ENDPOINT, email_service.MAILERSEND_ENDPOINT]
    assert calls[0][1]["headers"]["Authorization"] == "Bearer re_test"
    assert calls[1][1]["headers"]["Authorization"] == "Bearer mlsn.test"


def activate_pro(app, email="promoter@example.com"):
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == email))
        user.shop.plan_key = "pro"
        user.shop.subscription_status = "active"
        user.shop.subscription_source = "manual"
        user.shop.subscription_ends_at = utcnow() + timedelta(days=30)
        db.session.commit()


def test_pro_studio_renders_enhanced_import_and_branding_workspaces(client, app):
    register(client)
    activate_pro(app)

    response = client.get("/studio/?tab=growth")
    assert response.status_code == 200
    assert b"growth-import-heading" in response.data
    assert response.data.count(b"data-csv-picker") == 2
    assert response.data.count(b'data-max-bytes="2000000"') == 2
    assert b'name="rights_confirmed"' in response.data
    assert b'name="marketplace"' in response.data
    assert b"commission-summary-panel" in response.data

    settings = client.get("/studio/?tab=settings")
    assert settings.status_code == 200
    assert b"data-branding-form" in settings.data
    assert b"storefront-live-preview" in settings.data
    assert b'name="branding_theme"' in settings.data
    assert b'name="branding_logo"' in settings.data
    assert b'name="branding_banner"' in settings.data
    assert b'name="hide_platform_branding"' in settings.data


def test_pro_bulk_product_import_is_validated_and_uses_safe_placeholders(client, app):
    register(client)
    activate_pro(app)
    csv_data = (
        "name,description,why_sulit,best_for,department,affiliate_url,price,badge\n"
        "Student Power Bank,A compact power bank for school and daily travel.,Useful backup power at a student price.,Students,Tech & Gadgets,https://shopee.ph/student-power-bank?af=test,499.00,Under P500\n"
    ).encode()
    response = client.post(
        "/studio/products/bulk",
        data={"csv": (BytesIO(csv_data), "products.csv"), "rights_confirmed": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Imported 1 products" in response.data
    with app.app_context():
        product = db.session.scalar(db.select(Product).where(Product.name == "Student Power Bank"))
        assert product.image_name == BUILTIN_PRODUCT_IMAGE
        assert product.health_status == "needs_attention"
        assert detect_marketplace(product.affiliate_url) == "shopee"

    duplicate = client.post(
        "/studio/products/bulk",
        data={"csv": (BytesIO(csv_data), "products.csv"), "rights_confirmed": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"already exists" in duplicate.data


def test_free_user_cannot_call_pro_bulk_import_directly(client, app):
    register(client)
    response = client.post(
        "/studio/products/bulk",
        data={"csv": (BytesIO(b"name\nExample"), "products.csv"), "rights_confirmed": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"included with Pro" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Product.id))) == 0


def test_commission_import_hashes_references_and_blocks_duplicate_files(client, app):
    register(client)
    activate_pro(app)
    create_product(app)
    csv_data = (
        "order_date,order_reference,product_name,affiliate_url,order_value,commission,status\n"
        "2026-07-18,PRIVATE-ORDER-123,Sulit Power Bank,https://shopee.ph/sulit-power-bank?affiliate_id=test,499.00,24.95,approved\n"
    ).encode()
    response = client.post(
        "/studio/commissions/import",
        data={"marketplace": "shopee", "csv": (BytesIO(csv_data), "commissions.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Imported 1 commission rows" in response.data
    with app.app_context():
        commission_import = db.session.scalar(db.select(CommissionImport))
        entry = db.session.scalar(db.select(CommissionEntry))
        assert commission_import.total_commission_cents == 2495
        assert entry.commission_cents == 2495
        assert entry.product is not None
        assert "PRIVATE-ORDER-123" not in repr(entry.__dict__)
        assert len(entry.row_hash) == 64

    duplicate = client.post(
        "/studio/commissions/import",
        data={"marketplace": "shopee", "csv": (BytesIO(csv_data), "commissions.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"exact commission file was already imported" in duplicate.data


def test_pending_commission_rows_are_imported_but_excluded_from_approved_totals(client, app):
    register(client)
    activate_pro(app)
    csv_data = (
        "order_date,order_reference,product_name,affiliate_url,order_value,commission,status\n"
        "2026-07-18,PENDING-ORDER-1,Pending Product,https://shopee.ph/pending,199.00,9.95,processing\n"
    ).encode()
    response = client.post(
        "/studio/commissions/import",
        data={"marketplace": "shopee", "csv": (BytesIO(csv_data), "pending.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        commission_import = db.session.scalar(db.select(CommissionImport))
        entry = db.session.scalar(db.select(CommissionEntry))
        assert entry.status == "pending"
        assert commission_import.total_order_value_cents == 0
        assert commission_import.total_commission_cents == 0


def test_hourly_impressions_and_clicks_store_only_aggregate_device_data(client, app):
    register(client)
    product_id = create_product(app)
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile)"}
    assert client.post(
        "/events/impressions",
        json={"product_ids": [product_id], "source": "mall"},
        headers=headers,
    ).json == {"recorded": 1}
    client.get(f"/out/{product_id}?source=mall", headers=headers)
    with app.app_context():
        metric = db.session.scalar(db.select(ProductMetricHourly))
        assert metric.impressions == 1
        assert metric.clicks == 1
        assert metric.device == "mobile"
        assert metric.source == "mall"
        assert not hasattr(metric, "ip_address")
        assert not hasattr(metric, "user_agent")


def test_product_health_scan_flags_placeholder_and_accepts_replacement_image(client, app):
    register(client)
    activate_pro(app)
    csv_data = (
        "name,description,why_sulit,best_for,department,affiliate_url,price,badge\n"
        "Health Product,A valid product description for this health test.,A practical option for everyday use.,Students,Tech & Gadgets,https://shopee.ph/health-product,99.00,\n"
    ).encode()
    client.post(
        "/studio/products/bulk",
        data={"csv": (BytesIO(csv_data), "products.csv"), "rights_confirmed": "yes"},
        content_type="multipart/form-data",
    )
    with app.app_context():
        product = db.session.scalar(db.select(Product).where(Product.name == "Health Product"))
        product_id = product.id
    client.post("/studio/products/health-scan")
    with app.app_context():
        product = db.session.get(Product, product_id)
        assert product.health_status == "needs_attention"
        assert "image" in product.health_note.lower()

    response = client.post(
        f"/studio/products/{product_id}/image",
        data={"image": png_upload(name="replacement.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Product image updated" in response.data
    with app.app_context():
        product = db.session.get(Product, product_id)
        assert product.image_name.endswith(".webp")
        assert product.health_status == "healthy"


def test_pro_branding_is_tenant_scoped_and_rendered_publicly(client, app):
    register(client)
    activate_pro(app)
    response = client.post(
        "/studio/shop",
        data={
            "name": "Branded Shelf",
            "slug": "branded-shelf",
            "bio": "Curated affordable products for students and commuters.",
            "branding_theme": "purple",
            "branding_tagline": "Honest student finds",
            "hide_platform_branding": "yes",
            "branding_logo": png_upload(name="logo.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Shop settings updated" in response.data
    public = client.get("/shop/branded-shelf")
    assert b"shop-theme-purple" in public.data
    assert b"Honest student finds" in public.data
    assert b"Storefront powered by" not in public.data


def test_public_growth_surfaces_are_safe_and_discoverable(client, app):
    register(client)
    product_id = create_product(app)
    client.post("/logout")
    product_api = client.get(f"/api/products?ids={product_id},invalid,999999")
    assert product_api.status_code == 200
    assert [item["id"] for item in product_api.json["products"]] == [product_id]
    assert client.get("/saved").status_code == 200
    assert client.get("/service-worker.js").headers["Service-Worker-Allowed"] == "/"
    manifest = client.get("/static/manifest.json").json
    assert {icon["sizes"] for icon in manifest["icons"]} >= {"192x192", "512x512"}
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/static/images/apple-touch-icon.png").status_code == 200
    assert client.get("/static/images/brand-icon.webp").status_code == 200
    pwa_home = client.get("/?source=pwa")
    assert b'data-impression-source="pwa"' in pwa_home.data
    assert b'property="og:title"' in pwa_home.data
    assert b'property="og:site_name" content="SulitShelf PH"' in pwa_home.data
    assert b'rel="apple-touch-icon"' in pwa_home.data
    assert b'sizes="48x48"' in pwa_home.data
    assert b'images/brand-icon.webp' in pwa_home.data
    assert b'"@type": "Organization"' in pwa_home.data
    assert b'rel="canonical"' in pwa_home.data
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert f"/product/{product_id}".encode() in sitemap.data
    assert b"/sitemap.xml" in client.get("/robots.txt").data
    detail = client.get(f"/product/{product_id}")
    assert b"application/ld+json" in detail.data
    assert b'"priceCurrency": "PHP"' in detail.data
