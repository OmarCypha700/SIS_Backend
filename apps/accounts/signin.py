"""
A growing wait after wrong passwords, per email address.

The first three wrong passwords are free. Each further one makes the next attempt wait longer:
15 seconds, then 30, 1, 2, 4 and 8 minutes, up to 15 minutes at most. The count clears after a
successful sign-in, a password reset, an administrator clearing it, or a day without failures.

* It is temporary, never a lock an administrator has to undo, so nobody can keep someone else out
  of their account by typing their email with wrong passwords.
* It applies to every typed email, existing or not, and the message is the same either way, so it
  never reveals which emails have accounts.
* While waiting, the password is not even checked.
* The per-address and per-email rate limits (settings REST_FRAMEWORK throttles) still apply too.
"""
import datetime
import math

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action

from .models import SignInDelay, User

FREE_ATTEMPTS = 3
FIRST_WAIT_SECONDS = 15
MAX_WAIT_SECONDS = 15 * 60
FORGET_AFTER = datetime.timedelta(hours=24)
ALERT_AT = 10  # failures that warn the account holder


def normalise(email):
    return (email or "").strip().lower()[:254]


def wait_after(failures):
    """Seconds to wait after this many wrong passwords in a row."""
    if failures <= FREE_ATTEMPTS:
        return 0
    return min(FIRST_WAIT_SECONDS * 2 ** (failures - FREE_ATTEMPTS - 1), MAX_WAIT_SECONDS)


def seconds_left(email, now=None):
    now = now or timezone.now()
    row = SignInDelay.objects.filter(email=normalise(email), locked_until__gt=now).only("locked_until").first()
    return math.ceil((row.locked_until - now).total_seconds()) if row else 0


@transaction.atomic
def record_failure(email, now=None):
    """Count a wrong password; returns the seconds the next attempt must wait (0 for none)."""
    now = now or timezone.now()
    row, _ = SignInDelay.objects.select_for_update().get_or_create(email=normalise(email))
    if row.last_failure_at and now - row.last_failure_at > FORGET_AFTER:
        row.failures = 0
    row.failures += 1
    row.last_failure_at = now
    wait = wait_after(row.failures)
    row.locked_until = now + datetime.timedelta(seconds=wait) if wait else None
    row.save()
    if row.failures == ALERT_AT:
        _alert(row)
    return wait


def clear(email):
    SignInDelay.objects.filter(email=normalise(email)).delete()


def _alert(row):
    """Many wrong passwords in a row: tell the account holder (if the account exists) and log it."""
    user = User.objects.filter(email=row.email, is_active=True).first()
    log_action("auth.repeated_failed_sign_ins", instance=user, entity_type="" if user else "accounts.user",
               summary=f"{row.failures} wrong passwords in a row for {row.email}")
    if user is None:
        return
    from apps.notifications.models import Kind
    from apps.notifications.services import notify

    transaction.on_commit(lambda: notify(
        [user], kind=Kind.SECURITY, email=False, title="Several failed sign-ins to your account",
        body="Someone has entered a wrong password for your account several times. If it was not you, "
             "change your password.", link="/profile#password"))


def format_wait(seconds):
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
