"""
Course registration rules.

`build_options` evaluates every offering for a student and is used both to display choices
and to validate a submission, so the rules exist in exactly one place. `submit_registration`
re-evaluates everything inside a transaction with the offering rows locked.
"""
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.academics.models import CourseCategory, CourseOffering, OfferingStatus, PeriodStatus, ProgrammeCourse
from apps.audit.services import log_action
from apps.institutions.models import AcademicRegulation, InstitutionProfile
from apps.results.models import CourseResult, StudentMark
from apps.results.services import student_course_history
from apps.students.models import StatusPolicy
from common.exceptions import DomainError

from .models import (
    CourseRegistration, CourseRegistrationStatus, RegistrationEvent, RegistrationStatus, SemesterRegistration,
)


@dataclass
class OfferingOption:
    offering: CourseOffering
    category: str = CourseCategory.ELECTIVE
    is_compulsory: bool = False
    is_retake: bool = False
    is_registered: bool = False
    seats_left: int | None = None
    reasons: list = field(default_factory=list)

    @property
    def eligible(self):
        return not self.reasons

    def as_dict(self):
        course = self.offering.course
        return {
            "offering": self.offering.id,
            "course_code": course.code,
            "course_title": course.title,
            "credits": course.credits,
            "course_type": course.course_type,
            "category": self.category,
            "is_compulsory": self.is_compulsory,
            "is_retake": self.is_retake,
            "is_registered": self.is_registered,
            "eligible": self.eligible,
            "reasons": self.reasons,
            "seats_left": self.seats_left,
            "prerequisites": [p.code for p in course.prerequisites.all()],
            "lecturers": [a.lecturer.get_full_name() for a in self.offering.assignments.all()],
        }


@dataclass
class RegistrationContext:
    student: object
    semester: object
    profile: InstitutionProfile
    can_register: bool
    options: dict  # offering_id -> OfferingOption
    registration: SemesterRegistration | None

    @property
    def compulsory_ids(self):
        return {oid for oid, option in self.options.items() if option.is_compulsory}


def _offerings(semester, lock=False):
    queryset = (
        CourseOffering.objects.filter(semester=semester, status=OfferingStatus.OPEN, course__is_active=True)
        .select_related("course")
        .prefetch_related("programmes", "levels", "course__prerequisites", "assignments__lecturer")
        .order_by("id")
    )
    if lock:
        # Lock offerings so concurrent capacity checks are serialised (row locks on PostgreSQL;
        # SQLite serialises all writers through its IMMEDIATE transactions).
        list(CourseOffering.objects.select_for_update().filter(pk__in=queryset.values("pk")).order_by("id"))
    return list(queryset)


def build_options(student, semester, *, lock=False):
    profile = InstitutionProfile.load()
    regulation = AcademicRegulation.load()
    policy = StatusPolicy.for_status(student.status)
    registration = SemesterRegistration.objects.filter(student=student, semester=semester).first()
    registered_ids = set()
    if registration:
        registered_ids = set(registration.courses.filter(status=CourseRegistrationStatus.ACTIVE)
                             .values_list("offering_id", flat=True))

    offerings = _offerings(semester, lock=lock)
    enrolment = dict(
        CourseRegistration.objects.filter(offering__in=offerings, status=CourseRegistrationStatus.ACTIVE)
        .values("offering").annotate(n=Count("id")).values_list("offering", "n")
    )
    curriculum = {pc.course_id: pc for pc in
                  ProgrammeCourse.objects.filter(programme=student.programme).select_related("level")}
    passed, failed = student_course_history(student)
    attempts = attempt_counts(student)
    # Courses registered in another semester whose results are not yet published.
    pending = set(
        CourseRegistration.objects.filter(student=student, status=CourseRegistrationStatus.ACTIVE)
        .exclude(offering__semester=semester)
        .filter(Q(result__isnull=True) | Q(result__is_published=False))
        .values_list("offering__course_id", flat=True)
    )
    student_level = student.current_level

    options = {}
    for offering in offerings:
        course = offering.course
        option = OfferingOption(offering=offering, is_registered=offering.id in registered_ids)
        programme_ids = {p.id for p in offering.programmes.all()}
        level_orders = [lvl.order for lvl in offering.levels.all()]
        link = curriculum.get(course.id)

        open_to_programme = not programme_ids or student.programme_id in programme_ids
        if link is not None and open_to_programme:
            option.category = link.category
            course_level_order = link.level.order
        elif link is None and programme_ids and student.programme_id in programme_ids:
            option.category = CourseCategory.ELECTIVE
            course_level_order = min(level_orders) if level_orders else None
        elif profile.allow_cross_programme_electives:
            option.category = CourseCategory.ELECTIVE
            course_level_order = min(level_orders) if level_orders else None
        else:
            option.reasons.append("Not offered to your programme.")
            course_level_order = None

        if course_level_order is not None and course_level_order > student_level.order \
                and not profile.allow_higher_level_courses:
            option.reasons.append("This course is above your current level.")

        if course.id in passed:
            option.reasons.append("You have already passed this course.")
        elif course.id in failed:
            option.is_retake = True
            if not profile.allow_retakes:
                option.reasons.append("Retaking failed courses is not permitted.")
            elif regulation.max_attempts_per_course and attempts.get(course.id, 0) >= regulation.max_attempts_per_course:
                option.reasons.append(
                    f"You have used all {regulation.max_attempts_per_course} permitted attempts at this course."
                )
        if course.id in pending:
            option.reasons.append("You are registered for this course in another semester and the result is pending.")

        missing = [p.code for p in course.prerequisites.all() if p.id not in passed]
        if missing:
            option.reasons.append(f"Prerequisite not met: {', '.join(missing)}.")

        if offering.capacity is not None:
            option.seats_left = max(offering.capacity - enrolment.get(offering.id, 0), 0)
            if option.seats_left == 0 and not option.is_registered:
                option.reasons.append("This course is full.")

        is_curriculum_compulsory = link is not None and open_to_programme and link.category == CourseCategory.COMPULSORY
        option.is_compulsory = bool(
            is_curriculum_compulsory and course.id not in passed and (
                (link.level_id == student_level.id and link.term_number == semester.term_number)
                # A failed compulsory course must be retaken whenever it is offered, if the regulations say so.
                or (regulation.failed_compulsory_must_be_retaken and option.is_retake)
            )
        )
        options[offering.id] = option

    return RegistrationContext(student=student, semester=semester, profile=profile, can_register=policy.can_register,
                               options=options, registration=registration)


