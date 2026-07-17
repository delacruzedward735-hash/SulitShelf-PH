from flask import current_app

from app.extensions import db
from app.models import Campaign, DonationTier, PlatformSettings

DEFAULT_DONATION_TIERS = {
    "coffee": ("Buy us a coffee", "A small thank-you that keeps development moving.", 4900),
    "supporter": ("Project supporter", "Help with hosting, maintenance, and improvements.", 14900),
    "sponsor": ("Open-source sponsor", "Make a larger contribution to SulitShelf's future.", 49900),
}

DEFAULT_CAMPAIGNS = {
    "student-essentials-under-500": (
        "Student Essentials Under ₱500",
        "SULIT FOR STUDENTS",
        "Affordable study, desk, and everyday tech finds selected for Filipino students.",
        10,
    ),
    "brownout-survival-kit": (
        "Brownout Survival Kit",
        "READY WHEN POWER IS OUT",
        "Rechargeable lights, fans, power banks, and practical emergency essentials.",
        20,
    ),
    "budget-desk-setup": (
        "Budget Desk Setup",
        "WORK AND STUDY BETTER",
        "Useful accessories for a clean, comfortable setup without the premium price.",
        30,
    ),
}


def ensure_defaults():
    changed = False
    for key, (name, description, amount) in DEFAULT_DONATION_TIERS.items():
        if not db.session.get(DonationTier, key):
            db.session.add(DonationTier(key=key, name=name, description=description, amount_cents=amount))
            changed = True
    if not db.session.get(PlatformSettings, 1):
        db.session.add(PlatformSettings(id=1))
        changed = True
    for slug, (title, eyebrow, description, sort_order) in DEFAULT_CAMPAIGNS.items():
        if not db.session.scalar(db.select(Campaign).where(Campaign.slug == slug)):
            db.session.add(
                Campaign(
                    title=title,
                    slug=slug,
                    eyebrow=eyebrow,
                    description=description,
                    sort_order=sort_order,
                )
            )
            changed = True
    if changed:
        db.session.commit()


def active_subscription(_shop):
    """Compatibility shim: SulitShelf is now free and has no subscription gate."""
    return True


def dodo_endpoint():
    mode = current_app.config["DODO_PAYMENTS_ENVIRONMENT"]
    return "https://live.dodopayments.com" if mode == "live_mode" else "https://test.dodopayments.com"
