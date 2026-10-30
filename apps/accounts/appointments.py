"""
Appointments: staff holding an office (Registrar, Results Officer) for a period.

One account per person, for their whole time at the institution. Offices move between people
through appointments, so the audit trail always shows which person acted, in which capacity, and
when. Ending an appointment removes the office's access at once; nothing the person did is changed
or reassigned.
"""
import datetime

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from common.exceptions import DomainError

from .models import Appointment, Role, User

# Open-ended appointments older than this are flagged for review on the administrator's dashboard.
REVIEW_AFTER = datetime.timedelta(days=365)


def _overlaps(user, office, starts_on, ends_on, exclude=None):
    """Another live appointment of the same person to the same office during [starts_on, ends_on]."""
    others = Appointment.objects.filter(user=user, office=office, ended_at__isnull=True)
    if exclude is not None:
        others = others.exclude(pk=exclude.pk)
    if ends_on is not None:
        others = others.filter(starts_on__lte=ends_on)
    return others.filter(ends_on__isnull=True).exists() or others.filter(ends_on__gte=starts_on).exists()


def _check_period(starts_on, ends_on):
    if ends_on is not None and ends_on < starts_on:
        raise DomainError("The end date cannot be before the start date.", code="invalid_dates")


@transaction.atomic
def appoint(user, *, office, starts_on, actor, ends_on=None, acting=False, reference="", notes=""):
    user = User.objects.select_for_update().get(pk=user.pk)
    if user.role == Role.STUDENT:
        raise DomainError("Students cannot be appointed to an office.", code="not_staff")
    if not user.is_active:
        raise DomainError("This account is disabled. Activate it before recording an appointment.",
                          code="inactive")
    if user.role == office:
        raise DomainError(f"{user.get_full_name()}'s own role is already {user.get_role_display()}.",
                          code="already_role")
    _check_period(starts_on, ends_on)
    if _overlaps(user, office, starts_on, ends_on):
        raise DomainError("This person already holds that office during part of this period. End or change that "
                          "appointment first.", code="overlapping_appointment", status_code=409)
    appointment = Appointment.objects.create(
        user=user, office=office, acting=acting, starts_on=starts_on, ends_on=ends_on,
        reference=reference.strip(), notes=notes.strip(), appointed_by=actor,
    )
    period = f"from {starts_on:%d %b %Y}" + (f" to {ends_on:%d %b %Y}" if ends_on else "")
    log_action("appointment.created", actor=actor, instance=appointment,
               summary=f"Appointed {user.get_full_name()} {appointment.title} {period}"[:255],
               after=_record(appointment))
    user.forget_appointments()
    from apps.notifications import events

    events.appointment_changed(appointment, actor, started=True)
    return appointment


@transaction.atomic
def update_appointment(appointment, *, actor, **changes):
    """Change the planned end date, reference or notes. Office, person and start date stay as recorded."""
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if appointment.ended_at is not None:
        raise DomainError("This appointment has ended and is part of the record; it cannot be changed.",
                          code="ended", status_code=409)
    before = _record(appointment)
    for field in ("ends_on", "reference", "notes"):
        if field in changes:
            value = changes[field]
            setattr(appointment, field, value.strip() if isinstance(value, str) else value)
    _check_period(appointment.starts_on, appointment.ends_on)
    if _overlaps(appointment.user, appointment.office, appointment.starts_on, appointment.ends_on, exclude=appointment):
        raise DomainError("This would overlap another appointment of the same person to that office.",
                          code="overlapping_appointment", status_code=409)
    appointment.save(update_fields=["ends_on", "reference", "notes"])
    after = _record(appointment)
    changed = {k for k in after if before.get(k) != after.get(k)}
    if changed:
        log_action("appointment.updated", actor=actor, instance=appointment,
                   summary=f"Updated {appointment.user.get_full_name()}'s {appointment.title} appointment",
                   before={k: before[k] for k in changed}, after={k: after[k] for k in changed})
    return appointment


@transaction.atomic
def end_appointment(appointment, *, reason, actor, deactivate_account=False):
    """End now, e.g. a reshuffle or a person relieved of their post. Access stops on their next request."""
    if not (reason or "").strip():
        raise DomainError("Give a reason for ending this appointment.", code="reason_required")
    appointment = Appointment.objects.select_for_update().select_related("user").get(pk=appointment.pk)
    if appointment.ended_at is not None:
        raise DomainError("This appointment has already ended.", code="ended", status_code=409)
    today = timezone.localdate()
    if appointment.ends_on is not None and appointment.ends_on < today:
        raise DomainError("This appointment already ran its course.", code="ended", status_code=409)
    appointment.ended_at = timezone.now()
    appointment.ended_by = actor
    appointment.end_reason = reason.strip()
    # The recorded last day becomes today (or the start, for one that had not begun yet).
    appointment.ends_on = max(appointment.starts_on, today)
    appointment.save(update_fields=["ended_at", "ended_by", "end_reason", "ends_on"])
    log_action("appointment.ended", actor=actor, instance=appointment, reason=appointment.end_reason,
               summary=f"Ended {appointment.user.get_full_name()}'s {appointment.title} appointment")
    user = appointment.user
    if deactivate_account and user.is_active:
        if user.pk == actor.pk:
            raise DomainError("You cannot disable your own account.")
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        log_action("user.deactivated", actor=actor, instance=user, reason=appointment.end_reason,
                   summary=f"Deactivated {user.email} when ending their {appointment.title} appointment")
    from apps.notifications import events

    events.appointment_changed(appointment, actor, started=False)
    return appointment


def needs_review(today=None):
    """Open-ended appointments held for over a year, and ones ending within 30 days."""
    today = today or timezone.localdate()
    current = Appointment.objects.current(today).select_related("user")
    stale = current.filter(ends_on__isnull=True, starts_on__lte=today - REVIEW_AFTER)
    ending = current.filter(ends_on__isnull=False, ends_on__lte=today + datetime.timedelta(days=30))
    return [
        {"id": a.id, "user": a.user_id, "name": a.user.get_full_name(), "title": a.title,
         "starts_on": a.starts_on, "ends_on": a.ends_on, "reason": reason}
        for reason, items in (("open_ended", stale), ("ending_soon", ending)) for a in items
    ]


def _record(appointment):
    return {
        "user": appointment.user_id, "office": appointment.office, "acting": appointment.acting,
        "starts_on": appointment.starts_on.isoformat(),
        "ends_on": appointment.ends_on.isoformat() if appointment.ends_on else None,
        "reference": appointment.reference, "notes": appointment.notes,
    }