def attempt_counts(student):
    """Number of published results per course id, i.e. completed attempts."""
    counts = {}
    for course_id in CourseResult.objects.filter(student=student, is_published=True).values_list(
            "offering__course_id", flat=True):
        counts[course_id] = counts.get(course_id, 0) + 1
    return counts


def window_state(semester, registration):
    now = timezone.now()
    if semester.status == PeriodStatus.CLOSED:
        return False, "This semester is closed."
    if registration is None or registration.status in (RegistrationStatus.REJECTED, RegistrationStatus.CANCELLED):
        if semester.registration_is_open(now):
            return True, ""
        if now < semester.registration_start:
            return False, "Registration has not opened yet."
        return False, "Registration for this semester is closed."
    if semester.amendments_are_open(now):
        return True, ""
    return False, "The add/drop period has ended."


@transaction.atomic
def submit_registration(student, semester, offering_ids, *, actor, on_behalf=False, reason=""):
    """
    Make the student's active course list for the semester equal to `offering_ids`.

    Raises DomainError (with per-course errors) and rolls back if any rule fails.
    """
    from apps.students.models import Student

    student = Student.objects.select_for_update().select_related("programme", "current_level").get(pk=student.pk)
    requested = set(int(i) for i in offering_ids)
    if not requested:
        raise DomainError("Select at least one course.", code="empty_registration")
    if on_behalf and not (reason or "").strip():
        raise DomainError("A reason is required when registering on behalf of a student.", code="reason_required")

    ctx = build_options(student, semester, lock=True)
    if not ctx.can_register:
        raise DomainError(f"Students with status '{student.get_status_display()}' cannot register.", code="status_blocked")
    is_open, message = window_state(semester, ctx.registration)
    if not is_open and not on_behalf:
        raise DomainError(message, code="window_closed")
    if semester.status == PeriodStatus.CLOSED:
        raise DomainError("This semester is closed.", code="window_closed")

    errors = []
    unknown = requested - set(ctx.options)
    for offering_id in sorted(unknown):
        errors.append({"offering": offering_id, "message": "This course is not available this semester."})
    for offering_id in sorted(requested - unknown):
        option = ctx.options[offering_id]
        if not option.eligible:
            errors.append({"offering": offering_id, "course_code": option.offering.course.code,
                           "message": " ".join(option.reasons)})
    for offering_id in sorted(ctx.compulsory_ids - requested):
        option = ctx.options[offering_id]
        if option.eligible:
            errors.append({"offering": offering_id, "course_code": option.offering.course.code,
                           "message": "This is a compulsory course for your level and must be registered."})

    selected = [ctx.options[i] for i in requested if i in ctx.options]
    total_credits = sum(o.offering.course.credits for o in selected)
    programme = student.programme
    approval_reasons = []
    if programme.min_credits_per_semester and total_credits < programme.min_credits_per_semester:
        errors.append({"message": f"You must register at least {programme.min_credits_per_semester} credits "
                                  f"(selected {total_credits})."})
    if programme.max_credits_per_semester and total_credits > programme.max_credits_per_semester:
        if ctx.profile.allow_credit_overload:
            approval_reasons.append(f"Credit overload: {total_credits} > {programme.max_credits_per_semester}.")
        else:
            errors.append({"message": f"You may register at most {programme.max_credits_per_semester} credits "
                                      f"(selected {total_credits})."})

    registration = ctx.registration
    existing = {}
    if registration is not None:
        existing = {cr.offering_id: cr for cr in registration.courses.filter(status=CourseRegistrationStatus.ACTIVE)}
    to_drop = [cr for oid, cr in existing.items() if oid not in requested]
    with_marks = set(StudentMark.objects.filter(registration__in=to_drop, score__isnull=False)
                     .values_list("registration_id", flat=True))
    for cr in to_drop:
        if cr.id in with_marks:
            errors.append({"offering": cr.offering_id, "course_code": cr.offering.course.code,
                           "message": "Marks have already been recorded for this course; it cannot be dropped."})

    if errors:
        raise DomainError("Registration could not be completed.", code="registration_invalid", errors=errors)

    if ctx.profile.registration_requires_approval:
        approval_reasons.append("Institution requires registration approval.")
    new_status = RegistrationStatus.PENDING if approval_reasons and not on_behalf else RegistrationStatus.REGISTERED
    now = timezone.now()

    if registration is None:
        registration = SemesterRegistration.objects.create(
            student=student, semester=semester, programme=student.programme, level=student.current_level,
            status=new_status,
        )
    registration.status = new_status
    registration.requires_approval_reason = " ".join(approval_reasons)[:255] if new_status == RegistrationStatus.PENDING else ""
    registration.total_credits = total_credits
    registration.submitted_at = now
    registration.programme, registration.level = student.programme, student.current_level
    registration.reviewed_by, registration.reviewed_at, registration.review_comment = None, None, ""
    registration.save()

    events = []
    for cr in to_drop:
        cr.status, cr.dropped_at = CourseRegistrationStatus.DROPPED, now
        cr.save(update_fields=["status", "dropped_at", "updated_at"])
        events.append(RegistrationEvent(semester_registration=registration, action=RegistrationEvent.Action.COURSE_DROPPED,
                                        offering=cr.offering, actor=actor, on_behalf=on_behalf, reason=reason))
    added = []
    for offering_id in sorted(requested - set(existing)):
        option = ctx.options[offering_id]
        try:
            with transaction.atomic():
                added.append(CourseRegistration.objects.create(
                    semester_registration=registration, student=student, offering=option.offering,
                    is_retake=option.is_retake, category=option.category, added_by=actor,
                ))
        except IntegrityError:
            raise DomainError("You are already registered for this course.", code="duplicate") from None
        events.append(RegistrationEvent(semester_registration=registration, action=RegistrationEvent.Action.COURSE_ADDED,
                                        offering=option.offering, actor=actor, on_behalf=on_behalf, reason=reason))
    events.append(RegistrationEvent(semester_registration=registration, action=RegistrationEvent.Action.SUBMITTED,
                                    actor=actor, on_behalf=on_behalf, reason=reason))
    RegistrationEvent.objects.bulk_create(events)

    # Re-check capacity after writing, inside the same transaction, as a final guard.
    for cr in added:
        offering = cr.offering
        if offering.capacity is not None and offering.registrations.filter(
                status=CourseRegistrationStatus.ACTIVE).count() > offering.capacity:
            raise DomainError(f"{offering.course.code} became full while you were registering.", code="full")

    log_action(
        "registration.on_behalf" if on_behalf else "registration.submitted", actor=actor, instance=registration,
        reason=reason, summary=f"{student.student_number} registration for {semester}: {len(added)} added, {len(to_drop)} dropped",
        after={"offerings": sorted(requested), "status": registration.status, "credits": total_credits},
        before={"offerings": sorted(existing)},
    )
    return registration


