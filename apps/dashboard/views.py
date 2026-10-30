"""Role-specific dashboard figures, all computed from live data."""
from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import CourseOffering, Semester
from apps.accounts import appointments
from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.registration.models import CourseRegistration, RegistrationStatus, SemesterRegistration
from apps.results import signoff
from apps.results.models import AmendmentStatus, ResultAmendment, ResultSheet, SheetStatus, StudentMark
from apps.results.services import build_academic_record
from apps.students.models import StatusPolicy, Student, StudentStatus


def _semester_payload(semester):
    if semester is None:
        return None
    return {
        "id": semester.id, "name": semester.name, "academic_year": semester.academic_year.name,
        "label": str(semester), "status": semester.status, "start_date": semester.start_date,
        "end_date": semester.end_date, "registration_start": semester.registration_start,
        "registration_end": semester.registration_end, "add_drop_deadline": semester.add_drop_deadline,
        "result_entry_deadline": semester.result_entry_deadline,
        "registration_open": semester.registration_is_open(),
    }


def _sheet_counts(queryset=None):
    queryset = queryset if queryset is not None else ResultSheet.objects.all()
    counts = dict(queryset.values("status").annotate(n=Count("id")).values_list("status", "n"))
    return {status: counts.get(status, 0) for status in SheetStatus.values}


def _registry_dashboard(user, semester):
    active = Student.objects.filter(status=StudentStatus.ACTIVE, is_archived=False)
    by_programme = list(active.values("programme__code", "programme__name").annotate(count=Count("id"))
                        .order_by("-count")[:12])
    by_level = list(active.values("current_level__name", "current_level__order").annotate(count=Count("id"))
                    .order_by("current_level__order"))
    by_status = dict(Student.objects.values("status").annotate(n=Count("id")).values_list("status", "n"))
    registration = None
    offerings = 0
    if semester:
        regs = SemesterRegistration.objects.filter(semester=semester)
        eligible = Student.objects.filter(
            status__in=StatusPolicy.objects.filter(can_register=True).values("status"), is_archived=False).count()
        registered = regs.filter(status=RegistrationStatus.REGISTERED).count()
        registration = {
            "registered": registered,
            "pending": regs.filter(status=RegistrationStatus.PENDING).count(),
            "eligible": eligible,
            "not_registered": max(eligible - registered - regs.filter(status=RegistrationStatus.PENDING).count(), 0),
        }
        offerings = CourseOffering.objects.filter(semester=semester).count()
    data = {
        "kind": "registry",
        "current_semester": _semester_payload(semester),
        "students": {"active": active.count(), "total": Student.objects.count(), "by_status": by_status,
                     "by_programme": by_programme, "by_level": by_level},
        "registration": registration,
        "offerings": offerings,
        "results": _sheet_counts(),
        "pending_amendments": ResultAmendment.objects.filter(status=AmendmentStatus.PENDING).count(),
        "signoff_blocked": signoff.blocked_sheets(),
    }
    if user.role == Role.ADMIN:
        data["appointments_review"] = appointments.needs_review()
        data["recent_activity"] = [
            {"id": log.id, "created_at": log.created_at, "actor": log.actor_email, "action": log.action,
             "summary": log.summary}
            for log in AuditLog.objects.exclude(action__startswith="auth.")[:8]
        ]
    return data


def _results_officer_dashboard(user, semester):
    awaiting = (ResultSheet.objects.filter(status=SheetStatus.SUBMITTED)
                .select_related("offering__course", "submitted_by").order_by("submitted_at")[:10])
    return {
        "kind": "results_officer",
        "current_semester": _semester_payload(semester),
        "results": _sheet_counts(),
        "semester_results": _sheet_counts(ResultSheet.objects.filter(offering__semester=semester)) if semester else None,
        "pending_amendments": ResultAmendment.objects.filter(status=AmendmentStatus.PENDING).count(),
        "signoff_blocked": signoff.blocked_sheets(),
        "awaiting_review": [{"id": s.id, "course_code": s.offering.course.code, "course_title": s.offering.course.title,
                             "submitted_by": s.submitted_by.get_full_name() if s.submitted_by else None,
                             "submitted_at": s.submitted_at} for s in awaiting],
    }


