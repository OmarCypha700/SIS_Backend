"""Results workflow: assessment configuration, mark entry, approval, publication, amendments."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.services import log_action
from apps.institutions.models import AcademicRegulation, InstitutionProfile
from apps.notifications import events as notify_events
from apps.registration.models import CourseRegistration, CourseRegistrationStatus
from common.exceptions import DomainError

from . import grading
from .grading import plain
from .models import (
    AmendmentStatus, AssessmentComponent, CourseResult, GradingScheme, ResultAmendment, ResultSheet,
    ResultSheetEvent, SheetStatus, StudentMark,
)

HUNDRED = Decimal("100")


def forbidden(message):
    return DomainError(message, code="permission_denied", status_code=403)


# --- Access helpers ---------------------------------------------------------

def is_assigned_lecturer(user, offering):
    return user.role == Role.LECTURER and offering.assignments.filter(lecturer=user).exists()


def can_view_sheet(user, sheet):
    if user.role in (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER):
        return True
    return is_assigned_lecturer(user, sheet.offering)


def visible_sheets(user, queryset):
    role = getattr(user, "role", None)
    if role in (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER):
        return queryset
    if role == Role.LECTURER:
        return queryset.filter(offering__assignments__lecturer=user).distinct()
    return queryset.none()


# --- Sheet setup ------------------------------------------------------------

def get_or_create_sheet(offering):
    try:
        return offering.result_sheet
    except ResultSheet.DoesNotExist:
        pass
    scheme = GradingScheme.objects.filter(is_default=True, is_active=True).first()
    if scheme is None:
        raise DomainError("No default grading scheme is configured. An administrator must set one up first.",
                          code="no_grading_scheme")
    try:
        with transaction.atomic():
            return ResultSheet.objects.create(offering=offering, grading_scheme=scheme)
    except IntegrityError:
        return ResultSheet.objects.get(offering=offering)


def _require_editable(sheet):
    if not sheet.is_editable:
        raise DomainError(f"Results are {sheet.get_status_display().lower()} and can no longer be edited.",
                          code="sheet_locked", status_code=409)


def _active_registrations(offering):
    return (CourseRegistration.objects.filter(offering=offering, status=CourseRegistrationStatus.ACTIVE)
            .select_related("student").order_by("student__last_name", "student__first_name"))


@transaction.atomic
def replace_components(sheet, components, *, actor):
    """components: list of {"id"?, "name", "weight", "max_score", "is_required"}; replaces the whole set."""
    if not (is_assigned_lecturer(actor, sheet.offering) or actor.role in (Role.ADMIN, Role.RESULTS_OFFICER)):
        raise forbidden("Only the assigned lecturer or an examinations officer can configure assessments.")
    _require_editable(sheet)
    names = [c["name"].strip().lower() for c in components]
    if len(names) != len(set(names)):
        raise DomainError("Component names must be unique.")
    total_weight = sum(Decimal(c["weight"]) for c in components)
    if total_weight != HUNDRED:
        raise DomainError(f"Component weights must total 100% (currently {plain(total_weight)}%).",
                          code="invalid_weights")

    existing = {c.id: c for c in sheet.offering.assessment_components.all()}
    keep_ids = {c.get("id") for c in components if c.get("id")}
    unknown = keep_ids - set(existing)
    if unknown:
        raise DomainError("One or more components do not belong to this course offering.")
    before = [{"name": c.name, "weight": str(c.weight), "max_score": str(c.max_score)} for c in existing.values()]

    removed = [c for cid, c in existing.items() if cid not in keep_ids]
    for component in removed:
        component.delete()  # cascades to draft marks for that component
    # Temporarily rename kept components to avoid unique-name clashes while reordering.
    for order, data in enumerate(components):
        if data.get("id"):
            AssessmentComponent.objects.filter(pk=data["id"]).update(name=f"__tmp_{data['id']}")
    for order, data in enumerate(components):
        fields = {"name": data["name"].strip(), "weight": data["weight"], "max_score": data["max_score"],
                  "is_required": data.get("is_required", True), "is_examination": data.get("is_examination", False),
                  "order": order}
        if data.get("id"):
            component = existing[data["id"]]
            if Decimal(data["max_score"]) < component.max_score and component.marks.filter(score__gt=data["max_score"]).exists():
                raise DomainError(f"Some marks for '{component.name}' exceed the new maximum score.")
            for key, value in fields.items():
                setattr(component, key, value)
            component.save()
        else:
            AssessmentComponent.objects.create(offering=sheet.offering, **fields)
    after = [{"name": c["name"], "weight": str(c["weight"]), "max_score": str(c["max_score"])} for c in components]
    log_action("assessment.configured", actor=actor, instance=sheet, summary=f"Configured assessments for {sheet.offering}",
               before={"components": before}, after={"components": after})
    return sheet


def _parse_score(raw, component):
    if raw is None or raw == "":
        return None
    try:
        score = Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise DomainError(f"'{raw}' is not a valid score.") from None
    if score < 0 or score > component.max_score:
        raise DomainError(f"Scores for {component.name} must be between 0 and {plain(component.max_score)}.")
    return score


@transaction.atomic
def save_marks(sheet, entries, *, actor, source="manual"):
    """entries: list of {"registration": id, "component": id, "score": number|null}."""
    if not is_assigned_lecturer(actor, sheet.offering):
        raise forbidden("Only lecturers assigned to this course can enter marks.")
    sheet = ResultSheet.objects.select_for_update().get(pk=sheet.pk)
    _require_editable(sheet)
    if not sheet.offering.semester.result_entry_is_open():
        raise DomainError("The result-entry deadline for this semester has passed.", code="entry_closed", status_code=409)

    components = {c.id: c for c in sheet.offering.assessment_components.all()}
    registrations = set(_active_registrations(sheet.offering).values_list("id", flat=True))
    existing = {(m.registration_id, m.component_id): m
                for m in StudentMark.objects.filter(registration_id__in=registrations, component_id__in=components)}
    errors, changes, to_create, to_update = [], [], [], []
    for index, entry in enumerate(entries):
        registration_id, component_id = entry.get("registration"), entry.get("component")
        component = components.get(component_id)
        if registration_id not in registrations or component is None:
            errors.append({"index": index, "message": "Unknown student or assessment component for this course."})
            continue
        try:
            score = _parse_score(entry.get("score"), component)
        except DomainError as exc:
            errors.append({"index": index, "registration": registration_id, "component": component_id,
                           "message": exc.message})
            continue
        mark = existing.get((registration_id, component_id))
        if mark is None:
            to_create.append(StudentMark(registration_id=registration_id, component=component, score=score,
                                         updated_by=actor))
            changes.append({"registration": registration_id, "component": component_id, "from": None, "to": score})
        elif mark.score != score:
            changes.append({"registration": registration_id, "component": component_id, "from": mark.score, "to": score})
            mark.score, mark.updated_by = score, actor
            to_update.append(mark)
    if errors:
        raise DomainError("Some marks are invalid. Nothing was saved.", code="invalid_marks", errors=errors)
    StudentMark.objects.bulk_create(to_create)
    now = timezone.now()
    for mark in to_update:
        mark.updated_at = now
    StudentMark.objects.bulk_update(to_update, ["score", "updated_by", "updated_at"])
    if changes:
        how = "imported" if source == "import" else "saved"
        log_action("marks.saved", actor=actor, instance=sheet, summary=f"{how.capitalize()} {len(changes)} mark(s) for {sheet.offering}",
                   metadata={"changes": changes[:500], "sheet_status": sheet.status, "source": source})
    return len(changes)


# --- Computation ------------------------------------------------------------

def _bands(scheme):
    return list(scheme.bands.all())


def sheet_rows(sheet):
    """Class list with marks, computed totals and grades (computed on the fly; nothing stored)."""
    components = list(sheet.offering.assessment_components.all())
    registrations = list(_active_registrations(sheet.offering))
    marks = defaultdict(dict)
    for mark in StudentMark.objects.filter(registration__in=registrations, component__in=components):
        marks[mark.registration_id][mark.component_id] = mark.score
    bands = _bands(sheet.grading_scheme)
    retake_cap = AcademicRegulation.load().retake_max_grade_point
    stored = {r.registration_id: r for r in sheet.results.all()}
    rows = []
    for registration in registrations:
        student_marks = marks.get(registration.id, {})
        missing = [c.id for c in components if c.is_required and student_marks.get(c.id) is None]
        total = grading.weighted_total(components, student_marks)
        rounded, band = None, None
        if components and student_marks:
            try:
                rounded, band = grading.grade(total, sheet.grading_scheme, bands)
            except ValueError:
                rounded, band = grading.round_total(total, sheet.grading_scheme), None
        capped = False
        if band is not None and registration.is_retake:
            band, capped = grading.cap_band(band, bands, retake_cap)
        result = stored.get(registration.id)
        rows.append({
            "registration": registration.id,
            "student_id": registration.student_id,
            "student_number": registration.student.student_number,
            "student_name": registration.student.full_name,
            "is_retake": registration.is_retake,
            "marks": {str(cid): (plain(score) if score is not None else None) for cid, score in student_marks.items()},
            "missing_components": missing,
            "total": str(rounded) if rounded is not None else None,
            "grade": band.letter if band else None,
            "grade_point": str(band.grade_point) if band else None,
            "is_pass": band.is_pass if band else None,
            "is_amended": bool(result and result.is_amended),
            "grade_capped": capped,
            "result_id": result.id if result else None,
        })
    return components, rows


def sheet_summary(rows, components):
    graded = [r for r in rows if r["grade"]]
    distribution = Counter(r["grade"] for r in graded)
    totals = [Decimal(r["total"]) for r in rows if r["total"] is not None]
    return {
        "students": len(rows),
        "complete": sum(1 for r in rows if not r["missing_components"]) if components else 0,
        "missing": sum(1 for r in rows if r["missing_components"]),
        "passed": sum(1 for r in graded if r["is_pass"]),
        "failed": sum(1 for r in graded if r["is_pass"] is False),
        "average": str((sum(totals) / len(totals)).quantize(Decimal("0.01"))) if totals else None,
        "highest": str(max(totals)) if totals else None,
        "lowest": str(min(totals)) if totals else None,
        "distribution": dict(distribution),
        "weights_total": plain(sum((c.weight for c in components), Decimal(0))),
    }


def _store_results(sheet):
    """Persist computed CourseResults for every registration (used on submit/approve)."""
    components, rows = sheet_rows(sheet)
    credits = sheet.offering.course.credits
    existing = {r.registration_id: r for r in sheet.results.all()}
    for row in rows:
        values = {
            "total_score": Decimal(row["total"]), "grade": row["grade"], "grade_point": Decimal(row["grade_point"]),
            "is_pass": row["is_pass"], "credits": credits, "grade_capped": row["grade_capped"],
        }
        result = existing.pop(row["registration"], None)
        if result is None:
            CourseResult.objects.create(sheet=sheet, registration_id=row["registration"], student_id=row["student_id"],
                                        offering=sheet.offering, **values)
        else:
            for key, value in values.items():
                setattr(result, key, value)
            result.save()
    # Registrations dropped since the last computation.
    for stale in existing.values():
        stale.delete()


def _validate_complete(sheet):
    components = list(sheet.offering.assessment_components.all())
    if not components:
        raise DomainError("Configure the assessment components before submitting.", code="no_components")
    total_weight = sum((c.weight for c in components), Decimal(0))
    if total_weight != HUNDRED:
        raise DomainError(f"Component weights must total 100% (currently {plain(total_weight)}%).",
                          code="invalid_weights")
    _, rows = sheet_rows(sheet)
    if not rows:
        raise DomainError("No students are registered for this course.", code="no_students")
    incomplete = [r for r in rows if r["missing_components"]]
    if incomplete:
        raise DomainError(
            f"{len(incomplete)} student(s) have missing required marks.", code="missing_marks",
            errors=[{"registration": r["registration"], "student_number": r["student_number"]} for r in incomplete],
        )
    ungraded = [r for r in rows if r["grade"] is None]
    if ungraded:
        raise DomainError("Some totals do not fall within any grade band. Check the grading scheme.", code="ungraded")


# --- Workflow ---------------------------------------------------------------

def _record(sheet, from_status, actor, comment, action):
    ResultSheetEvent.objects.create(sheet=sheet, from_status=from_status, to_status=sheet.status, actor=actor,
                                    comment=comment)
    log_action(f"results.{action}", actor=actor, instance=sheet, reason=comment,
               summary=f"{sheet.offering}: {from_status} -> {sheet.status}",
               before={"status": from_status}, after={"status": sheet.status})


@transaction.atomic
def submit_sheet(sheet, *, actor, comment=""):
    sheet = ResultSheet.objects.select_for_update().select_related("offering__course").get(pk=sheet.pk)
    if not is_assigned_lecturer(actor, sheet.offering):
        raise forbidden("Only a lecturer assigned to this course can submit its results.")
    if sheet.status not in (SheetStatus.DRAFT, SheetStatus.RETURNED):
        raise DomainError("Only draft or returned results can be submitted.", code="invalid_transition", status_code=409)
    _validate_complete(sheet)
    _store_results(sheet)
    previous = sheet.status
    sheet.status = SheetStatus.SUBMITTED
    sheet.submitted_by, sheet.submitted_at, sheet.last_comment = actor, timezone.now(), comment
    sheet.save()
    _record(sheet, previous, actor, comment, "submitted")
    notify_events.sheet_submitted(sheet, actor)
    return sheet


@transaction.atomic
def return_sheet(sheet, *, actor, comment):
    sheet = ResultSheet.objects.select_for_update().get(pk=sheet.pk)
    if not actor.can_approve_results:
        raise forbidden("You are not authorised to review results.")
    if sheet.status not in (SheetStatus.SUBMITTED, SheetStatus.APPROVED):
        raise DomainError("Only submitted or approved (unpublished) results can be returned.",
                          code="invalid_transition", status_code=409)
    if not comment or not comment.strip():
        raise DomainError("Give a reason so the lecturer knows what to correct.", code="comment_required")
    previous = sheet.status
    sheet.status = SheetStatus.RETURNED
    sheet.approved_by, sheet.approved_at, sheet.last_comment = None, None, comment
    sheet.save()
    _record(sheet, previous, actor, comment, "returned")
    notify_events.sheet_returned(sheet, actor)
    return sheet


@transaction.atomic
def approve_sheet(sheet, *, actor, comment=""):
    sheet = ResultSheet.objects.select_for_update().select_related("offering__course").get(pk=sheet.pk)
    if not actor.can_approve_results:
        raise forbidden("You are not authorised to approve results.")
    if sheet.status != SheetStatus.SUBMITTED:
        raise DomainError("Only submitted results can be approved.", code="invalid_transition", status_code=409)
    profile = InstitutionProfile.load()
    if profile.approver_must_differ_from_submitter and sheet.submitted_by_id == actor.pk:
        raise forbidden("Results must be approved by someone other than the person who submitted them.")
    _validate_complete(sheet)
    _store_results(sheet)
    previous = sheet.status
    sheet.status = SheetStatus.APPROVED
    sheet.approved_by, sheet.approved_at, sheet.last_comment = actor, timezone.now(), comment
    sheet.save()
    _record(sheet, previous, actor, comment, "approved")
    notify_events.sheet_approved(sheet, actor)
    return sheet


@transaction.atomic
def publish_sheet(sheet, *, actor, comment=""):
    sheet = ResultSheet.objects.select_for_update().get(pk=sheet.pk)
    if not actor.can_publish_results:
        raise forbidden("You are not authorised to publish results.")
    if sheet.status != SheetStatus.APPROVED:
        raise DomainError("Only approved results can be published.", code="invalid_transition", status_code=409)
    if InstitutionProfile.load().publisher_must_differ_from_submitter and sheet.submitted_by_id == actor.pk:
        raise forbidden("Results must be published by someone other than the person who submitted them.")
    now = timezone.now()
    previous = sheet.status
    sheet.status = SheetStatus.PUBLISHED
    sheet.published_by, sheet.published_at, sheet.last_comment = actor, now, comment
    sheet.save()
    sheet.results.update(is_published=True, published_at=now)
    _record(sheet, previous, actor, comment, "published")
    notify_events.sheet_published(sheet, actor)
    return sheet


# --- Amendments -------------------------------------------------------------

def _capped_grade(result, total, scheme):
    """Grade a recalculated total, keeping the retake/resit cap that applied to the original."""
    bands = _bands(scheme)
    rounded, band = grading.grade(total, scheme, bands)
    regulation = AcademicRegulation.load()
    if result.is_resit:
        band, _ = grading.cap_band(band, bands, regulation.resit_max_grade_point)
    elif result.registration.is_retake:
        band, _ = grading.cap_band(band, bands, regulation.retake_max_grade_point)
    return rounded, band


def _current_marks(result):
    return {str(m.component_id): (str(m.score) if m.score is not None else None)
            for m in StudentMark.objects.filter(registration_id=result.registration_id)}


@transaction.atomic
def request_amendment(result, *, new_marks, reason, actor):
    result = CourseResult.objects.select_related("sheet__grading_scheme", "offering").select_for_update().get(pk=result.pk)
    if not actor.can_request_amendments:
        raise forbidden("You are not authorised to request result amendments.")
    if actor.role == Role.LECTURER and not is_assigned_lecturer(actor, result.offering):
        raise forbidden("You can only request amendments for courses you teach.")
    if result.sheet.status != SheetStatus.PUBLISHED:
        raise DomainError("Amendments apply to published results. Unpublished results can be returned for correction.",
                          code="not_published", status_code=409)
    if not reason or not reason.strip():
        raise DomainError("A reason is required for every amendment.", code="reason_required")
    if result.is_resit:
        raise DomainError("This grade comes from a resit examination; resit grades cannot be amended here.",
                          code="resit_result", status_code=409)
    if result.amendments.filter(status=AmendmentStatus.PENDING).exists():
        raise DomainError("An amendment for this result is already awaiting approval.", code="pending_exists", status_code=409)

    components = {c.id: c for c in result.offering.assessment_components.all()}
    original = _current_marks(result)
    proposed = dict(original)
    for key, raw in (new_marks or {}).items():
        try:
            component = components[int(key)]
        except (KeyError, ValueError):
            raise DomainError("Unknown assessment component in the amendment.") from None
        score = _parse_score(raw, component)
        proposed[str(component.id)] = str(score) if score is not None else None
    if proposed == original:
        raise DomainError("The amendment does not change any marks.")
    missing = [c.name for c in components.values() if c.is_required and proposed.get(str(c.id)) is None]
    if missing:
        raise DomainError(f"Required marks cannot be removed: {', '.join(missing)}.")

    scheme = result.sheet.grading_scheme
    total = grading.weighted_total(components.values(), {int(k): v for k, v in proposed.items()})
    rounded, band = _capped_grade(result, total, scheme)
    amendment = ResultAmendment.objects.create(
        course_result=result, original_marks=original, new_marks=proposed, original_total=result.total_score,
        original_grade=result.grade, new_total=rounded, new_grade=band.letter, reason=reason, requested_by=actor,
    )
    log_action("results.amendment_requested", actor=actor, instance=amendment, reason=reason,
               summary=f"Amendment requested for {result}",
               before={"total": str(result.total_score), "grade": result.grade, "marks": original},
               after={"total": str(rounded), "grade": band.letter, "marks": proposed})
    notify_events.amendment_requested(amendment, actor)
    return amendment


@transaction.atomic
def review_amendment(amendment, *, approve, actor, comment=""):
    amendment = ResultAmendment.objects.select_for_update().select_related(
        "course_result__sheet__grading_scheme", "course_result__offering").get(pk=amendment.pk)
    if not actor.can_approve_amendments:
        raise forbidden("You are not authorised to approve result amendments.")
    if amendment.status != AmendmentStatus.PENDING:
        raise DomainError("This amendment has already been reviewed.", code="already_reviewed", status_code=409)
    profile = InstitutionProfile.load()
    if profile.amendments_require_second_approver and amendment.requested_by_id == actor.pk:
        raise forbidden("An amendment must be approved by someone other than the requester.")
    if not approve and not (comment or "").strip():
        raise DomainError("Give a reason for rejecting the amendment.", code="comment_required")

    amendment.reviewed_by, amendment.reviewed_at, amendment.review_comment = actor, timezone.now(), comment or ""
    result = amendment.course_result
    if not approve:
        amendment.status = AmendmentStatus.REJECTED
        amendment.save()
        log_action("results.amendment_rejected", actor=actor, instance=amendment, reason=comment,
                   summary=f"Amendment rejected for {result}")
        notify_events.amendment_reviewed(amendment, actor)
        return amendment

    if _current_marks(result) != amendment.original_marks:
        raise DomainError("The marks changed after this amendment was requested. Request a new amendment.",
                          code="stale_amendment", status_code=409)
    for key, value in amendment.new_marks.items():
        StudentMark.objects.update_or_create(
            registration_id=result.registration_id, component_id=int(key),
            defaults={"score": Decimal(value) if value is not None else None, "updated_by": actor},
        )
    before = {"total": str(result.total_score), "grade": result.grade, "grade_point": str(result.grade_point)}
    scheme = result.sheet.grading_scheme
    components = list(result.offering.assessment_components.all())
    marks = {int(k): (Decimal(v) if v is not None else None) for k, v in amendment.new_marks.items()}
    rounded, band = _capped_grade(result, grading.weighted_total(components, marks), scheme)
    result.total_score, result.grade, result.grade_point, result.is_pass = rounded, band.letter, band.grade_point, band.is_pass
    result.is_amended = True
    result.save()
    amendment.status = AmendmentStatus.APPLIED
    amendment.save()
    log_action("results.amended", actor=actor, instance=result, reason=amendment.reason,
               summary=f"Published result amended for {result.student.student_number} {result.offering.course.code}",
               before=before, after={"total": str(rounded), "grade": band.letter, "grade_point": str(band.grade_point)},
               metadata={"amendment": amendment.pk, "requested_by": amendment.requested_by_id})
    notify_events.amendment_reviewed(amendment, actor)
    return amendment


# --- Academic records -------------------------------------------------------

def student_course_history(student):
    """Course ids the student has passed, failed (not since passed) or has pending results for."""
    published = (CourseResult.objects.filter(student=student, is_published=True)
                 .values_list("offering__course_id", "is_pass"))
    passed, failed = set(), set()
    for course_id, is_pass in published:
        (passed if is_pass else failed).add(course_id)
    failed -= passed
    return passed, failed


def build_academic_record(student, *, published_only=True):
    profile = InstitutionProfile.load()
    places = profile.gpa_decimal_places
    results = CourseResult.objects.filter(student=student).select_related(
        "offering__course", "offering__semester__academic_year", "registration")
    if published_only:
        results = results.filter(is_published=True)
    results = sorted(results, key=lambda r: (r.offering.semester.start_date, r.offering.course.code))

    semesters, cumulative = [], []
    by_semester = defaultdict(list)
    for result in results:
        by_semester[result.offering.semester].append(result)
    for semester in sorted(by_semester, key=lambda s: s.start_date):
        items = by_semester[semester]
        cumulative.extend(items)
        counted = grading.apply_retake_policy(cumulative, profile.cgpa_retake_policy)
        semesters.append({
            "semester_id": semester.id,
            "semester": semester.name,
            "academic_year": semester.academic_year.name,
            "courses": [{
                "course_code": r.offering.course.code,
                "course_title": r.offering.course.title,
                "credits": r.credits,
                "total": str(r.total_score),
                "grade": r.grade,
                "grade_point": str(r.grade_point),
                "is_pass": r.is_pass,
                "is_retake": r.registration.is_retake,
                "is_published": r.is_published,
                "is_amended": r.is_amended,
                "is_resit": r.is_resit,
                "grade_capped": r.grade_capped,
            } for r in items],
            "credits_attempted": sum(r.credits for r in items),
            "credits_earned": sum(r.credits for r in items if r.is_pass),
            "gpa": _str(grading.gpa(((r.grade_point, r.credits) for r in items), places)),
            "cgpa": _str(grading.gpa(((r.grade_point, r.credits) for r in counted), places)),
        })
    counted = grading.apply_retake_policy(cumulative, profile.cgpa_retake_policy)
    earned_courses = {r.offering.course_id: r.credits for r in cumulative if r.is_pass}
    return {
        "student": {
            "id": student.id, "student_number": student.student_number, "full_name": student.full_name,
            "programme": student.programme.name, "programme_code": student.programme.code,
            "level": student.current_level.name, "status": student.status, "admission_year": student.admission_year,
            "academic_standing": student.academic_standing,
        },
        "semesters": semesters,
        "summary": {
            "cgpa": _str(grading.gpa(((r.grade_point, r.credits) for r in counted), places)),
            "credits_attempted": sum(r.credits for r in counted),
            "credits_earned": sum(earned_courses.values()),
            "required_credits": student.programme.required_credits,
            "retake_policy": profile.cgpa_retake_policy,
        },
    }


def _str(value):
    return str(value) if value is not None else None
