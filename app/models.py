from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="promoter")
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    shop = db.relationship("Shop", back_populates="owner", uselist=False, cascade="all, delete-orphan")
    oauth_identities = db.relationship("OAuthIdentity", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_active(self):
        return self.is_active_account

    @property
    def is_admin(self):
        return self.role == "admin"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="scrypt")

    def set_unusable_password(self):
        self.password_hash = "!oauth-only"

    @property
    def has_usable_password(self):
        return bool(self.password_hash and not self.password_hash.startswith("!"))

    def check_password(self, password):
        return self.has_usable_password and check_password_hash(self.password_hash, password)


class OAuthIdentity(TimestampMixin, db.Model):
    # Keep SQLAlchemy's acronym handling aligned with migration 0003.
    __tablename__ = "oauth_identity"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = db.Column(db.String(20), nullable=False)
    provider_user_id = db.Column(db.String(191), nullable=False)
    email_at_link = db.Column(db.String(254))
    last_used_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    user = db.relationship("User", back_populates="oauth_identities")
    __table_args__ = (
        db.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_identity_provider_user"),
        db.UniqueConstraint("user_id", "provider", name="uq_oauth_identity_user_provider"),
    )


class Shop(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)
    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)
    bio = db.Column(db.String(240), nullable=False, default="Curated finds worth checking out.")
    plan_key = db.Column(db.String(20), nullable=False, default="free")
    subscription_status = db.Column(db.String(30), nullable=False, default="free")
    subscription_source = db.Column(db.String(30))
    subscription_external_id = db.Column(db.String(160))
    subscription_ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=False, index=True)
    owner = db.relationship("User", back_populates="shop")
    products = db.relationship("Product", back_populates="shop", cascade="all, delete-orphan")
    click_events = db.relationship("ClickEvent", back_populates="shop", cascade="all, delete-orphan")
    payment_submissions = db.relationship("PaymentSubmission", back_populates="shop", cascade="all, delete-orphan")
    donations = db.relationship("Donation", back_populates="shop", cascade="all, delete-orphan")


class Plan(TimestampMixin, db.Model):
    """Legacy subscription configuration kept for migration compatibility."""
    key = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    price_cents = db.Column(db.Integer, nullable=False)
    product_limit = db.Column(db.Integer, nullable=False)
    dodo_product_id = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    updated_by = db.Column(db.String(254), nullable=False, default="system@sulitshelf.ph")


class Product(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    department = db.Column(db.String(60), nullable=False, index=True)
    marketplace = db.Column(db.String(20), nullable=False, index=True)
    affiliate_url = db.Column(db.Text, nullable=False)
    price_cents = db.Column(db.Integer, nullable=False)
    image_name = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    badge = db.Column(db.String(32))
    click_count = db.Column(db.Integer, nullable=False, default=0)
    why_sulit = db.Column(db.String(240), nullable=False, default="")
    best_for = db.Column(db.String(160), nullable=False, default="")
    price_checked_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    is_sponsored = db.Column(db.Boolean, nullable=False, default=False, index=True)
    shop = db.relationship("Shop", back_populates="products")
    click_events = db.relationship("ClickEvent", back_populates="product", cascade="all, delete-orphan")
    campaign_links = db.relationship("CampaignProduct", back_populates="product", cascade="all, delete-orphan")
    reports = db.relationship("ProductReport", back_populates="product", cascade="all, delete-orphan")


class ClickEvent(db.Model):
    """Privacy-friendly aggregate event; no IP address or visitor identifier is stored."""

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace = db.Column(db.String(20), nullable=False, index=True)
    source = db.Column(db.String(24), nullable=False, default="direct", index=True)
    occurred_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    product = db.relationship("Product", back_populates="click_events")
    shop = db.relationship("Shop", back_populates="click_events")


class Campaign(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(80), nullable=False)
    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)
    eyebrow = db.Column(db.String(50), nullable=False, default="CURATED COLLECTION")
    description = db.Column(db.String(240), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    product_links = db.relationship(
        "CampaignProduct",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="CampaignProduct.position",
    )

    @property
    def products(self):
        return [link.product for link in self.product_links if link.product and link.product.status == "active"]


class CampaignProduct(db.Model):
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="CASCADE"), primary_key=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    campaign = db.relationship("Campaign", back_populates="product_links")
    product = db.relationship("Product", back_populates="campaign_links")


class ProductReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = db.Column(db.String(30), nullable=False, index=True)
    details = db.Column(db.String(300), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime(timezone=True))
    reviewed_by = db.Column(db.String(254))
    product = db.relationship("Product", back_populates="reports")


class PlatformSettings(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True, default=1)
    gcash_account_name = db.Column(db.String(80), nullable=False, default="")
    gcash_number = db.Column(db.String(20), nullable=False, default="")
    gcash_qr_name = db.Column(db.String(160))
    wallet_provider = db.Column(db.String(40), nullable=False, default="GCash")
    donation_message = db.Column(db.String(240), nullable=False, default="Your support helps keep SulitShelf free and open source.")
    updated_by = db.Column(db.String(254), nullable=False, default="system@sulitshelf.ph")


class DonationTier(TimestampMixin, db.Model):
    key = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(160), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    dodo_product_id = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    updated_by = db.Column(db.String(254), nullable=False, default="system@sulitshelf.ph")


class Donation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False, index=True)
    tier_key = db.Column(db.String(20), db.ForeignKey("donation_tier.key", ondelete="SET NULL"), index=True)
    method = db.Column(db.String(20), nullable=False, index=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    reference_number = db.Column(db.String(40), unique=True, index=True)
    receipt_name = db.Column(db.String(160))
    dodo_payment_id = db.Column(db.String(160), unique=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    submitted_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime(timezone=True))
    reviewed_by = db.Column(db.String(254))
    review_note = db.Column(db.String(300))
    shop = db.relationship("Shop", back_populates="donations")
    tier = db.relationship("DonationTier")


class PaymentSubmission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_key = db.Column(db.String(20), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    reference_number = db.Column(db.String(40), unique=True, nullable=False, index=True)
    receipt_name = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    submitted_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime(timezone=True))
    reviewed_by = db.Column(db.String(254))
    review_note = db.Column(db.String(300))
    shop = db.relationship("Shop", back_populates="payment_submissions")


class WebhookEvent(db.Model):
    id = db.Column(db.String(160), primary_key=True)
    event_type = db.Column(db.String(80), nullable=False)
    processed_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_email = db.Column(db.String(254), nullable=False)
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(40), nullable=False)
    target_id = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
