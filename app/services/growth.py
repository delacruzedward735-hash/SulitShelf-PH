import csv
import hashlib
import hmac
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import current_app
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import Product, ProductMetricHourly, ProductReport, utcnow
from app.services.billing import aware
from app.services.catalog import DEPARTMENTS, detect_marketplace


BUILTIN_PRODUCT_IMAGE = "builtin:product-placeholder"
BRANDING_THEMES = {"orange", "purple", "blue", "green", "midnight"}

BULK_PRODUCT_TEMPLATE = """name,description,why_sulit,best_for,department,affiliate_url,price,badge
10000mAh Power Bank,A compact power bank for school and travel.,Useful backup power at a student-friendly price.,Students and commuters,Tech & Gadgets,https://shopee.ph/example-affiliate-link,499.00,Under P500
"""

COMMISSION_TEMPLATE = """order_date,order_reference,product_name,affiliate_url,order_value,commission,status
2026-07-18,EXAMPLE-ORDER-001,10000mAh Power Bank,https://shopee.ph/example-affiliate-link,499.00,24.95,approved
"""


class CSVImportError(ValueError):
    pass


def _read_csv(file, maximum_rows):
    if not file or not file.filename:
        raise CSVImportError("Choose a CSV file to import.")
    if not file.filename.lower().endswith(".csv"):
        raise CSVImportError("Only .csv files are accepted.")
    limit = current_app.config["CSV_IMPORT_MAX_BYTES"]
    data = file.read(limit + 1)
    if not data or len(data) > limit:
        raise CSVImportError(f"CSV files must be no larger than {limit // 1_000_000 or 1} MB.")
    if b"\x00" in data:
        raise CSVImportError("The CSV contains invalid binary data.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CSVImportError("Save the CSV as UTF-8 before importing it.") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise CSVImportError("The CSV needs a header row.")
    rows = []
    for row_number, row in enumerate(reader, start=2):
        if row_number > maximum_rows + 1:
            raise CSVImportError(f"The CSV may contain at most {maximum_rows} data rows.")
        if any(value and str(value).strip() for value in row.values()):
            rows.append((row_number, row))
    if not rows:
        raise CSVImportError("The CSV has no data rows.")
    return data, reader.fieldnames, rows, secure_filename(file.filename)[:160] or "import.csv"


def _normalized_header(value):
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _row_by_normalized_header(row):
    return {_normalized_header(key): (value or "").strip() for key, value in row.items() if key}


def _money_cents(value, *, allow_negative=False, required=False):
    cleaned = (value or "").strip().upper().replace("PHP", "").replace("₱", "").replace(",", "").replace(" ", "")
    if required and not cleaned:
        raise CSVImportError("A required money amount is missing.")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        amount = Decimal(cleaned or "0")
        if not amount.is_finite() or amount.as_tuple().exponent < -2:
            raise InvalidOperation
        cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise CSVImportError(f"Invalid money amount: {value!r}.") from None
    minimum = -100_000_000 if allow_negative else 0
    if not minimum <= cents <= 1_000_000_000:
        raise CSVImportError("A money amount is outside the accepted range.")
    return cents


def parse_bulk_products(file):
    _data, fieldnames, rows, filename = _read_csv(file, current_app.config["BULK_PRODUCT_MAX_ROWS"])
    required = {"name", "description", "why_sulit", "department", "affiliate_url", "price"}
    headers = {_normalized_header(item) for item in fieldnames}
    missing = sorted(required - headers)
    if missing:
        raise CSVImportError("Missing required columns: " + ", ".join(missing) + ".")

    department_lookup = {item.casefold(): item for item in DEPARTMENTS}
    parsed = []
    errors = []
    seen_urls = set()
    for row_number, source in rows:
        row = _row_by_normalized_header(source)
        name = row.get("name", "")
        description = row.get("description", "")
        why_sulit = row.get("why_sulit", "")
        best_for = row.get("best_for", "")
        department = department_lookup.get(row.get("department", "").casefold())
        affiliate_url = row.get("affiliate_url", "")
        marketplace = detect_marketplace(affiliate_url)
        try:
            price_cents = _money_cents(row.get("price", ""), required=True)
        except CSVImportError as error:
            errors.append(f"Row {row_number}: {error}")
            continue
        if not 3 <= len(name) <= 100:
            errors.append(f"Row {row_number}: product name must contain 3–100 characters.")
        elif not 12 <= len(description) <= 500:
            errors.append(f"Row {row_number}: description must contain 12–500 characters.")
        elif not 8 <= len(why_sulit) <= 240:
            errors.append(f"Row {row_number}: why_sulit must contain 8–240 characters.")
        elif len(best_for) > 160:
            errors.append(f"Row {row_number}: best_for is too long.")
        elif not department:
            errors.append(f"Row {row_number}: department does not match a SulitShelf department.")
        elif not marketplace:
            errors.append(f"Row {row_number}: affiliate_url is not an approved marketplace HTTPS URL.")
        elif affiliate_url in seen_urls:
            errors.append(f"Row {row_number}: duplicate affiliate_url in this CSV.")
        else:
            seen_urls.add(affiliate_url)
            parsed.append(
                {
                    "name": name,
                    "description": description,
                    "why_sulit": why_sulit,
                    "best_for": best_for,
                    "department": department,
                    "marketplace": marketplace,
                    "affiliate_url": affiliate_url,
                    "price_cents": price_cents,
                    "badge": row.get("badge", "")[:32] or None,
                }
            )
    if errors:
        preview = " ".join(errors[:8])
        suffix = f" Plus {len(errors) - 8} more errors." if len(errors) > 8 else ""
        raise CSVImportError(preview + suffix)
    return parsed, filename


COMMISSION_ALIASES = {
    "order_date": {"order_date", "date", "conversion_date", "conversion_time", "order_time", "purchase_time"},
    "order_reference": {"order_reference", "order_id", "order_no", "order_number", "conversion_id"},
    "product_name": {"product_name", "item_name", "product", "item"},
    "affiliate_url": {"affiliate_url", "product_url", "item_url", "url"},
    "order_value": {"order_value", "sales_amount", "item_price", "subtotal", "gmv"},
    "commission": {"commission", "commission_amount", "estimated_commission", "payout"},
    "status": {"status", "order_status", "conversion_status"},
}

COMMISSION_STATUS_GROUPS = {
    "approved": {"approved", "confirmed", "completed", "paid", "validated"},
    "pending": {"pending", "open", "processing", "under_review"},
    "rejected": {"rejected", "cancelled", "canceled", "invalid", "reversed", "refunded"},
}


def _commission_columns(fieldnames):
    normalized = {_normalized_header(value): value for value in fieldnames}
    resolved = {}
    for target, aliases in COMMISSION_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                resolved[target] = normalized[alias]
                break
    missing = [item for item in ("order_date", "order_reference", "product_name", "commission") if item not in resolved]
    if missing:
        raise CSVImportError("Missing commission columns: " + ", ".join(missing) + ". Download the SulitShelf template for the supported format.")
    return resolved


def _parse_date(value):
    cleaned = (value or "").strip()
    if not cleaned:
        raise CSVImportError("An order date is missing.")
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    raise CSVImportError(f"Invalid order date: {cleaned!r}. Use YYYY-MM-DD.")


def _commission_status(value):
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", (value or "approved").lower()).strip("_")[:40] or "approved"
    for normalized, values in COMMISSION_STATUS_GROUPS.items():
        if cleaned in values:
            return normalized
    return cleaned


def parse_commissions(file, marketplace, shop):
    if marketplace not in {"shopee", "lazada", "tiktok"}:
        raise CSVImportError("Choose Shopee, Lazada, or TikTok Shop.")
    data, fieldnames, rows, filename = _read_csv(file, current_app.config["COMMISSION_MAX_ROWS"])
    columns = _commission_columns(fieldnames)
    products = list(db.session.scalars(db.select(Product).where(Product.shop_id == shop.id)))
    by_url = {item.affiliate_url: item for item in products}
    name_groups = {}
    for item in products:
        name_groups.setdefault(item.name.casefold(), []).append(item)
    parsed = []
    errors = []
    seen_hashes = set()
    for row_number, raw in rows:
        row = {target: (raw.get(source) or "").strip() for target, source in columns.items()}
        reference = row.get("order_reference", "")
        product_name = row.get("product_name", "")
        if not 1 <= len(reference) <= 200 or not 1 <= len(product_name) <= 160:
            errors.append(f"Row {row_number}: order reference or product name is missing or too long.")
            continue
        try:
            occurred_on = _parse_date(row.get("order_date", ""))
            if occurred_on < date(2000, 1, 1) or occurred_on > date.today() + timedelta(days=1):
                raise CSVImportError("Order date must be between 2000-01-01 and tomorrow.")
            order_value_cents = _money_cents(row.get("order_value", "0"))
            commission_cents = _money_cents(row.get("commission", ""), allow_negative=True, required=True)
        except CSVImportError as error:
            errors.append(f"Row {row_number}: {error}")
            continue
        status = _commission_status(row.get("status", "approved"))
        reference_hash = hmac.new(
            current_app.config["COMMISSION_HASH_KEY"].encode(),
            f"{shop.id}:{marketplace}:{reference}".encode(),
            hashlib.sha256,
        ).hexdigest()
        row_material = "|".join(
            [reference_hash, product_name.casefold(), occurred_on.isoformat(), str(order_value_cents), str(commission_cents), status]
        )
        row_hash = hashlib.sha256(row_material.encode()).hexdigest()
        if row_hash in seen_hashes:
            continue
        seen_hashes.add(row_hash)
        product = by_url.get(row.get("affiliate_url", ""))
        if not product:
            matches = name_groups.get(product_name.casefold(), [])
            product = matches[0] if len(matches) == 1 else None
        parsed.append(
            {
                "product": product,
                "row_hash": row_hash,
                "occurred_on": occurred_on,
                "product_name": product_name,
                "order_value_cents": order_value_cents,
                "commission_cents": commission_cents,
                "status": status,
            }
        )
    if errors:
        preview = " ".join(errors[:8])
        suffix = f" Plus {len(errors) - 8} more errors." if len(errors) > 8 else ""
        raise CSVImportError(preview + suffix)
    if not parsed:
        raise CSVImportError("No new commission rows were found in the CSV.")
    return data, parsed, filename


def device_bucket(user_agent):
    value = (user_agent or "").lower()
    if any(marker in value for marker in ("ipad", "tablet", "kindle")):
        return "tablet"
    if any(marker in value for marker in ("mobile", "android", "iphone", "ipod")):
        return "mobile"
    return "desktop"


def record_hourly_metric(product, source, device, *, impressions=0, clicks=0):
    period_start = utcnow().replace(minute=0, second=0, microsecond=0)
    values = {
        "product_id": product.id,
        "shop_id": product.shop_id,
        "period_start": period_start,
        "source": source,
        "device": device,
        "impressions": impressions,
        "clicks": clicks,
    }
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(ProductMetricHourly).values(**values)
        statement = statement.on_conflict_do_update(
            constraint="uq_product_metric_hourly_bucket",
            set_={
                "impressions": ProductMetricHourly.impressions + impressions,
                "clicks": ProductMetricHourly.clicks + clicks,
            },
        )
        db.session.execute(statement)
        return
    if dialect == "sqlite":
        statement = sqlite_insert(ProductMetricHourly).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["product_id", "period_start", "source", "device"],
            set_={
                "impressions": ProductMetricHourly.impressions + impressions,
                "clicks": ProductMetricHourly.clicks + clicks,
            },
        )
        db.session.execute(statement)
        return
    metric = db.session.scalar(
        db.select(ProductMetricHourly).where(
            ProductMetricHourly.product_id == product.id,
            ProductMetricHourly.period_start == period_start,
            ProductMetricHourly.source == source,
            ProductMetricHourly.device == device,
        ).with_for_update()
    )
    if not metric:
        metric = ProductMetricHourly(**values)
        db.session.add(metric)
    else:
        metric.impressions += impressions
        metric.clicks += clicks


