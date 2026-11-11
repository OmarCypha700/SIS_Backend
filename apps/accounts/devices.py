"""
Signed-in devices: where each person is signed in, how long a sign-in may last, and ending sign-ins.

Sessions stay in Django's session table (an HttpOnly cookie holds only the random session id). Each
sign-in also gets a SignedInDevice row describing it (browser, system, network address, last activity),
so people can see their sign-ins and end any of them, and administrators can sign someone out everywhere.
"""
import ipaddress
import re
import time
from datetime import timedelta

from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import Role, SignedInDevice, SignInMethod

DEVICE_ID = "sis_device"          # session key: the SignedInDevice row of this session
SIGNED_IN_AT = "sis_signed_in"    # session key: Unix time of the sign-in (for the maximum length)
SEEN_AT = "sis_seen"              # session key: Unix time last activity was written to the database
SEEN_KEY = "sis_seen_key"         # session key: the session id last written (it changes on a password change)
SEEN_EVERY = 5 * 60               # write "last active" at most every 5 minutes
LIMITS_CACHE_KEY = "accounts.session_limits"


# --- Describing a device ----------------------------------------------------

_BROWSERS = [  # order matters: Edge, Opera and Samsung Internet also say "Chrome"; Chrome also says "Safari"
    ("Edge", r"Edg(e|A|iOS)?/"), ("Opera", r"OPR/|Opera"), ("Samsung Internet", r"SamsungBrowser/"),
    ("Firefox", r"Firefox/|FxiOS/"), ("Chrome", r"Chrome/|CriOS/"), ("Safari", r"Safari/"),
]
_SYSTEMS = [
    ("iPhone", r"iPhone"), ("iPad", r"iPad"), ("Android", r"Android"), ("Windows", r"Windows"),
    ("ChromeOS", r"CrOS"), ("macOS", r"Mac OS X|Macintosh"), ("Linux", r"Linux"),
]


def describe(user_agent):
    """("Chrome", "Windows") from a browser's User-Agent; "Unknown" where it cannot tell."""
    ua = user_agent or ""
    browser = next((name for name, pattern in _BROWSERS if re.search(pattern, ua)), "Unknown browser")
    system = next((name for name, pattern in _SYSTEMS if re.search(pattern, ua)), "unknown device")
    return browser, system


def _client_ip(request):
    """The client's address, trusting X-Forwarded-For only as far as NUM_PROXIES (like the rate limits)."""
    from rest_framework.throttling import BaseThrottle

    address = BaseThrottle().get_ident(request) if getattr(request, "META", None) else None
    try:
        ipaddress.ip_address(address or "")
    except ValueError:
        return None
    return address


# --- Recording sign-ins and activity ----------------------------------------

def record_sign_in(request, user, method=SignInMethod.PASSWORD):
    """Called on every sign-in (user_logged_in): a row for the new session, and its start time."""
    session = request.session
    if not session.session_key:
        session.save()
    now = timezone.now()
    device = SignedInDevice.objects.create(
        user=user, session_key=session.session_key, method=method, signed_in_at=now, last_seen_at=now,
        user_agent=request.headers.get("User-Agent", "")[:300] if hasattr(request, "headers") else "",
        ip_address=_client_ip(request),
    )
    session[DEVICE_ID] = device.pk
    session[SIGNED_IN_AT] = int(now.timestamp())
    session[SEEN_AT] = int(now.timestamp())
    session[SEEN_KEY] = session.session_key
    return device


def forget_sign_in(request):
    """Called on sign-out (user_logged_out): the session is being deleted, so is its row."""
    session = getattr(request, "session", None)
    if session is None:
        return
    device_id = session.get(DEVICE_ID)
    if device_id:
        SignedInDevice.objects.filter(pk=device_id).delete()
    elif session.session_key:
        SignedInDevice.objects.filter(session_key=session.session_key).delete()


def current_device_id(request):
    """The SignedInDevice id of this request's sign-in, or None."""
    session = getattr(request, "session", None)
    device_id = session.get(DEVICE_ID) if session is not None else None
    if device_id is None or not getattr(request.user, "is_authenticated", False):
        return None
    return SignedInDevice.objects.filter(pk=device_id, user=request.user).values_list("pk", flat=True).first()


