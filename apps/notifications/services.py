"""
Creating notifications.

`notify()` is called by the workflow services after a state change. Each recipient gets an
in-app notification, a push message on every device they enabled, and (staff only, when they
have not opted out) an alert email within the daily allowance. Nothing is sent if the
surrounding transaction rolls back.

Messages never contain marks, grades or personal details: they appear on lock screens and in
mailboxes. The link leads to the signed-in page.
"""
from collections import defaultdict

from django.conf import settings
from django.db import transaction

from apps.accounts.models import Role, User
from apps.institutions.models import InstitutionProfile

from . import delivery, messages
from .models import Category, Channel, Kind, MessageStatus, Notification, OutgoingMessage, PushSubscription


def _absolute(link):
    return f"{settings.FRONTEND_URL.rstrip('/')}{link or '/notifications'}"


def _email_body(user, title, body, link, profile):
    lines = [f"Hello {user.first_name},", "", title]
    if body:
        lines.append(body)
    lines += ["", f"Open: {_absolute(link)}", "",
              f"You receive these emails because something needs your attention in {profile.name}'s "
              "student information system. You can turn them off under Profile > Notifications."]
    return "\n".join(lines)


def _unique_users(recipients, actor):
    seen, users = set(), []
    for user in recipients:
        if user is None or user.pk in seen or not user.is_active or (actor is not None and user.pk == actor.pk):
            continue
        seen.add(user.pk)
        users.append(user)
    # Staff first, so their alerts are among those sent straight away on large fan-outs.
    users.sort(key=lambda u: u.role == Role.STUDENT)
    return users


def notify(recipients, *, kind, title, body="", link="", actor=None, email=True):
    users = _unique_users(recipients, actor)
    if not users:
        return []
    profile = InstitutionProfile.load()
    notifications = Notification.objects.bulk_create([
        Notification(recipient=u, kind=kind, title=title[:200], body=body[:500], link=link[:300]) for u in users
    ])
    subscriptions = defaultdict(list)
    if delivery.push_enabled():
        for sub in PushSubscription.objects.filter(user__in=users):
            subscriptions[sub.user_id].append(sub)
    outbox = []
    for user, notification in zip(users, notifications):
        payload = {"id": notification.pk, "title": title[:200], "body": body[:500], "link": link,
                   "kind": kind, "tag": f"{kind}-{link}"}
        outbox += [OutgoingMessage(channel=Channel.PUSH, recipient=user, subscription=sub, notification=notification,
                                   payload=payload) for sub in subscriptions[user.pk]]
        if email and user.role != Role.STUDENT and user.email_alerts and user.email:
            outbox.append(OutgoingMessage(
                channel=Channel.EMAIL, category=Category.ALERT, recipient=user, to=user.email, notification=notification,
                subject=f"{profile.short_name}: {title}"[:200], body=_email_body(user, title, body, link, profile)))
    queued = OutgoingMessage.objects.bulk_create(outbox)
    _drain_after_commit([m.pk for m in queued])
    return notifications


def send_security_email(user, template):
    """Password reset and account set-up mail (see messages.TEMPLATES). May use the whole daily allowance."""
    return send_security_emails([user], template)[0]


def send_security_emails(users, template):
    queued = OutgoingMessage.objects.bulk_create([
        OutgoingMessage(channel=Channel.EMAIL, category=Category.SECURITY, recipient=user, to=user.email,
                        subject=messages.render(template, user)[0], payload={"template": template})
        for user in users
    ])
    _drain_after_commit([m.pk for m in queued])
    return queued


def pending_security_emails(users):
    return OutgoingMessage.objects.filter(recipient__in=users, category=Category.SECURITY,
                                          status=MessageStatus.PENDING).count()


def _drain_after_commit(ids):
    if not ids:
        return
    transaction.on_commit(lambda: _safe_drain(ids))


def _safe_drain(ids):
    try:
        delivery.drain(ids=ids, max_items=delivery.IMMEDIATE_LIMIT, deadline_seconds=8)
    except Exception:  # noqa: BLE001 - the queue is retried later
        delivery.logger.exception("Immediate notification delivery failed")


def build_digests():
    """Merge each person's pending alert emails into one, so a backlog fits the allowance."""
    pending = (OutgoingMessage.objects.filter(status=MessageStatus.PENDING, channel=Channel.EMAIL,
                                              category=Category.ALERT)
               .select_related("recipient", "notification").order_by("created_at"))
    groups = defaultdict(list)
    for message in pending:
        groups[message.to].append(message)
    profile = InstitutionProfile.load()
    created = 0
    for to, messages in groups.items():
        if len(messages) < 2:
            continue
        user = messages[0].recipient
        lines = [f"Hello {user.first_name}," if user else "Hello,", "",
                 f"You have {len(messages)} updates that may need your attention:", ""]
        for m in messages:
            title = m.notification.title if m.notification else m.subject
            link = m.notification.link if m.notification else ""
            lines.append(f"- {title}\n  {_absolute(link)}")
        lines += ["", f"All notifications: {_absolute('/notifications')}", "",
                  "You can turn these emails off under Profile > Notifications."]
        with transaction.atomic():
            OutgoingMessage.objects.create(channel=Channel.EMAIL, category=Category.ALERT, recipient=user, to=to,
                                           subject=f"{profile.short_name}: {len(messages)} updates"[:200],
                                           body="\n".join(lines))
            OutgoingMessage.objects.filter(pk__in=[m.pk for m in messages]).update(
                status=MessageStatus.SKIPPED, last_error="Merged into a digest")
        created += 1
    return created


# --- Who to tell ----------------------------------------------------------------

def users_with(flag):
    return list(User.objects.filter(is_active=True, **{flag: True}).exclude(role=Role.STUDENT))


def lecturers_of(offering):
    return list(User.objects.filter(is_active=True, lecturer_assignments__offering=offering).distinct())


def registry_staff():
    registrars = list(User.objects.filter(is_active=True, role=Role.REGISTRAR))
    return registrars or list(User.objects.filter(is_active=True, role=Role.ADMIN))


def student_users(students):
    return [s.user for s in students if getattr(s, "user", None) is not None]


def unread_count(user):
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


def own(user):
    return Notification.objects.filter(recipient=user)

