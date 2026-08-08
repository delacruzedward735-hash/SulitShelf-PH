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
    session_version = db.Column(db.Integer, nullable=False, default=1)
    two_factor_secret_ciphertext = db.Column(db.String(512))
    two_factor_enabled_at = db.Column(db.DateTime(timezone=True))
    two_factor_last_counter = db.Column(db.BigInteger)
    shop = db.relationship("Shop", back_populates="owner", uselist=False, cascade="all, delete-orphan")
    oauth_identities = db.relationship("OAuthIdentity", back_populates="user", cascade="all, delete-orphan")
    password_reset_tokens = db.relationship(
        "PasswordResetToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    two_factor_recovery_codes = db.relationship(
        "TwoFactorRecoveryCode",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    received_crm_messages = db.relationship(
        "CRMMessage",
        foreign_keys="CRMMessage.recipient_id",
        back_populates="recipient",
        cascade="all, delete-orphan",
    )
    sent_crm_messages = db.relationship(
        "CRMMessage",
        foreign_keys="CRMMessage.sender_id",
        back_populates="sender",
    )

    @property
    def is_active(self):
        return self.is_active_account

    @property
    def is_admin(self):
        return self.role == "admin"

    def get_id(self):
        return f"{self.id}:{self.session_version}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="scrypt")

    def set_unusable_password(self):
        self.password_hash = "!oauth-only"

    @property
    def has_usable_password(self):
        return bool(self.password_hash and not self.password_hash.startswith("!"))

    def check_password(self, password):
        return self.has_usable_password and check_password_hash(self.password_hash, password)

    @property
    def two_factor_enabled(self):
        return bool(self.two_factor_enabled_at and self.two_factor_secret_ciphertext)


class CRMMessage(db.Model):
    """An administrator message delivered to a promoter's private inbox."""

    __tablename__ = "crm_message"

    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), index=True)
    sender_email = db.Column(db.String(254), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)
    email_requested = db.Column(db.Boolean, nullable=False, default=False)
    email_status = db.Column(db.String(20), nullable=False, default="not_requested", index=True)
    email_provider = db.Column(db.String(20))
    read_at = db.Column(db.DateTime(timezone=True), index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    recipient = db.relationship("User", foreign_keys=[recipient_id], back_populates="received_crm_messages")
    sender = db.relationship("User", foreign_keys=[sender_id], back_populates="sent_crm_messages")


class PasswordResetToken(db.Model):
    """A one-time password reset grant. Only the SHA-256 token digest is stored."""

    __tablename__ = "password_reset_token"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    user = db.relationship("User", back_populates="password_reset_tokens")


class TwoFactorRecoveryCode(db.Model):
    """A one-time 2FA recovery code stored only as a keyed digest."""

    __tablename__ = "two_factor_recovery_code"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True), index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    user = db.relationship("User", back_populates="two_factor_recovery_codes")


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
    subscription_customer_id = db.Column(db.String(160), index=True)
    subscription_product_id = db.Column(db.String(120))
    subscription_price_cents = db.Column(db.Integer)
    subscription_event_at = db.Column(db.DateTime(timezone=True))
    subscription_ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=False, index=True)
    branding_theme = db.Column(db.String(20), nullable=False, default="orange")
    branding_tagline = db.Column(db.String(120), nullable=False, default="")
    branding_logo_name = db.Column(db.String(160))
    branding_banner_name = db.Column(db.String(160))
    hide_platform_branding = db.Column(db.Boolean, nullable=False, default=False)
    owner = db.relationship("User", back_populates="shop")
    products = db.relationship("Product", back_populates="shop", cascade="all, delete-orphan")
    click_events = db.relationship("ClickEvent", back_populates="shop", cascade="all, delete-orphan")
    payment_submissions = db.relationship("PaymentSubmission", back_populates="shop", cascade="all, delete-orphan")
    donations = db.relationship("Donation", back_populates="shop", cascade="all, delete-orphan")
    metric_hours = db.relationship("ProductMetricHourly", back_populates="shop", cascade="all, delete-orphan")
    commission_imports = db.relationship("CommissionImport", back_populates="shop", cascade="all, delete-orphan")
    commission_entries = db.relationship("CommissionEntry", back_populates="shop", cascade="all, delete-orphan")
    __table_args__ = (
        db.Index("ix_shop_subscription_expiry", "plan_key", "subscription_status", "subscription_ends_at"),
    )


