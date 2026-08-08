import re
import unicodedata
from urllib.parse import urlparse

DEPARTMENTS = [
    "Tech & Gadgets", "Fashion", "Beauty & Care", "Home & Living",
    "Kitchen & Dining", "School & Office", "Gaming", "Sports & Outdoors",
    "Automotive & Moto", "Mom, Baby & Kids", "Pet Supplies", "Brownout Essentials",
]

MARKETPLACE_HOSTS = {
    "shopee": ("shopee.ph",),
    "lazada": ("lazada.com.ph",),
    "tiktok": ("tiktok.com",),
}


def detect_marketplace(raw_url):
    if not raw_url or len(raw_url) > 2048 or "\r" in raw_url or "\n" in raw_url:
        return None
    try:
        parsed = urlparse(raw_url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or port not in {None, 443}:
        return None
    host = parsed.hostname.lower().rstrip(".")
    for marketplace, roots in MARKETPLACE_HOSTS.items():
        if any(host == root or host.endswith(f".{root}") for root in roots):
            return marketplace
    return None


def slugify(value):
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", normalized))[:56]


def php(cents):
    return f"₱{cents / 100:,.2f}"
