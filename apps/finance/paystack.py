"""
Online payment through Paystack's hosted checkout (mobile money, card, bank).

The student pays on Paystack's page, so card numbers and MoMo PINs never reach this system. A
payment is credited only after this server has asked Paystack about it (`confirm`) and the
reference, currency and amount all match: the browser's return and the webhook only trigger that
check. Confirming twice is harmless; the first confirmation credits, the rest find it done.

Without background workers, pending payments are checked when the student returns, when someone
presses "Check status", and by the daily task (`reconcile`).
"""
import datetime
import hashlib
import hmac
import logging
import secrets
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit.services import log_action
from apps.institutions.models import InstitutionProfile
from common.exceptions import DomainError

from . import accounts
from .models import Payment, PaymentSource, PaymentStatus

log = logging.getLogger(__name__)
TIMEOUT = (3.05, 15)
CHANNELS = ["mobile_money", "card", "bank", "bank_transfer"]
CHECK_AFTER = datetime.timedelta(minutes=30)
ABANDON_AFTER = datetime.timedelta(hours=24)


class GatewayError(Exception):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status

    @property
    def rejected_key(self):
        return self.status == 401


KEY_HELP = ("the payment provider rejected PAYSTACK_SECRET_KEY. Use the secret key (sk_test_... or sk_live_...), "
            "not the public key (pk_...), with no quotes or spaces, from the same mode (test or live) as the "
            "dashboard you copied it from; then reload the web app. Run `manage.py check_payment_setup` to test it.")


def configured():
    return bool(settings.PAYSTACK_SECRET_KEY)


def enabled(profile=None):
    profile = profile or InstitutionProfile.load()
    return profile.online_payments_enabled and configured()


def fee_for(amount, profile=None):
    """The transaction fee added on top, so that after the provider's charge the institution receives `amount`."""
    profile = profile or InstitutionProfile.load()
    rate = Decimal(profile.payment_fee_percent) / 100
    if rate <= 0:
        return accounts.ZERO
    fee = accounts.ceil_cents(Decimal(amount) * rate / (1 - rate))
    if profile.payment_fee_cap is not None:
        fee = min(fee, profile.payment_fee_cap)
    return accounts.money(fee)


def quote(student, amount, profile=None):
    profile = profile or InstitutionProfile.load()
    amount = accounts._amount(amount)
    owed = accounts.balance(student)
    if owed <= 0:
        raise DomainError("You have nothing to pay at the moment.", code="nothing_owed")
    if amount > owed:
        raise DomainError(f"You can pay at most GHS {owed:,.2f}, your outstanding balance.", code="amount_too_large")
    if amount < 1:
        raise DomainError("The smallest payment is GHS 1.00.", code="amount_too_small")
    fee = fee_for(amount, profile)
    return {"amount": amount, "fee": fee, "total": amount + fee}


def pesewas(value):
    return int((Decimal(value) * 100).to_integral_value())


def _headers():
    return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}