def _lecturer_dashboard(user, semester):
    offerings = (CourseOffering.objects.filter(assignments__lecturer=user)
                 .select_related("course", "semester__academic_year", "result_sheet")
                 .annotate(class_size=Count("registrations", filter=Q(registrations__status="ACTIVE"), distinct=True))
                 .order_by("-semester__start_date", "course__code"))
    if semester:
        offerings = offerings.filter(semester=semester)
    items = []
    for offering in offerings:
        sheet = getattr(offering, "result_sheet", None)
        components = offering.assessment_components.filter(is_required=True).count()
        expected = components * offering.class_size
        entered = StudentMark.objects.filter(
            component__offering=offering, component__is_required=True, score__isnull=False,
            registration__status="ACTIVE").count() if expected else 0
        status = sheet.status if sheet else SheetStatus.DRAFT
        items.append({
            "offering": offering.id, "course_code": offering.course.code, "course_title": offering.course.title,
            "semester": str(offering.semester), "class_size": offering.class_size, "result_status": status,
            "sheet_id": sheet.id if sheet else None,
            "marks_progress": round(100 * entered / expected) if expected else 0,
            "needs_action": status in (SheetStatus.DRAFT, SheetStatus.RETURNED),
        })
    return {
        "kind": "lecturer",
        "current_semester": _semester_payload(semester),
        "courses": items,
        "totals": {
            "courses": len(items),
            "students": sum(i["class_size"] for i in items),
            "draft": sum(1 for i in items if i["result_status"] == SheetStatus.DRAFT),
            "returned": sum(1 for i in items if i["result_status"] == SheetStatus.RETURNED),
            "submitted": sum(1 for i in items if i["result_status"] == SheetStatus.SUBMITTED),
            "published": sum(1 for i in items if i["result_status"] == SheetStatus.PUBLISHED),
        },
    }


def _student_dashboard(user, semester):
    student = Student.objects.select_related("programme", "current_level").filter(user=user).first()
    if student is None:
        return {"kind": "student", "student": None, "current_semester": _semester_payload(semester)}
    registration = None
    if semester:
        reg = SemesterRegistration.objects.filter(student=student, semester=semester).first()
        if reg:
            courses = (CourseRegistration.objects.filter(semester_registration=reg, status="ACTIVE")
                       .select_related("offering__course"))
            registration = {
                "id": reg.id, "status": reg.status, "status_display": reg.get_status_display(),
                "total_credits": reg.total_credits,
                "courses": [{"course_code": c.offering.course.code, "course_title": c.offering.course.title,
                             "credits": c.offering.course.credits, "category": c.category} for c in courses],
            }
    policy = StatusPolicy.for_status(student.status)
    record = build_academic_record(student, published_only=True) if policy.can_view_results else None
    return {
        "kind": "student",
        "current_semester": _semester_payload(semester),
        "student": {"id": student.id, "student_number": student.student_number, "full_name": student.full_name,
                    "programme": student.programme.name, "programme_code": student.programme.code,
                    "level": student.current_level.name, "status": student.status,
                    "status_display": student.get_status_display(), "can_register": policy.can_register},
        "registration": registration,
        "results": {
            "cgpa": record["summary"]["cgpa"], "credits_earned": record["summary"]["credits_earned"],
            "required_credits": record["summary"]["required_credits"],
            "latest": record["semesters"][-1] if record["semesters"] else None,
        } if record else None,
    }


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: None})
    def get(self, request):
        semester = Semester.objects.select_related("academic_year").filter(is_current=True).first()
        user = request.user
        if user.teaches_only:
            return Response(_lecturer_dashboard(user, semester))
        if user.has_any_role(Role.ADMIN, Role.REGISTRAR):
            data = _registry_dashboard(user, semester)
        elif user.has_any_role(Role.RESULTS_OFFICER):
            data = _results_officer_dashboard(user, semester)
        else:
            return Response(_student_dashboard(user, semester))
        # Office holders who also teach see their own courses below their office's overview.
        if Role.LECTURER in user.roles:
            courses = _lecturer_dashboard(user, semester)["courses"]
            if courses:
                data["teaching"] = courses
        return Response(data)
