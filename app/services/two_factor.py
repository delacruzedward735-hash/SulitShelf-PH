import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from app.extensions import db
from app.models import TwoFactorRecoveryCode, utcnow


ISSUER = "SulitShelf PH"
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class TwoFactorError(ValueError):
    pass


def configuration_issue():
    """Return a safe configuration problem for request-time diagnostics."""
    configured = str(current_app.config.get("TWO_FACTOR_ENCRYPTION_KEY", "")).strip()
    if not configured:
        return "TWO_FACTOR_ENCRYPTION_KEY is not configured"
    if configured == str(current_app.config.get("SECRET_KEY", "")):
        return "TWO_FACTOR_ENCRYPTION_KEY must be separate from SECRET_KEY"
    return None


def _master_key_bytes():
    configured = str(current_app.config.get("TWO_FACTOR_ENCRYPTION_KEY", "")).encode()
    if not configured:
        raise TwoFactorError("Two-factor encryption is not configured.")
    return configured


def _fernet():
    derived = hashlib.sha256(b"sulitshelf:two-factor:v1:" + _master_key_bytes()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(secret):
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(ciphertext, *, ttl=None):
    try:
        return _fernet().decrypt(str(ciphertext).encode(), ttl=ttl).decode()
    except (InvalidToken, TypeError, ValueError) as error:
        raise TwoFactorError("The two-factor secret could not be decrypted.") from error


def generate_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def format_secret(secret):
    return " ".join(secret[index:index + 4] for index in range(0, len(secret), 4))


def provisioning_uri(secret, email):
    label = quote(f"{ISSUER}:{email}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": ISSUER,
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"


def totp_code(secret, *, for_time=None, counter=None):
    if counter is None:
        timestamp = time.time() if for_time is None else float(for_time)
        counter = int(timestamp // TOTP_PERIOD_SECONDS)
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def matching_totp_counter(secret, supplied_code, *, at_time=None, window=1):
    code = re.sub(r"\s+", "", supplied_code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    timestamp = time.time() if at_time is None else float(at_time)
    current_counter = int(timestamp // TOTP_PERIOD_SECONDS)
    for offset in range(-window, window + 1):
        candidate_counter = current_counter + offset
        if candidate_counter >= 0 and hmac.compare_digest(totp_code(secret, counter=candidate_counter), code):
            return candidate_counter
    return None


def normalize_recovery_code(code):
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def _recovery_digest(user_id, code):
    normalized = normalize_recovery_code(code)
    message = f"recovery:{user_id}:{normalized}".encode()
    return hmac.new(_master_key_bytes(), message, hashlib.sha256).hexdigest()


def _new_recovery_code():
    compact = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(16))
    return "-".join(compact[index:index + 4] for index in range(0, 16, 4))


def replace_recovery_codes(user, count=RECOVERY_CODE_COUNT):
    db.session.query(TwoFactorRecoveryCode).filter_by(user_id=user.id).delete(synchronize_session=False)
    raw_codes = [_new_recovery_code() for _ in range(count)]
    db.session.add_all(
        TwoFactorRecoveryCode(user_id=user.id, code_hash=_recovery_digest(user.id, code))
        for code in raw_codes
    )
    return raw_codes


def remaining_recovery_codes(user_id):
    return db.session.scalar(
        db.select(db.func.count(TwoFactorRecoveryCode.id)).where(
            TwoFactorRecoveryCode.user_id == user_id,
            TwoFactorRecoveryCode.used_at.is_(None),
        )
    ) or 0


def verify_and_consume_code(user, supplied_code, *, allow_recovery=True):
    if not user.two_factor_enabled:
        return None
    compact = re.sub(r"\s+", "", supplied_code or "")
    if re.fullmatch(r"\d{6}", compact):
        try:
            secret = decrypt_secret(user.two_factor_secret_ciphertext)
        except TwoFactorError:
            current_app.logger.exception("Unable to decrypt two-factor secret user_id=%s", user.id)
            return None
        counter = matching_totp_counter(secret, compact)
        if counter is None or (user.two_factor_last_counter is not None and counter <= user.two_factor_last_counter):
            return None
        user.two_factor_last_counter = counter
        return "authenticator"

    normalized = normalize_recovery_code(compact)
    if not allow_recovery or not re.fullmatch(r"[2-9A-HJ-NP-Z]{16}", normalized):
        return None
    code_hash = _recovery_digest(user.id, normalized)
    recovery = db.session.scalar(
        db.select(TwoFactorRecoveryCode)
        .where(
            TwoFactorRecoveryCode.user_id == user.id,
            TwoFactorRecoveryCode.code_hash == code_hash,
            TwoFactorRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if not recovery:
        return None
    recovery.used_at = utcnow()
    return "recovery"


def clear_two_factor(user):
    db.session.query(TwoFactorRecoveryCode).filter_by(user_id=user.id).delete(synchronize_session=False)
    user.two_factor_secret_ciphertext = None
    user.two_factor_enabled_at = None
    user.two_factor_last_counter = None
