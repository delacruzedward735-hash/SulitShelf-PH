from datetime import timedelta
from io import BytesIO

import cloudinary.uploader
import pytest
from PIL import Image

from app.extensions import db
from app.models import (
    AuditLog,
    Campaign,
    ClickEvent,
    Donation,
    OAuthIdentity,
    PlatformSettings,
    Product,
    ProductReport,
    Shop,
    User,
    utcnow,
)
from app.services.catalog import detect_marketplace
from app.services.storage import UploadError, media_url, path_for, save_image


def register(client, email="promoter@example.com", password="very-secure-password"):
    return client.post(
        "/register",
        data={"email": email, "display_name": "Test Promoter", "password": password},
        follow_redirects=True,
    )


def login(client, email="promoter@example.com", password="very-secure-password"):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=True)


def png_upload(name="receipt.png", size=(32, 32)):
    output = BytesIO()
    Image.new("RGB", size, "#ee4d2d").save(output, format="PNG")
    output.seek(0)
    output.filename = name
    return output, name


def test_home_and_registration_are_free(client, app):
    response = client.get("/")
    assert response.status_code == 200
    assert b"No subscription" in response.data

    response = register(client)
    assert response.status_code == 200
    assert b"Promoter Studio" in response.data
    assert b"Free forever" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "promoter@example.com"))
        assert user.shop.plan_key == "free"
        assert user.shop.subscription_status == "free"


def test_marketplace_allowlist_rejects_lookalikes():
    assert detect_marketplace("https://shopee.ph/item") == "shopee"
    assert detect_marketplace("https://evilshopee.ph/item") is None
    assert detect_marketplace("http://shopee.ph/item") is None
    assert detect_marketplace("https://shopee.ph.evil.example/item") is None


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
    monkeypatch.setattr("app.auth.routes.oauth.create_client", lambda provider: FakeGoogleClient())

    response = client.get("/oauth/google/callback", follow_redirects=True)
    assert response.status_code == 200
    assert b"Signed in securely with Google" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "social@example.com"))
        identity = db.session.scalar(db.select(OAuthIdentity).where(OAuthIdentity.user_id == user.id))
        assert not user.has_usable_password
        assert identity.provider == "google"
        assert identity.provider_user_id == "google-123"
        assert not hasattr(identity, "access_token")


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
    assert b"Check all product details" in response.data
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
