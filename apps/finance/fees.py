"""
Fee schedules and billing.

Billing a schedule charges each of its items to every student of the programme and level whose
status allows registration. It can be run again safely: items already charged are left alone,
new items are added, and a changed amount is billed as an adjustment for the difference. A charge
that someone reversed is not charged again.
"""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.audit.services import log_action
from apps.students.models import StatusPolicy, Student
from common.exceptions import DomainError

from . import accounts
from .models import EntryKind, EntrySide, FeeItem, FeeSchedule, LedgerEntry

MAX_PREVIEW = 2000


class _Rollback(Exception):
    pass


def students_for(schedule):
    allowed = StatusPolicy.objects.filter(can_register=True).values_list("status", flat=True)
    return (Student.objects.filter(programme=schedule.programme, current_level=schedule.level, status__in=allowed,
                                   is_archived=False)
            .order_by("last_name", "first_name", "student_number"))


def _plan(schedule, students):
    items = list(schedule.items.all())
    if not items:
        raise DomainError("Add at least one fee item before billing.", code="no_items")
    charges = {(e.student_id, e.fee_item_id): e for e in
               LedgerEntry.objects.filter(kind=EntryKind.CHARGE, fee_item__in=items, student__in=students)
               .prefetch_related("reversed_by")}
    billed = defaultdict(Decimal)
    for row in (LedgerEntry.objects.filter(fee_item__in=items, student__in=students, side=EntrySide.BILL)
                .values("student_id", "fee_item_id").annotate(total=Sum("amount"))):
        billed[(row["student_id"], row["fee_item_id"])] = row["total"]
    plan = []
    for student in students:
        lines = []
        for item in items:
            charge = charges.get((student.pk, item.pk))
            if charge is None:
                lines.append((item, EntryKind.CHARGE, item.amount))
            elif hasattr(charge, "reversed_by"):
                continue  # removed on purpose
            else:
                difference = item.amount - billed[(student.pk, item.pk)]
                if difference:
                    lines.append((item, EntryKind.ADJUSTMENT, difference))
        plan.append((student, lines))
    return plan


def bill(schedule, *, actor, dry_run=False, student_ids=None):
    """Charge the schedule to its students. Returns a summary; a dry run saves nothing."""
    schedule = FeeSchedule.objects.select_related("programme", "level", "academic_year", "semester").get(pk=schedule.pk)
    students = list(students_for(schedule))
    if student_ids is not None:
        wanted = set(student_ids)
        students = [s for s in students if s.pk in wanted]
    rows, changed = [], []
    try:
        with transaction.atomic():
            for student, lines in _plan(schedule, students):
                adding = sum((amount for _, _, amount in lines), Decimal("0"))
                for item, kind, amount in lines:
                    LedgerEntry.objects.create(
                        student=student, kind=kind, amount=amount, fee_item=item, academic_year=schedule.academic_year,
                        semester=schedule.semester, created_by=actor,
                        description=item.description if kind == EntryKind.CHARGE else f"{item.description} (amount changed)")
                if lines:
                    changed.append(student)
                rows.append({"student": student.pk, "student_number": student.student_number,
                             "full_name": student.full_name, "adding": accounts.money(adding),
                             "outcome": "unchanged" if not lines else
                             ("new" if all(k == EntryKind.CHARGE for _, k, _ in lines) else "updated")})
            if dry_run:
                raise _Rollback
            if changed:
                log_action("finance.billed", actor=actor, instance=schedule,
                           summary=f"Billed {schedule} to {len(changed)} student(s)",
                           metadata={"students": len(changed), "total": str(sum(r["adding"] for r in rows))})
                accounts.refresh_clearance(changed)
    except _Rollback:
        pass
    return {
        "schedule": schedule.pk, "dry_run": dry_run, "students": len(rows), "changed": len(changed),
        "total": accounts.money(sum((r["adding"] for r in rows), Decimal("0"))),
        "rows": rows[:MAX_PREVIEW],
    }


@transaction.atomic
def save_items(schedule, items, *, actor):
    """Replace a schedule's items. Items already charged to students can change amount but not be removed."""
    keep_ids = {int(i["id"]) for i in items if i.get("id")}
    for item in schedule.items.exclude(pk__in=keep_ids):
        if item.entries.exists():
            raise DomainError(f"“{item.description}” has already been billed to students. Set it to the right amount "
                              "and bill again, or reverse the charges first.", code="item_billed", status_code=409)
        item.delete()
    existing = {i.pk: i for i in schedule.items.all()}
    for order, data in enumerate(items):
        description = (data.get("description") or "").strip()
        if not description:
            raise DomainError("Each fee item needs a description.", code="description_required")
        amount = accounts._amount(data.get("amount"))
        item = existing.get(int(data["id"])) if data.get("id") else None
        if item is None:
            FeeItem.objects.create(schedule=schedule, description=description[:120], amount=amount, order=order)
        else:
            item.description, item.amount, item.order = description[:120], amount, order
            item.save()
    log_action("finance.fee_schedule_saved", actor=actor, instance=schedule, summary=f"Saved fees for {schedule}",
               metadata={"items": len(items), "total": str(schedule.total)})
    return schedule


@transaction.atomic
def copy_schedules(source_year, target_year, *, actor):
    """Copy one year's schedules (with items) to another year. Existing schedules are left as they are."""
    created = 0
    for schedule in FeeSchedule.objects.filter(academic_year=source_year).prefetch_related("items"):
        semester = None
        if schedule.semester_id:
            semester = target_year.semesters.filter(term_number=schedule.semester.term_number).first()
            if semester is None:
                continue
        copy, made = FeeSchedule.objects.get_or_create(programme=schedule.programme, level=schedule.level,
                                                       academic_year=target_year, semester=semester,
                                                       defaults={"notes": schedule.notes})
        if made:
            FeeItem.objects.bulk_create([FeeItem(schedule=copy, description=i.description, amount=i.amount,
                                                 order=i.order) for i in schedule.items.all()])
            created += 1
    log_action("finance.fee_schedules_copied", actor=actor, summary=f"Copied {created} fee schedule(s) "
                                                                    f"from {source_year} to {target_year}")
    return created
