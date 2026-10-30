"""
Sends queued email and web push.

There are no background workers on the target host, so the outbox is drained:
* right after the action that queued messages (a few, inside the request);
* a few at a time whenever someone's app polls for its unread count;
* completely by `manage.py send_pending_messages`, run once a day as a scheduled task.

Failures are logged and retried; they never break the request that triggered them.
"""
import datetime
import json
import logging
import time
from urllib.parse import urlsplit

import requests

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.db.models import F
from django.utils import timezone

from . import budget, messages
from .models import Category, Channel, MessageStatus, Notification, OutgoingMessage, PushSubscription

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Sending happens inside web requests, so each request does only a little: the host's proxy
# answers 502 when a request runs too long. The rest waits for the next poll or the daily task.
IMMEDIATE_LIMIT = 5         # sent inside the request that queued them
IMMEDIATE_SECONDS = 6
POLL_LIMIT = 3              # sent per unread-count poll
POLL_SECONDS = 4
POLL_EMAILS = 1             # email is slower than push; at most one per poll
POLL_INTERVAL_SECONDS = 10  # at most one poll-triggered drain per interval
PUSH_TTL_SECONDS = 24 * 60 * 60
ALERT_EXPIRY = datetime.timedelta(days=7)
STALE_SENDING = datetime.timedelta(minutes=10)


def push_enabled():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def drain(*, max_items=None, deadline_seconds=None, ids=None, max_emails=None):
    """
    Send pending messages, security email first, then oldest first. Returns counts by outcome.
    The deadline is checked before each send, so a call can overrun it by one send's timeout.
    """
    stats = {"sent": 0, "failed": 0, "deferred": 0, "skipped": 0}
    ends = time.monotonic() + deadline_seconds if deadline_seconds else None
    emails_sent = budget.sent_today()
    queue = OutgoingMessage.objects.filter(status=MessageStatus.PENDING)
    if ids is not None:
        queue = queue.filter(pk__in=list(ids))
    handled = emails_tried = 0
    for category in (Category.SECURITY, Category.ALERT):
        for pk in list(queue.filter(category=category).order_by("created_at", "id").values_list("pk", flat=True)):
            if (max_items is not None and handled >= max_items) or (ends and time.monotonic() > ends):
                return stats
            message = OutgoingMessage.objects.select_related("subscription", "recipient").filter(pk=pk).first()
            if message is None:
                continue
            if message.channel == Channel.EMAIL:
                if max_emails is not None and emails_tried >= max_emails:
                    continue
                if budget.remaining(category, sent=emails_sent) <= 0:
                    stats["deferred"] += 1
                    continue
            # Claim the row so two workers never send the same message.
            claimed = OutgoingMessage.objects.filter(pk=pk, status=MessageStatus.PENDING).update(
                status=MessageStatus.SENDING, attempts=F("attempts") + 1, updated_at=timezone.now())
            if not claimed:
                continue
            handled += 1
            emails_tried += message.channel == Channel.EMAIL
            message.refresh_from_db(fields=["attempts"])
            outcome = _send(message)
            stats[outcome] += 1
            if outcome == "sent" and message.channel == Channel.EMAIL:
                emails_sent += 1
    return stats


def drain_on_poll():
    """Opportunistic drain from the unread-count endpoint, rate-limited per process."""
    if not cache.add("notifications:poll-drain", 1, timeout=POLL_INTERVAL_SECONDS):
        return None
    if not OutgoingMessage.objects.filter(status=MessageStatus.PENDING).exists():
        return None
    try:
        return drain(max_items=POLL_LIMIT, deadline_seconds=POLL_SECONDS, max_emails=POLL_EMAILS)
    except Exception:  # noqa: BLE001 - never fail the poll
        logger.exception("Notification drain failed")
        return None