def scan_product_health(shop):
    products = list(db.session.scalars(db.select(Product).where(Product.shop_id == shop.id)))
    product_ids = [item.id for item in products]
    broken_reports = set()
    if product_ids:
        broken_reports = set(
            db.session.scalars(
                db.select(ProductReport.product_id).where(
                    ProductReport.product_id.in_(product_ids),
                    ProductReport.reason == "broken_link",
                    ProductReport.status == "pending",
                )
            )
        )
    stale_before = utcnow() - timedelta(days=current_app.config["PRODUCT_STALE_DAYS"])
    summary = {"healthy": 0, "needs_attention": 0}
    for product in products:
        reasons = []
        checked_at = aware(product.price_checked_at)
        if product.image_name == BUILTIN_PRODUCT_IMAGE:
            reasons.append("Add a real product image")
        if not checked_at or checked_at < stale_before:
            reasons.append("Refresh the listed price")
        if not detect_marketplace(product.affiliate_url):
            reasons.append("Replace the invalid marketplace link")
        if product.id in broken_reports:
            reasons.append("Review a visitor broken-link report")
        product.health_status = "needs_attention" if reasons else "healthy"
        product.health_note = "; ".join(reasons)[:240]
        product.health_checked_at = utcnow()
        summary[product.health_status] += 1
    return summary