@transaction.atomic
def review_registration(registration, *, approve, actor, comment=""):
    registration = SemesterRegistration.objects.select_for_update().get(pk=registration.pk)
    if registration.status != RegistrationStatus.PENDING:
        raise DomainError("Only registrations awaiting approval can be reviewed.", code="invalid_transition", status_code=409)
    if not approve and not (comment or "").strip():
        raise DomainError("Give a reason for rejecting the registration.", code="comment_required")
    now = timezone.now()
    registration.status = RegistrationStatus.REGISTERED if approve else RegistrationStatus.REJECTED
    registration.reviewed_by, registration.reviewed_at, registration.review_comment = actor, now, comment or ""
    registration.save()
    if not approve:
        registration.courses.filter(status=CourseRegistrationStatus.ACTIVE).update(
            status=CourseRegistrationStatus.DROPPED, dropped_at=now, updated_at=now)
    RegistrationEvent.objects.create(
        semester_registration=registration,
        action=RegistrationEvent.Action.APPROVED if approve else RegistrationEvent.Action.REJECTED,
        actor=actor, reason=comment or "",
    )
    log_action("registration.approved" if approve else "registration.rejected", actor=actor, instance=registration,
               reason=comment or "", summary=f"Registration {registration} {'approved' if approve else 'rejected'}")
    return registration