def _call(method, path, **kwargs):
    url = f"{settings.PAYSTACK_BASE_URL.rstrip('/')}{path}"
    try:
        response = requests.request(method, url, headers=_headers(), timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise GatewayError(f"Could not reach the payment service ({exc.__class__.__name__}).") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or not body.get("status"):
        raise GatewayError(body.get("message") or f"Payment service error {response.status_code}",
                           status=response.status_code)
    return body.get("data") or {}


def start_payment(student, amount, *, actor):
    """Create a pending payment and return it with the Paystack checkout URL."""
    profile = InstitutionProfile.load()
    if not enabled(profile):
        raise DomainError("Online payment is not available. Pay at the bank or the Accounts Office.",
                          code="payments_disabled", status_code=403)
    email = (getattr(student.user, "email", "") or student.email or "").strip()
    if not email:
        raise DomainError("Your account has no email address, which the payment service needs. Contact the registry.",
                          code="email_required")
    priced = quote(student, amount, profile)
    payment = Payment.objects.create(student=student, reference=f"SIS-{secrets.token_hex(10).upper()}",
                                     source=PaymentSource.ONLINE, amount=priced["amount"], fee=priced["fee"],
                                     total=priced["total"])
    try:
        data = _call("POST", "/transaction/initialize", json={
            "email": email, "amount": pesewas(payment.total), "currency": "GHS", "reference": payment.reference,
            "channels": CHANNELS,
            "callback_url": f"{settings.FRONTEND_URL.rstrip('/')}/fees/return",
            "metadata": {"student_number": student.student_number, "school_fees": str(payment.amount),
                         "transaction_fee": str(payment.fee),
                         "custom_fields": [{"display_name": "Student number", "variable_name": "student_number",
                                            "value": student.student_number}]},
        })
    except GatewayError as exc:
        payment.status, payment.gateway_message = PaymentStatus.FAILED, str(exc)[:255]
        payment.save(update_fields=["status", "gateway_message", "updated_at"])
        if exc.rejected_key:
            log.error("Online payment is misconfigured (%s): %s", payment.reference, KEY_HELP)
            raise DomainError("Online payment is not available at the moment. Please try again later, or pay at the "
                              "bank or the Accounts Office.", code="payments_unavailable", status_code=503) from None
        log.warning("Paystack initialize failed for %s: %s", payment.reference, exc)
        raise DomainError("The payment service is not responding. Please try again in a few minutes.",
                          code="gateway_unavailable", status_code=502) from None
    log_action("finance.payment_started", actor=actor, instance=payment,
               summary=f"Online payment of GHS {payment.amount} started for {student.student_number}")
    return payment, data.get("authorization_url")


def _trim(data):
    """What we keep of Paystack's answer: no card or customer details."""
    authorization = data.get("authorization") or {}
    return {
        "id": data.get("id"), "status": data.get("status"), "channel": data.get("channel"),
        "paid_at": data.get("paid_at") or data.get("paidAt"), "gateway_response": data.get("gateway_response"),
        "fees": data.get("fees"), "amount": data.get("amount"), "currency": data.get("currency"),
        "bank": authorization.get("bank"), "brand": authorization.get("brand") or authorization.get("card_type"),
    }


def confirm(reference, *, actor=None):
    """
    Ask Paystack about a payment and record the outcome. Returns the payment. Network problems
    leave it pending (it is checked again later).
    """
    payment = Payment.objects.filter(reference=reference, source=PaymentSource.ONLINE).first()
    if payment is None or payment.status != PaymentStatus.PENDING:
        return payment
    try:
        data = _call("GET", f"/transaction/verify/{reference}")
    except GatewayError as exc:
        if exc.rejected_key:
            log.error("Could not check payment %s: %s", reference, KEY_HELP)
        Payment.objects.filter(pk=payment.pk).update(last_checked_at=timezone.now(), gateway_message=str(exc)[:255])
        payment.refresh_from_db()
        return payment
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != PaymentStatus.PENDING:
            return payment  # someone else confirmed it meanwhile
        payment.last_checked_at = timezone.now()
        payment.gateway_response = _trim(data)
        payment.gateway_message = (data.get("gateway_response") or "")[:255]
        status = data.get("status")
        if status == "success":
            matches = (data.get("reference") == payment.reference and data.get("currency") == "GHS"
                       and int(data.get("amount") or 0) == pesewas(payment.total))
            if matches:
                _succeed(payment, data, actor)
            else:
                payment.status = PaymentStatus.MISMATCH
                payment.save()
                log_action("finance.payment_mismatch", actor=actor, instance=payment,
                           summary=f"The payment service reported {data.get('currency')} {Decimal(data.get('amount') or 0) / 100} "
                                   f"for {payment.reference}, expected GHS {payment.total}; not credited")
                from apps.notifications import events

                transaction.on_commit(lambda: events.payment_attention(payment))
        elif status in ("failed", "reversed"):
            payment.status = PaymentStatus.FAILED
            payment.save()
        else:  # abandoned, ongoing, pending, processing, queued: not finished yet
            payment.save()
    return payment


def _succeed(payment, data, actor):
    paid_at = parse_datetime(str(data.get("paid_at") or data.get("paidAt") or "")) or timezone.now()
    payment.status = PaymentStatus.SUCCESS
    payment.channel = (data.get("channel") or "")[:30]
    payment.paid_at = paid_at
    payment.receipt_number = accounts.next_receipt_number()
    payment.save()
    log_action("finance.payment_confirmed", actor=actor, instance=payment,
               summary=f"Online payment {payment.receipt_number}: GHS {payment.amount} from "
                       f"{payment.student.student_number} ({payment.channel or 'online'})",
               metadata={"reference": payment.reference, "fee": str(payment.fee)})
    accounts.credit_payment(payment, actor=actor)


def valid_signature(raw_body, signature):
    if not configured() or not signature:
        return False
    expected = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_webhook(event):
    """Paystack's notification of a finished charge. We still verify it ourselves before crediting."""
    if event.get("event") != "charge.success":
        return None
    reference = (event.get("data") or {}).get("reference")
    return confirm(reference) if reference else None


def reconcile(now=None):
    """Daily: check payments left pending, and give up on ones abandoned for a day."""
    now = now or timezone.now()
    checked = succeeded = 0
    if configured():
        for payment in Payment.objects.filter(source=PaymentSource.ONLINE, status=PaymentStatus.PENDING,
                                              created_at__lt=now - CHECK_AFTER).order_by("created_at")[:200]:
            checked += 1
            if confirm(payment.reference).status == PaymentStatus.SUCCESS:
                succeeded += 1
    abandoned = Payment.objects.filter(source=PaymentSource.ONLINE, status=PaymentStatus.PENDING,
                                       created_at__lt=now - ABANDON_AFTER).update(status=PaymentStatus.ABANDONED,
                                                                                  updated_at=now)
    return {"checked": checked, "succeeded": succeeded, "abandoned": abandoned}


def check_setup():
    """Ask the provider whether the configured key works. Returns (ok, message) for the IT administrator."""
    if not configured():
        return False, "PAYSTACK_SECRET_KEY is not set, so online payment is off."
    key = settings.PAYSTACK_SECRET_KEY
    if key.startswith("pk_"):
        return False, "PAYSTACK_SECRET_KEY holds the public key (pk_...). Use the secret key (sk_...)."
    try:
        _call("GET", "/transaction", params={"perPage": 1})
    except GatewayError as exc:
        if exc.rejected_key:
            return False, f"Not working: {KEY_HELP}"
        return False, f"Could not check the key: {exc}"
    mode = "TEST mode (no real money)" if key.startswith("sk_test_") else "LIVE mode (real payments)"
    return True, f"The key works. Online payment is in {mode}."