class Plan(TimestampMixin, db.Model):
    """Hosted-service access plans. A product_limit of zero means unlimited."""
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
    health_status = db.Column(db.String(24), nullable=False, default="unchecked", index=True)
    health_checked_at = db.Column(db.DateTime(timezone=True))
    health_note = db.Column(db.String(240), nullable=False, default="")
    shop = db.relationship("Shop", back_populates="products")
    click_events = db.relationship("ClickEvent", back_populates="product", cascade="all, delete-orphan")
    campaign_links = db.relationship("CampaignProduct", back_populates="product", cascade="all, delete-orphan")
    reports = db.relationship("ProductReport", back_populates="product", cascade="all, delete-orphan")
    metric_hours = db.relationship("ProductMetricHourly", back_populates="product", cascade="all, delete-orphan")
    commission_entries = db.relationship("CommissionEntry", back_populates="product")


class ProductMetricHourly(db.Model):
    """Privacy-friendly hourly aggregates; no visitor identifier, IP, or raw user agent."""

    __tablename__ = "product_metric_hourly"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False, index=True)
    period_start = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    source = db.Column(db.String(24), nullable=False, default="direct")
    device = db.Column(db.String(12), nullable=False, default="desktop")
    impressions = db.Column(db.Integer, nullable=False, default=0)
    clicks = db.Column(db.Integer, nullable=False, default=0)
    product = db.relationship("Product", back_populates="metric_hours")
    shop = db.relationship("Shop", back_populates="metric_hours")
    __table_args__ = (
        db.UniqueConstraint(
            "product_id", "period_start", "source", "device",
            name="uq_product_metric_hourly_bucket",
        ),
    )


class CommissionImport(db.Model):
    __tablename__ = "commission_import"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace = db.Column(db.String(20), nullable=False, index=True)
    original_filename = db.Column(db.String(160), nullable=False)
    content_hash = db.Column(db.String(64), nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    total_order_value_cents = db.Column(db.BigInteger, nullable=False, default=0)
    total_commission_cents = db.Column(db.BigInteger, nullable=False, default=0)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    shop = db.relationship("Shop", back_populates="commission_imports")
    entries = db.relationship("CommissionEntry", back_populates="commission_import", cascade="all, delete-orphan")
    __table_args__ = (
        db.UniqueConstraint("shop_id", "marketplace", "content_hash", name="uq_commission_import_file"),
    )


class CommissionEntry(db.Model):
    __tablename__ = "commission_entry"

    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(db.Integer, db.ForeignKey("commission_import.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="SET NULL"), index=True)
    marketplace = db.Column(db.String(20), nullable=False, index=True)
    row_hash = db.Column(db.String(64), nullable=False)
    occurred_on = db.Column(db.Date, nullable=False, index=True)
    product_name = db.Column(db.String(160), nullable=False)
    order_value_cents = db.Column(db.BigInteger, nullable=False, default=0)
    commission_cents = db.Column(db.BigInteger, nullable=False)
    status = db.Column(db.String(40), nullable=False, default="approved", index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    commission_import = db.relationship("CommissionImport", back_populates="entries")
    shop = db.relationship("Shop", back_populates="commission_entries")
    product = db.relationship("Product", back_populates="commission_entries")
    __table_args__ = (
        db.UniqueConstraint("shop_id", "row_hash", name="uq_commission_entry_shop_row"),
    )


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
        return [
            link.product
            for link in self.product_links
            if (
                link.product
                and link.product.status == "active"
                and link.product.shop
                and link.product.shop.owner
                and link.product.shop.owner.is_active_account
            )
        ]


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


class WalletReference(db.Model):
    """One e-wallet transaction reference may fund only one action."""
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(40), unique=True, nullable=False, index=True)
    purpose = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


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
