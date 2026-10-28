"""
Daily email allowance.

Brevo's free plan allows a fixed number of emails per day (300, shared with campaigns). Alerts may
only use EMAIL_DAILY_LIMIT - EMAIL_SECURITY_RESERVE so that password resets and account set-up
links always get through. The day runs midnight to midnight UTC (Ghana time).
"""
import datetime

from django.conf import settings
from django.utils import timezone

from .models import Category, Channel, MessageStatus, OutgoingMessage


def day_start(now=None):
    now = (now or timezone.now()).astimezone(datetime.timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def sent_today(now=None):
    return OutgoingMessage.objects.filter(channel=Channel.EMAIL, status=MessageStatus.SENT,
                                          sent_at__gte=day_start(now)).count()


def allowance(category):
    limit = settings.EMAIL_DAILY_LIMIT
    if category == Category.ALERT:
        limit -= settings.EMAIL_SECURITY_RESERVE
    return max(limit, 0)


def remaining(category, now=None, sent=None):
    sent = sent_today(now) if sent is None else sent
    return max(allowance(category) - sent, 0)


def status(now=None):
    sent = sent_today(now)
    return {
        "limit": settings.EMAIL_DAILY_LIMIT,
        "security_reserve": settings.EMAIL_SECURITY_RESERVE,
        "sent_today": sent,
        "alerts_remaining": remaining(Category.ALERT, sent=sent),
        "security_remaining": remaining(Category.SECURITY, sent=sent),
    }