def touch(request):
    """Keep "last active" and the session id up to date, writing at most every few minutes."""
    session = request.session
    now = int(time.time())
    device_id = session.get(DEVICE_ID)
    if device_id is None:
        # A session from before devices were recorded: describe it now. Its start time is unknown, so
        # the maximum length counts from here.
        record_sign_in(request, request.user)
        return
    if session.get(SEEN_KEY) == session.session_key and now - session.get(SEEN_AT, 0) < SEEN_EVERY:
        return
    updated = SignedInDevice.objects.filter(pk=device_id, user=request.user).update(
        last_seen_at=timezone.now(), session_key=session.session_key, ip_address=_client_ip(request))
    if not updated:  # the row is gone (cleaned up); describe the session again
        session.pop(DEVICE_ID, None)
        record_sign_in(request, request.user)
        return
    session[SEEN_AT] = now
    session[SEEN_KEY] = session.session_key


# --- Maximum sign-in length ---------------------------------------------------

def session_limits():
    """(student days, staff hours) from Institution settings, cached briefly: this is read on every request."""
    limits = cache.get(LIMITS_CACHE_KEY)
    if limits is None:
        from apps.institutions.models import InstitutionProfile

        profile = InstitutionProfile.load()
        limits = (profile.student_session_days, profile.staff_session_hours)
        cache.set(LIMITS_CACHE_KEY, limits, timeout=300)
    return limits


def forget_session_limits():
    cache.delete(LIMITS_CACHE_KEY)


def max_length(user):
    student_days, staff_hours = session_limits()
    return timedelta(days=student_days) if user.role == Role.STUDENT else timedelta(hours=staff_hours)


def has_run_out(request):
    """True once this sign-in is older than the institution allows, however active it has been."""
    signed_in = request.session.get(SIGNED_IN_AT)
    if not signed_in:
        return False
    return time.time() - signed_in > max_length(request.user).total_seconds()


# --- Listing and ending sign-ins ----------------------------------------------

def live_devices(user):
    """The person's sign-ins whose session has not expired, most recently active first."""
    live_keys = Session.objects.filter(expire_date__gt=timezone.now()).values("session_key")
    return (SignedInDevice.objects.filter(user=user, session_key__in=live_keys)
            .prefetch_related("push_subscriptions"))


def _untracked_session_keys(user):
    """Sessions of this person with no device row (made before devices were recorded and not used since)."""
    tracked = SignedInDevice.objects.values("session_key")
    keys = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()).exclude(session_key__in=tracked):
        if session.get_decoded().get(SESSION_KEY) == str(user.pk):
            keys.append(session.session_key)
    return keys


@transaction.atomic
def sign_out_device(device):
    """End one sign-in now: its session, its row and the notifications it turned on."""
    from apps.notifications.models import PushSubscription

    Session.objects.filter(session_key=device.session_key).delete()
    PushSubscription.objects.filter(device=device).delete()
    device.delete()


@transaction.atomic
def sign_out_everywhere(user, *, keep_device_id=None):
    """
    End every sign-in of this person except `keep_device_id` (the device asking, if any), and stop
    notifications on every other device. Returns how many sign-ins were ended.
    """
    from apps.notifications.models import PushSubscription

    others = SignedInDevice.objects.filter(user=user).exclude(pk=keep_device_id)
    keys = list(others.values_list("session_key", flat=True)) + _untracked_session_keys(user)
    live = Session.objects.filter(session_key__in=keys, expire_date__gt=timezone.now()).count()
    Session.objects.filter(session_key__in=keys).delete()
    subscriptions = PushSubscription.objects.filter(user=user)
    if keep_device_id:
        subscriptions = subscriptions.exclude(device_id=keep_device_id)
    subscriptions.delete()
    others.delete()
    return live


def keep_others_signed_in(user, *, keep_device_id):
    """
    After a password change where the person chose to stay signed in elsewhere: Django signs other
    sessions out when the password changes, so give them the new password fingerprint.
    """
    new_hash = user.get_session_auth_hash()
    keys = SignedInDevice.objects.filter(user=user).exclude(pk=keep_device_id).values_list("session_key", flat=True)
    store = SessionStore()
    for session in Session.objects.filter(session_key__in=list(keys), expire_date__gt=timezone.now()):
        data = session.get_decoded()
        if data.get(SESSION_KEY) == str(user.pk):
            data[HASH_SESSION_KEY] = new_hash
            Session.objects.filter(pk=session.pk).update(session_data=store.encode(data))


def clear_expired():
    """Daily: remove expired sessions and the rows describing them. Returns how many rows were removed."""
    Session.objects.filter(expire_date__lte=timezone.now()).delete()
    deleted, _ = SignedInDevice.objects.exclude(session_key__in=Session.objects.values("session_key")).delete()
    return deleted