def _send(message):
    try:
        if message.channel == Channel.EMAIL:
            _send_email(message)
        else:
            if not _send_push(message):
                return _finish(message, MessageStatus.SKIPPED, "Device unsubscribed")
    except Exception as exc:  # noqa: BLE001 - recorded and retried
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.warning("Could not send %s message %s: %s", message.channel, message.pk, error)
        final = message.attempts >= MAX_ATTEMPTS
        _finish(message, MessageStatus.FAILED if final else MessageStatus.PENDING, error)
        return "failed"
    return _finish(message, MessageStatus.SENT)


def _finish(message, status, error=""):
    now = timezone.now()
    OutgoingMessage.objects.filter(pk=message.pk).update(
        status=status, last_error=error[:300], updated_at=now, sent_at=now if status == MessageStatus.SENT else None)
    return {"SENT": "sent", "SKIPPED": "skipped"}.get(status, "failed")


def _send_email(message):
    subject, body = message.subject, message.body
    template = message.payload.get("template")
    if template:
        if message.recipient is None or not message.recipient.is_active:
            raise RuntimeError("The account no longer exists or is disabled.")
        subject, body = messages.render(template, message.recipient)
    EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [message.to]).send(fail_silently=False)


class _NoRedirectSession(requests.Session):
    """Push services answer directly; following a redirect could reach an internal address."""

    def request(self, *args, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


def push_host_allowed(endpoint):
    host = (urlsplit(endpoint).hostname or "").lower()
    for allowed in settings.PUSH_ALLOWED_HOSTS:
        allowed = allowed.lower()
        if host == allowed.lstrip(".") or (allowed.startswith(".") and host.endswith(allowed)):
            return True
    return False


def _send_push(message):
    """Returns False when the subscription is gone (it is then deleted)."""
    from pywebpush import WebPushException, webpush

    subscription = message.subscription
    if subscription is None or not push_enabled():
        return False
    if not push_host_allowed(subscription.endpoint):
        subscription.delete()
        return False
    payload = dict(message.payload)
    if message.recipient_id:
        payload["unread"] = Notification.objects.filter(recipient_id=message.recipient_id, read_at__isnull=True).count()
    try:
        webpush(
            subscription_info={"endpoint": subscription.endpoint,
                               "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT or f"mailto:{settings.DEFAULT_FROM_EMAIL}"},
            ttl=PUSH_TTL_SECONDS,
            timeout=(3.05, 5),
            requests_session=_NoRedirectSession(),
        )
    except WebPushException as exc:
        code = getattr(exc.response, "status_code", None) if exc.response is not None else None
        if code in (404, 410):
            subscription.delete()
            return False
        PushSubscription.objects.filter(pk=subscription.pk).update(failures=F("failures") + 1)
        raise
    PushSubscription.objects.filter(pk=subscription.pk).update(last_success_at=timezone.now(), failures=0)
    return True


def recover_stale(now=None):
    """Messages left in SENDING by a crashed worker go back to the queue."""
    now = now or timezone.now()
    return OutgoingMessage.objects.filter(status=MessageStatus.SENDING, updated_at__lt=now - STALE_SENDING).update(
        status=MessageStatus.PENDING)


RESET_EXPIRY = datetime.timedelta(hours=24)


def expire_resets(now=None):
    """A reset link that waited a day for the allowance is dropped; the person can ask again."""
    now = now or timezone.now()
    return OutgoingMessage.objects.filter(status=MessageStatus.PENDING, category=Category.SECURITY,
                                          payload__template="password_reset",
                                          created_at__lt=now - RESET_EXPIRY).update(
        status=MessageStatus.SKIPPED, last_error="Expired after 24 hours")


def expire_alerts(now=None):
    """Alert email that could not be sent within a week is dropped; the in-app notification stays."""
    now = now or timezone.now()
    return OutgoingMessage.objects.filter(status=MessageStatus.PENDING, category=Category.ALERT,
                                          created_at__lt=now - ALERT_EXPIRY).update(
        status=MessageStatus.SKIPPED, last_error="Expired after 7 days")
