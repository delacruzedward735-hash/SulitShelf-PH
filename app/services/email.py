import hashlib

import requests
from flask import current_app, render_template


RESEND_ENDPOINT = "https://api.resend.com/emails"
MAILERSEND_ENDPOINT = "https://api.mailersend.com/v1/email"


class EmailDeliveryError(RuntimeError):
    """Raised when every configured transactional email provider fails."""


def _provider_order():
    configured = {
        "resend": bool(current_app.config["RESEND_API_KEY"]),
        "mailersend": bool(current_app.config["MAILERSEND_API_TOKEN"]),
    }
    preferred = current_app.config["EMAIL_PROVIDER"]
    order = ["resend", "mailersend"] if preferred == "auto" else [preferred]
    if preferred in {"resend", "mailersend"} and current_app.config["EMAIL_FAILOVER_ENABLED"]:
        order.append("mailersend" if preferred == "resend" else "resend")
    return [provider for provider in order if configured.get(provider)]


def _resend_payload(to_email, subject, text_body, html_body):
    return {
        "from": f"{current_app.config['MAIL_FROM_NAME']} <{current_app.config['MAIL_FROM_EMAIL']}>",
        "to": [to_email],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }


def _mailersend_payload(to_email, subject, text_body, html_body):
    return {
        "from": {
            "email": current_app.config["MAIL_FROM_EMAIL"],
            "name": current_app.config["MAIL_FROM_NAME"],
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }


def _send_with_resend(to_email, subject, text_body, html_body, idempotency_key):
    response = requests.post(
        RESEND_ENDPOINT,
        headers={
            "Authorization": f"Bearer {current_app.config['RESEND_API_KEY']}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
        json=_resend_payload(to_email, subject, text_body, html_body),
        timeout=current_app.config["EMAIL_TIMEOUT_SECONDS"],
    )
    if response.status_code != 200:
        raise EmailDeliveryError(f"Resend returned HTTP {response.status_code}.")


def _send_with_mailersend(to_email, subject, text_body, html_body, _idempotency_key):
    response = requests.post(
        MAILERSEND_ENDPOINT,
        headers={
            "Authorization": f"Bearer {current_app.config['MAILERSEND_API_TOKEN']}",
            "Content-Type": "application/json",
        },
        json=_mailersend_payload(to_email, subject, text_body, html_body),
        timeout=current_app.config["EMAIL_TIMEOUT_SECONDS"],
    )
    if response.status_code != 202:
        raise EmailDeliveryError(f"MailerSend returned HTTP {response.status_code}.")


def _deliver_email(to_email, subject, text_body, html_body, idempotency_key, log_label):
    providers = _provider_order()
    if not providers:
        raise EmailDeliveryError("No transactional email provider is configured.")
    senders = {
        "resend": _send_with_resend,
        "mailersend": _send_with_mailersend,
    }
    failures = []
    for provider in providers:
        try:
            senders[provider](to_email, subject, text_body, html_body, idempotency_key)
            current_app.logger.info("%s email accepted provider=%s", log_label, provider)
            return provider
        except (EmailDeliveryError, requests.RequestException) as error:
            failures.append(f"{provider}: {error}")
            current_app.logger.warning(
                "%s email provider failed provider=%s error_type=%s",
                log_label,
                provider,
                type(error).__name__,
            )
    raise EmailDeliveryError("; ".join(failures))


def send_password_reset_email(user, reset_url, reset_id):
    """Deliver a reset link through the preferred provider, then safe failover."""

    expires_minutes = current_app.config["PASSWORD_RESET_TOKEN_MINUTES"]
    context = {
        "display_name": user.display_name,
        "reset_url": reset_url,
        "expires_minutes": expires_minutes,
        "support_email": current_app.config["SERVICE_CONTACT_EMAIL"],
    }
    subject = "Reset your SulitShelf PH password"
    text_body = render_template("email/password_reset.txt", **context)
    html_body = render_template("email/password_reset.html", **context)
    idempotency_material = f"{current_app.config['PUBLIC_BASE_URL']}:{reset_id}:{reset_url}"
    idempotency_key = "sulitshelf-password-reset-" + hashlib.sha256(idempotency_material.encode()).hexdigest()
    return _deliver_email(user.email, subject, text_body, html_body, idempotency_key, "Password reset")


def send_crm_message_email(message, inbox_url):
    """Optionally mirror an in-app CRM message to the promoter's email."""

    context = {
        "display_name": message.recipient.display_name,
        "message_subject": message.subject,
        "message_body": message.body,
        "inbox_url": inbox_url,
        "support_email": current_app.config["SERVICE_CONTACT_EMAIL"],
    }
    subject = f"SulitShelf PH · {message.subject}"
    text_body = render_template("email/crm_message.txt", **context)
    html_body = render_template("email/crm_message.html", **context)
    material = f"{current_app.config['PUBLIC_BASE_URL']}:crm:{message.id}:{message.recipient_id}"
    idempotency_key = "sulitshelf-crm-" + hashlib.sha256(material.encode()).hexdigest()
    return _deliver_email(message.recipient.email, subject, text_body, html_body, idempotency_key, "CRM")
