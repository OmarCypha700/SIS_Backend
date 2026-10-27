from django.db.models import Count, Prefetch, Q
from django_filters import rest_framework as django_filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.academics.models import CourseOffering, Semester
from apps.accounts.models import Role
from apps.audit.services import log_action
from apps.students.models import StatusPolicy, Student
from common import pdf
from common.pdf import pdf_response
from common.tabular import requested_format, table_response
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission

from . import services
from .models import (
    CourseRegistration, CourseRegistrationStatus, RegistrationEvent, RegistrationStatus, SemesterRegistration,
)
from .serializers import (
    AdminRegistrationSerializer, ReviewSerializer, SemesterRegistrationDetailSerializer,
    SemesterRegistrationListSerializer, SubmitRegistrationSerializer,
)

REGISTRY = (Role.ADMIN, Role.REGISTRAR)
ACTIVE_REG_STATUSES = (RegistrationStatus.PENDING, RegistrationStatus.REGISTERED)


def _current_semester():
    semester = Semester.objects.select_related("academic_year").filter(is_current=True).first()
    if semester is None:
        raise DomainError("No current semester has been set.", code="no_current_semester", status_code=404)
    return semester


def _semester_from(request, value=None):
    value = value or request.query_params.get("semester")
    if value in (None, "", "current"):
        return _current_semester()
    try:
        return Semester.objects.select_related("academic_year").get(pk=int(value))
    except (Semester.DoesNotExist, ValueError):
        raise DomainError("Semester not found.", code="not_found", status_code=404) from None


def _options_payload(student, semester):
    ctx = services.build_options(student, semester)
    is_open, message = services.window_state(semester, ctx.registration)
    options = sorted(ctx.options.values(), key=lambda o: (not o.is_compulsory, o.category, o.offering.course.code))
    return {
        "semester": {
            "id": semester.id, "name": str(semester), "registration_start": semester.registration_start,
            "registration_end": semester.registration_end, "add_drop_deadline": semester.add_drop_deadline,
        },
        "student": {
            "id": student.id, "student_number": student.student_number, "full_name": student.full_name,
            "programme": student.programme.name, "level": student.current_level.name, "status": student.status,
        },
        "can_register": ctx.can_register,
        "window_open": is_open,
        "window_message": message,
        "credit_limits": {"min": student.programme.min_credits_per_semester,
                          "max": student.programme.max_credits_per_semester},
        "registration": (SemesterRegistrationDetailSerializer(_detail_queryset().get(pk=ctx.registration.pk)).data
                         if ctx.registration else None),
        "options": [o.as_dict() for o in options],
    }


def _detail_queryset():
    return SemesterRegistration.objects.select_related(
        "student", "semester__academic_year", "programme", "level"
    ).prefetch_related(
        Prefetch("courses", queryset=CourseRegistration.objects.select_related("offering__course")),
        Prefetch("events", queryset=RegistrationEvent.objects.select_related("actor", "offering__course")),
    )


class RegistrationFilter(django_filters.FilterSet):
    programme = django_filters.NumberFilter(field_name="programme")
    level = django_filters.NumberFilter(field_name="level")
    course = django_filters.NumberFilter(field_name="courses__offering__course", distinct=True)
    offering = django_filters.NumberFilter(field_name="courses__offering", distinct=True)

    class Meta:
        model = SemesterRegistration
        fields = ["semester", "status", "student"]


class RegistrationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Semester registrations. Students see only their own; registry staff see everything.
    Students register through /registration/current/ and /registration/submit/.
    """

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN, Role.REGISTRAR, Role.STUDENT)
    action_roles = {
        "current": (Role.STUDENT,),
        "submit": (Role.STUDENT,),
        "options_for_student": REGISTRY,
        "admin_register": REGISTRY,
        "review": REGISTRY,
        "export": REGISTRY,
        "not_registered": REGISTRY,
        "statistics": REGISTRY,
    }
    filterset_class = RegistrationFilter
    search_fields = ["student__student_number", "student__first_name", "student__last_name"]
    ordering_fields = ["submitted_at", "student__last_name", "total_credits", "status"]

    def get_queryset(self):
        if self.action == "retrieve":
            queryset = _detail_queryset()
        else:
            queryset = SemesterRegistration.objects.select_related(
                "student", "semester__academic_year", "programme", "level"
            ).annotate(course_count=Count("courses", filter=Q(courses__status=CourseRegistrationStatus.ACTIVE))
                       ).order_by("-semester__start_date", "student__last_name", "id")
        if getattr(self.request.user, "role", None) == Role.STUDENT:
            queryset = queryset.filter(student__user=self.request.user)
        return queryset

    def get_serializer_class(self):
        return SemesterRegistrationDetailSerializer if self.action == "retrieve" else SemesterRegistrationListSerializer

    def _own_student(self, request):
        student = Student.objects.select_related("programme", "current_level").filter(user=request.user).first()
        if student is None:
            raise DomainError("No student record is linked to this account.", code="not_found", status_code=404)
        return student

    @extend_schema(parameters=[OpenApiParameter("semester", str, description="Semester id or 'current'")],
                   responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def current(self, request):
        return Response(_options_payload(self._own_student(request), _semester_from(request)))

    @extend_schema(request=SubmitRegistrationSerializer, responses=SemesterRegistrationDetailSerializer)
    @action(detail=False, methods=["post"])
    def submit(self, request):
        payload = SubmitRegistrationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        semester = payload.validated_data.get("semester") or _current_semester()
        registration = services.submit_registration(
            self._own_student(request), semester, payload.validated_data["offering_ids"], actor=request.user
        )
        return Response(SemesterRegistrationDetailSerializer(_detail_queryset().get(pk=registration.pk)).data)

    @extend_schema(parameters=[OpenApiParameter("student", int, required=True), OpenApiParameter("semester", str)],
                   responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"], url_path="options")
    def options_for_student(self, request):
        try:
            student = Student.objects.select_related("programme", "current_level").get(
                pk=int(request.query_params.get("student", "")))
        except (Student.DoesNotExist, ValueError):
            raise DomainError("Student not found.", code="not_found", status_code=404) from None
        return Response(_options_payload(student, _semester_from(request)))

    @extend_schema(request=AdminRegistrationSerializer, responses=SemesterRegistrationDetailSerializer)
    @action(detail=False, methods=["post"], url_path="on-behalf")
    def admin_register(self, request):
        payload = AdminRegistrationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        registration = services.submit_registration(
            data["student"], data["semester"], data["offering_ids"], actor=request.user, on_behalf=True,
            reason=data["reason"],
        )
        return Response(SemesterRegistrationDetailSerializer(_detail_queryset().get(pk=registration.pk)).data)

    @extend_schema(request=ReviewSerializer, responses=SemesterRegistrationDetailSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        registration = self.get_object()
        payload = ReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.review_registration(registration, actor=request.user, **payload.validated_data)
        return Response(SemesterRegistrationDetailSerializer(_detail_queryset().get(pk=registration.pk)).data)

    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="slip-pdf")
    def slip_pdf(self, request, pk=None):
        registration = self.get_object()  # students only reach their own registrations
        registration = _detail_queryset().get(pk=registration.pk)
        return pdf_response(pdf.registration_slip(registration),
                            f"registration-slip-{registration.student.student_number}.pdf")

    @action(detail=False, methods=["get"])
    def export(self, request):
        rows = (
            CourseRegistration.objects.filter(status=CourseRegistrationStatus.ACTIVE,
                                              semester_registration__in=self.filter_queryset(self.get_queryset()))
            .select_related("student", "offering__course", "semester_registration__semester__academic_year",
                            "semester_registration__programme", "semester_registration__level")
            .order_by("student__student_number", "offering__course__code")
        )
        log_action("registration.exported", actor=request.user, entity_type="registration.semesterregistration",
                   summary="Exported registrations", metadata={"filters": dict(request.query_params)})
        return table_response(
            "registrations",
            ["student_number", "student_name", "programme", "level", "semester", "course_code", "course_title",
             "credits", "category", "retake", "registration_status"],
            ([cr.student.student_number, cr.student.full_name, cr.semester_registration.programme.code,
              cr.semester_registration.level.name, str(cr.semester_registration.semester), cr.offering.course.code,
              cr.offering.course.title, cr.offering.course.credits, cr.category, "yes" if cr.is_retake else "no",
              cr.semester_registration.status] for cr in rows.iterator(chunk_size=500)),
            requested_format(request), sheet_title="Registrations",
        )

    @extend_schema(parameters=[OpenApiParameter("semester", str)])
    @action(detail=False, methods=["get"], url_path="not-registered")
    def not_registered(self, request):
        """Students allowed to register who have no active registration for the semester."""
        semester = _semester_from(request)
        allowed_statuses = StatusPolicy.objects.filter(can_register=True).values_list("status", flat=True)
        registered = SemesterRegistration.objects.filter(semester=semester, status__in=ACTIVE_REG_STATUSES) \
            .values("student_id")
        queryset = (Student.objects.filter(status__in=allowed_statuses, is_archived=False)
                    .exclude(pk__in=registered).select_related("programme", "current_level")
                    .order_by("programme__code", "current_level__order", "last_name"))
        for key, field in (("programme", "programme"), ("level", "current_level")):
            if request.query_params.get(key):
                queryset = queryset.filter(**{f"{field}_id": request.query_params[key]})
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response([
            {"id": s.id, "student_number": s.student_number, "full_name": s.full_name, "programme_code": s.programme.code,
             "level_name": s.current_level.name, "status": s.status, "email": s.email}
            for s in page
        ])

    @extend_schema(parameters=[OpenApiParameter("semester", str)])
    @action(detail=False, methods=["get"])
    def statistics(self, request):
        semester = _semester_from(request)
        regs = SemesterRegistration.objects.filter(semester=semester)
        by_status = dict(regs.values("status").annotate(n=Count("id")).values_list("status", "n"))
        offerings = (CourseOffering.objects.filter(semester=semester).select_related("course")
                     .annotate(enrolled=Count("registrations", filter=Q(registrations__status="ACTIVE")))
                     .order_by("-enrolled")[:50])
        eligible = Student.objects.filter(
            status__in=StatusPolicy.objects.filter(can_register=True).values("status"), is_archived=False).count()
        return Response({
            "semester": {"id": semester.id, "name": str(semester)},
            "by_status": by_status,
            "registered": by_status.get(RegistrationStatus.REGISTERED, 0),
            "pending": by_status.get(RegistrationStatus.PENDING, 0),
            "eligible_students": eligible,
            "offerings": [{"offering": o.id, "course_code": o.course.code, "course_title": o.course.title,
                           "enrolled": o.enrolled, "capacity": o.capacity} for o in offerings],
        })


class ClassListViewSet(viewsets.ViewSet):
    """Class lists per course offering for assigned lecturers and registry staff."""

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER, Role.LECTURER)

    def get_queryset(self):
        queryset = CourseOffering.objects.select_related("course", "semester__academic_year")
        if getattr(self.request.user, "role", None) == Role.LECTURER:
            queryset = queryset.filter(assignments__lecturer=self.request.user)
        return queryset

    @extend_schema(parameters=[OpenApiParameter("export", str, description="'csv' to download")],
                   responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        offering = self.get_queryset().filter(pk=pk).first()
        if offering is None:
            raise DomainError("Course offering not found.", code="not_found", status_code=404)
        registrations = (CourseRegistration.objects.filter(offering=offering, status=CourseRegistrationStatus.ACTIVE)
                         .select_related("student__programme", "student__current_level")
                         .order_by("student__last_name", "student__first_name"))
        rows = [{
            "registration": cr.id, "student_id": cr.student_id, "student_number": cr.student.student_number,
            "full_name": cr.student.full_name, "email": cr.student.email, "programme_code": cr.student.programme.code,
            "level_name": cr.student.current_level.name, "is_retake": cr.is_retake, "category": cr.category,
        } for cr in registrations]
        export = request.query_params.get("export")
        if export in ("csv", "xlsx"):
            return table_response(
                f"class-list-{offering.course.code}",
                ["student_number", "full_name", "email", "programme", "level", "retake"],
                ([r["student_number"], r["full_name"], r["email"], r["programme_code"], r["level_name"],
                  "yes" if r["is_retake"] else "no"] for r in rows),
                export, sheet_title="Class list",
            )
        return Response({
            "offering": {"id": offering.id, "course_code": offering.course.code, "course_title": offering.course.title,
                         "semester": str(offering.semester), "capacity": offering.capacity},
            "count": len(rows),
            "students": rows,
        })
