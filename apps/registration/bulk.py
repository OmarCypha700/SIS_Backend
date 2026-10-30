"""
Bulk course registration by the registry.

The registrar picks a semester and a cohort (programme and level). Each student gets their own
compulsory courses for that semester (including failed compulsory courses the regulations require
them to retake), plus any electives the registrar chooses for the whole group. Courses are only
ever added: nothing a student already registered for is dropped.

Every student goes through exactly the same rules as a single registration (submit_registration):
prerequisites, retake limits, capacity, credit limits, status holds. The preview runs those rules
and rolls back; confirming runs them for real, one student per savepoint, so one student with a
problem is reported and skipped without affecting the others.

Calls are limited to MAX_STUDENTS students so a request stays short on small hosting; the page
sends a large cohort in batches and shows progress.
"""
from django.db import transaction

from apps.academics.models import CourseCategory, ProgrammeCourse
from apps.audit.services import log_action
from apps.notifications import events as notify_events
from apps.students.models import StatusPolicy, Student
from common.exceptions import DomainError

from . import services
from .models import RegistrationStatus

MAX_STUDENTS = 50


class _Rollback(Exception):
    """Raised inside a preview so nothing it wrote is kept."""


def cohort(semester, programme, level):
    """Students of the cohort who may register, and the offerings open to them this semester."""
    allowed = StatusPolicy.objects.filter(can_register=True).values_list("status", flat=True)
    students = (Student.objects.filter(programme=programme, current_level=level, status__in=allowed, is_archived=False)
                .order_by("last_name", "first_name", "student_number"))
    registrations = {r.student_id: r.status for r in semester.registrations.filter(student__in=students)}
    compulsory_courses = set(
        ProgrammeCourse.objects.filter(programme=programme, level=level, term_number=semester.term_number,
                                       category=CourseCategory.COMPULSORY).values_list("course_id", flat=True))
    offerings = []
    for offering in services._offerings(semester):
        programme_ids = {p.id for p in offering.programmes.all()}
        level_ids = {lvl.id for lvl in offering.levels.all()}
        if (programme_ids and programme.id not in programme_ids) or (level_ids and level.id not in level_ids):
            continue
        offerings.append({
            "offering": offering.id, "course_code": offering.course.code, "course_title": offering.course.title,
            "credits": offering.course.credits, "is_compulsory": offering.course_id in compulsory_courses,
        })
    offerings.sort(key=lambda o: (not o["is_compulsory"], o["course_code"]))
    return {
        "students": [{"id": s.id, "student_number": s.student_number, "full_name": s.full_name, "status": s.status,
                      "registration_status": registrations.get(s.id)} for s in students],
        "offerings": offerings,
    }


def _plan(student, semester, extra_ids):
    """What the bulk run would register for one student, before the full rule check."""
    ctx = services.build_options(student, semester)
    row = {"student": student.id, "student_number": student.student_number, "full_name": student.full_name,
           "adding": [], "skipped": [], "errors": [], "target": []}
    if not ctx.can_register:
        row.update(outcome="blocked", errors=[f"Students with status '{student.get_status_display()}' cannot register."])
        return row
    registration = ctx.registration
    if registration is not None and registration.status == RegistrationStatus.PENDING:
        row.update(outcome="pending", errors=["Their own registration is awaiting approval. Review it first."])
        return row
    existing = {oid for oid, option in ctx.options.items() if option.is_registered}
    adding = []
    for offering_id in sorted((ctx.compulsory_ids | set(extra_ids)) - existing):
        option = ctx.options.get(offering_id)
        if option is None:
            continue  # not offered this semester (the page only lists open offerings)
        if option.eligible:
            adding.append(option)
        else:
            row["skipped"].append({"course_code": option.offering.course.code, "reason": " ".join(option.reasons)})
    row["adding"] = sorted(o.offering.course.code for o in adding)
    row["target"] = sorted(existing | {o.offering.id for o in adding})
    row["credits"] = sum(ctx.options[i].offering.course.credits for i in row["target"] if i in ctx.options)
    row["already_registered"] = bool(existing)
    row["outcome"] = "ready" if adding else "unchanged"
    return row


def _register(student, semester, row, *, actor, reason, dry_run):
    """Run the normal registration rules. Returns error messages (empty when it would succeed)."""
    try:
        with transaction.atomic():
            services.submit_registration(student, semester, row["target"], actor=actor, on_behalf=True,
                                         reason=reason)
            if dry_run:
                raise _Rollback
    except _Rollback:
        return []
    except DomainError as exc:
        details = exc.errors if isinstance(exc.errors, list) else []
        messages = [f"{e['course_code']}: {e['message']}" if e.get("course_code") else e["message"] for e in details]
        return messages or [exc.message]
    return []


def run(semester, student_ids, extra_ids, *, actor, reason="", dry_run=True, notify=True):
    """Preview (dry_run) or register up to MAX_STUDENTS students. Returns one row per student."""
    if len(student_ids) > MAX_STUDENTS:
        raise DomainError(f"Send at most {MAX_STUDENTS} students at a time.", code="too_many")
    if not dry_run and not (reason or "").strip():
        raise DomainError("Give a reason for the bulk registration (e.g. the cohort and semester).",
                          code="reason_required")
    students = {s.pk: s for s in Student.objects.select_related("programme", "current_level")
                .filter(pk__in=student_ids)}
    full_reason = f"Bulk registration: {reason.strip()}" if reason.strip() else "Bulk registration preview"
    rows, registered = [], []
    for student_id in dict.fromkeys(student_ids):
        student = students.get(student_id)
        if student is None:
            rows.append({"student": student_id, "outcome": "blocked", "errors": ["Student not found."],
                         "adding": [], "skipped": []})
            continue
        row = _plan(student, semester, extra_ids)
        if row["outcome"] == "ready":
            row["errors"] = _register(student, semester, row, actor=actor, reason=full_reason, dry_run=dry_run)
            if row["errors"]:
                row["outcome"] = "problem"
            elif not dry_run:
                row["outcome"] = "registered"
                registered.append(student)
        rows.append({k: v for k, v in row.items() if k != "target"})

    if not dry_run and registered:
        log_action("registration.bulk", actor=actor, entity_type="registration.semesterregistration",
                   reason=full_reason, summary=f"Bulk registration for {semester}: {len(registered)} students",
                   metadata={"semester": semester.pk, "students": [s.student_number for s in registered],
                             "extra_offerings": sorted(set(extra_ids))})
        if notify:
            notify_events.registered_by_registry(registered, semester, actor)
    return rows


def review_many(queryset, ids, *, approve, actor, comment=""):
    """Approve or reject several pending registrations; each is reviewed (and audited) on its own."""
    from common.bulk import run_bulk

    return run_bulk(
        queryset, ids,
        lambda registration: services.review_registration(registration, approve=approve, actor=actor, comment=comment),
        label=lambda r: f"{r.student.student_number} {r.student.full_name}",
    )
