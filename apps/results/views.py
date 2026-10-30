from django.conf import settings
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from django_filters import rest_framework as django_filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.academics.models import CourseOffering, LecturerAssignment
from apps.accounts.models import STAFF_ROLES, Role
from apps.audit.mixins import AuditedModelMixin
from apps.audit.services import log_action
from apps.institutions.models import AcademicRegulation, InstitutionProfile
from apps.students.models import StatusPolicy, Student
from common import pdf
from common.pdf import pdf_response
from common.tabular import read_table, requested_format, table_response
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission

from . import marks_import, resits, services, signoff
from .grading import plain
from .models import CourseResult, GradingScheme, ResitSheet, ResultAmendment, ResultSheet, SheetStatus
from .serializers import (
    AmendmentRequestSerializer, AmendmentReviewSerializer, AmendmentSerializer, AssessmentComponentSerializer,
    ComponentsInputSerializer, GradingSchemeSerializer, MarksInputSerializer, ResultSheetEventSerializer,
    MyResitSerializer, OpenResitSerializer, ResitRegisterSerializer, ResitScoresInputSerializer,
    ResitSheetListSerializer, ResultSheetListSerializer, TransitionSerializer,
)

RESULTS_STAFF = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER, Role.LECTURER)


class GradingSchemeViewSet(AuditedModelMixin, viewsets.ModelViewSet):
    serializer_class = GradingSchemeSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = STAFF_ROLES
    write_roles = (Role.ADMIN,)
    filterset_fields = ["is_active", "is_default"]
    search_fields = ["name"]
    audit_name = "grading_scheme"

    def get_queryset(self):
        return GradingScheme.objects.prefetch_related("bands")

    def perform_destroy(self, instance):
        if instance.sheets.exists():
            raise DomainError("This scheme is used by result sheets and cannot be deleted. Deactivate it instead.",
                              code="protected", status_code=409)
        super().perform_destroy(instance)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"], url_path="set-default")
    @transaction.atomic
    def set_default(self, request, pk=None):
        scheme = self.get_object()
        if not scheme.is_active:
            raise DomainError("Activate the scheme before making it the default.")
        GradingScheme.objects.filter(is_default=True).exclude(pk=scheme.pk).update(is_default=False)
        scheme.is_default = True
        scheme.save(update_fields=["is_default", "updated_at"])
        log_action("grading_scheme.set_default", actor=request.user, instance=scheme,
                   summary=f"Set default grading scheme to {scheme}")
        return Response(self.get_serializer(scheme).data)


class ResultSheetFilter(django_filters.FilterSet):
    semester = django_filters.NumberFilter(field_name="offering__semester")
    department = django_filters.NumberFilter(field_name="offering__course__department")
    course = django_filters.NumberFilter(field_name="offering__course")
    programme = django_filters.NumberFilter(field_name="offering__programmes", distinct=True)
    level = django_filters.NumberFilter(field_name="offering__levels", distinct=True)

    class Meta:
        model = ResultSheet
        fields = ["status"]


class ResultSheetViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [RoleBasedPermission]
    read_roles = RESULTS_STAFF
    write_roles = RESULTS_STAFF  # fine-grained checks happen in the service layer
    serializer_class = ResultSheetListSerializer
    filterset_class = ResultSheetFilter
    search_fields = ["offering__course__code", "offering__course__title"]
    ordering_fields = ["updated_at", "submitted_at", "offering__course__code", "status"]
    ordering = ["-updated_at"]

    def get_queryset(self):
        queryset = ResultSheet.objects.select_related(
            "offering__course", "offering__semester__academic_year", "grading_scheme", "submitted_by",
        ).prefetch_related(
            Prefetch("offering__assignments", queryset=LecturerAssignment.objects.select_related("lecturer"))
        ).annotate(student_count=Count("offering__registrations",
                                       filter=Q(offering__registrations__status="ACTIVE"), distinct=True))
        return services.visible_sheets(self.request.user, queryset)

    def _detail(self, sheet):
        user = self.request.user
        sheet = ResultSheet.objects.select_related(
            "offering__course", "offering__semester__academic_year", "grading_scheme", "submitted_by",
            "approved_by", "published_by",
        ).prefetch_related("grading_scheme__bands", "events__actor",
                           Prefetch("offering__assignments", queryset=LecturerAssignment.objects.select_related("lecturer"))
                           ).get(pk=sheet.pk)
        components, rows = services.sheet_rows(sheet)
        assigned = services.is_assigned_lecturer(user, sheet.offering)
        semester = sheet.offering.semester
        profile = InstitutionProfile.load()
        return {
            **ResultSheetListSerializer(sheet).data,
            "credits": sheet.offering.course.credits,
            "result_entry_deadline": semester.result_entry_deadline,
            "entry_open": semester.result_entry_is_open(),
            "components": AssessmentComponentSerializer(components, many=True).data,
            "grade_bands": [{"letter": b.letter, "min_score": str(b.min_score), "max_score": str(b.max_score),
                             "grade_point": str(b.grade_point), "is_pass": b.is_pass}
                            for b in sheet.grading_scheme.bands.all()],
            "rows": rows,
            "summary": services.sheet_summary(rows, components),
            "events": ResultSheetEventSerializer(sheet.events.all(), many=True).data,
            "resit_sheet_id": ResitSheet.objects.filter(offering=sheet.offering).values_list("id", flat=True).first(),
            "signoff_warnings": signoff.gaps(sheet, profile=profile),
            "permissions": {
                "can_configure": sheet.is_editable and (assigned or user.role in (Role.ADMIN, Role.RESULTS_OFFICER)),
                "can_enter_marks": sheet.is_editable and assigned and semester.result_entry_is_open(),
                "can_submit": sheet.is_editable and assigned,
                "can_return": user.can_approve_results and sheet.status in (SheetStatus.SUBMITTED, SheetStatus.APPROVED),
                "can_approve": user.can_approve_results and sheet.status == SheetStatus.SUBMITTED and not (
                    profile.approver_must_differ_from_submitter and sheet.submitted_by_id == user.pk),
                "can_publish": user.can_publish_results and sheet.status == SheetStatus.APPROVED and not (
                    profile.publisher_must_differ_from_submitter and sheet.submitted_by_id == user.pk),
                "can_open_resit": (user.role in resits.RESULTS_ROLES and sheet.status == SheetStatus.PUBLISHED
                                   and AcademicRegulation.load().resits_enabled
                                   and not ResitSheet.objects.filter(offering=sheet.offering).exists()),
                "can_request_amendment": user.can_request_amendments and sheet.status == SheetStatus.PUBLISHED and (
                    assigned or user.role != Role.LECTURER),
            },
        }

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        return Response(self._detail(self.get_object()))

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get", "post"], url_path=r"for-offering/(?P<offering_id>\d+)")
    def for_offering(self, request, offering_id=None):
        """Open (creating if needed) the result sheet for a course offering."""
        offerings = CourseOffering.objects.all()
        if request.user.role == Role.LECTURER:
            offerings = offerings.filter(assignments__lecturer=request.user)
        offering = offerings.filter(pk=offering_id).first()
        if offering is None:
            raise DomainError("Course offering not found.", code="not_found", status_code=404)
        return Response(self._detail(services.get_or_create_sheet(offering)))

    @extend_schema(request=ComponentsInputSerializer)
    @action(detail=True, methods=["put"])
    def components(self, request, pk=None):
        sheet = self.get_object()
        payload = ComponentsInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.replace_components(sheet, payload.validated_data["components"], actor=request.user)
        return Response(self._detail(sheet))

    @extend_schema(request=MarksInputSerializer)
    @action(detail=True, methods=["put"])
    def marks(self, request, pk=None):
        sheet = self.get_object()
        payload = MarksInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        changed = services.save_marks(sheet, payload.validated_data["marks"], actor=request.user,
                                      source=payload.validated_data["source"])
        return Response({"changed": changed, **self._detail(sheet)})

    @extend_schema(parameters=[OpenApiParameter("file_format", str, description="csv or xlsx")],
                   responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="marks-template")
    def marks_template(self, request, pk=None):
        sheet = self.get_object()
        header, rows = marks_import.template_rows(sheet)
        return table_response(f"marks-{sheet.offering.course.code}", header, rows, requested_format(request),
                              sheet_title="Marks")

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}},
                   responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="marks-import", parser_classes=[MultiPartParser, FormParser])
    def marks_import(self, request, pk=None):
        """Check an uploaded CSV/Excel file of marks. Nothing is saved; send `entries` to PUT marks/ to apply."""
        sheet = self.get_object()
        upload = request.FILES.get("file")
        if upload is None:
            raise DomainError("Choose a CSV or Excel file.", code="file_required")
        try:
            records = read_table(upload, max_bytes=settings.MAX_UPLOAD_CSV_BYTES, max_rows=5000)
        except ValueError as exc:
            raise DomainError(str(exc), code="invalid_file") from exc
        return Response(marks_import.preview_import(sheet, records, actor=request.user))

    def _transition(self, request, fn, **extra):
        sheet = self.get_object()
        payload = TransitionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        fn(sheet, actor=request.user, comment=payload.validated_data.get("comment", ""), **extra)
        return Response(self._detail(sheet))

    @extend_schema(request=TransitionSerializer)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return self._transition(request, services.submit_sheet)

    @extend_schema(request=TransitionSerializer)
    @action(detail=True, methods=["post"], url_path="return")
    def return_for_correction(self, request, pk=None):
        return self._transition(request, services.return_sheet)

    @extend_schema(request=TransitionSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._transition(request, services.approve_sheet)

    @extend_schema(request=TransitionSerializer)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return self._transition(request, services.publish_sheet)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        sheet = self.get_object()
        components, rows = services.sheet_rows(sheet)
        log_action("results.exported", actor=request.user, instance=sheet, summary=f"Exported results for {sheet.offering}")
        return table_response(
            f"results-{sheet.offering.course.code}",
            ["student_number", "student_name", *[f"{c.name} (/{plain(c.max_score)})" for c in components],
             "total", "grade", "grade_point"],
            ([r["student_number"], r["student_name"], *[r["marks"].get(str(c.id)) for c in components],
              r["total"], r["grade"], r["grade_point"]] for r in rows),
            requested_format(request), sheet_title="Results",
        )


class AmendmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = AmendmentSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = RESULTS_STAFF
    write_roles = RESULTS_STAFF
    filterset_fields = ["status"]
    search_fields = ["course_result__student__student_number", "course_result__offering__course__code"]

    def get_queryset(self):
        queryset = ResultAmendment.objects.select_related(
            "course_result__student", "course_result__offering__course",
            "course_result__offering__semester__academic_year", "requested_by", "reviewed_by",
        )
        user = self.request.user
        if getattr(user, "role", None) == Role.LECTURER:
            queryset = queryset.filter(
                Q(requested_by=user) | Q(course_result__offering__assignments__lecturer=user)).distinct()
        return queryset

    @extend_schema(request=AmendmentRequestSerializer, responses=AmendmentSerializer)
    def create(self, request):
        payload = AmendmentRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        results = CourseResult.objects.all()
        if request.user.role == Role.LECTURER:
            results = results.filter(offering__assignments__lecturer=request.user)
        result = results.filter(pk=data["course_result"]).first()
        if result is None:
            raise DomainError("Result not found.", code="not_found", status_code=404)
        amendment = services.request_amendment(result, new_marks=data["new_marks"], reason=data["reason"],
                                               actor=request.user)
        return Response(AmendmentSerializer(self.get_queryset().get(pk=amendment.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AmendmentReviewSerializer, responses=AmendmentSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        amendment = self.get_object()
        payload = AmendmentReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.review_amendment(amendment, actor=request.user, **payload.validated_data)
        return Response(AmendmentSerializer(self.get_queryset().get(pk=amendment.pk)).data)


class MyResultsViewSet(viewsets.ViewSet):
    """A student's own published results, slips and transcript."""

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.STUDENT,)

    def _student(self, request):
        student = Student.objects.select_related("programme", "current_level").filter(user=request.user).first()
        if student is None:
            raise DomainError("No student record is linked to this account.", code="not_found", status_code=404)
        if not StatusPolicy.for_status(student.status).can_view_results:
            raise DomainError("Result access is not available for your current academic status. "
                              "Please contact the registry.", code="results_blocked", status_code=403)
        return student

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def list(self, request):
        return Response(services.build_academic_record(self._student(request), published_only=True))

    @extend_schema(parameters=[OpenApiParameter("semester", int, required=True)], responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def slip(self, request):
        student = self._student(request)
        record = services.build_academic_record(student, published_only=True)
        try:
            semester_id = int(request.query_params.get("semester", ""))
        except ValueError:
            raise DomainError("Choose a semester.", code="semester_required") from None
        entry = next((s for s in record["semesters"] if s["semester_id"] == semester_id), None)
        if entry is None:
            raise DomainError("No published results for that semester.", code="not_found", status_code=404)
        profile = InstitutionProfile.load()
        return Response({"student": record["student"], "semester": entry, "summary": record["summary"],
                         "institution": {"name": profile.name, "footer": profile.result_slip_footer}})

    @extend_schema(parameters=[OpenApiParameter("semester", int, required=True)],
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], url_path="slip-pdf")
    def slip_pdf(self, request):
        record = services.build_academic_record(self._student(request), published_only=True)
        try:
            semester_id = int(request.query_params.get("semester", ""))
        except ValueError:
            raise DomainError("Choose a semester.", code="semester_required") from None
        entry = next((s for s in record["semesters"] if s["semester_id"] == semester_id), None)
        if entry is None:
            raise DomainError("No published results for that semester.", code="not_found", status_code=404)
        return pdf_response(pdf.result_slip(record, entry), f"result-slip-{record['student']['student_number']}.pdf")

    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], url_path="transcript-pdf")
    def transcript_pdf(self, request):
        record = services.build_academic_record(self._student(request), published_only=True)
        content = pdf.transcript(record, title="Statement of results")
        return pdf_response(content, f"statement-{record['student']['student_number']}.pdf")


class ResitSheetViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Resit examinations: open, register candidates, enter scores, approve and publish."""

    permission_classes = [RoleBasedPermission]
    read_roles = RESULTS_STAFF
    write_roles = RESULTS_STAFF  # fine-grained checks are in apps.results.resits
    serializer_class = ResitSheetListSerializer
    filterset_fields = ["status"]
    search_fields = ["offering__course__code", "offering__course__title"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = ResitSheet.objects.select_related("offering__course", "offering__semester__academic_year").annotate(
            eligible_count=Count("entries", distinct=True),
            registered_count=Count("entries", filter=Q(entries__status__in=["REGISTERED", "COMPLETED"]), distinct=True),
        ).order_by("-created_at")
        return resits.visible_resit_sheets(self.request.user, queryset)

    def _detail(self, sheet):
        user = self.request.user
        sheet = self.get_queryset().get(pk=sheet.pk)
        assigned = services.is_assigned_lecturer(user, sheet.offering)
        regulation = AcademicRegulation.load()
        entries = sheet.entries.select_related("course_result__student", "course_result__sheet__grading_scheme",
                                                "course_result__offering")
        rows = []
        for entry in entries:
            outcome = resits.preview(entry) if entry.score is not None else None
            rows.append({
                "id": entry.id,
                "student_id": entry.course_result.student_id,
                "student_number": entry.course_result.student.student_number,
                "student_name": entry.course_result.student.full_name,
                "status": entry.status,
                "status_display": entry.get_status_display(),
                "original_total": str(entry.original_total),
                "original_grade": entry.original_grade,
                "score": str(entry.score) if entry.score is not None else None,
                "projected_total": str(outcome[0]) if outcome else None,
                "projected_grade": outcome[1].letter if outcome else None,
                "projected_capped": outcome[2] if outcome else False,
                "new_total": str(entry.new_total) if entry.new_total is not None else None,
                "new_grade": entry.new_grade or None,
            })
        exam_components = [c.name for c in sheet.offering.assessment_components.all() if c.is_examination]
        profile = InstitutionProfile.load()
        registration_open = sheet.is_editable and (
            sheet.registration_deadline is None or sheet.registration_deadline >= timezone.now())
        return {
            **ResitSheetListSerializer(sheet).data,
            "rows": rows,
            "exam_components": exam_components,
            "grade_cap": str(regulation.resit_max_grade_point) if regulation.resit_max_grade_point is not None else None,
            "registration_open": registration_open,
            "signoff_warnings": signoff.gaps(sheet, profile=profile),
            "permissions": {
                "can_register": user.role in resits.RESULTS_ROLES and sheet.is_editable,
                "can_enter_scores": sheet.is_editable and assigned,
                "can_submit": sheet.is_editable and assigned,
                "can_return": user.can_approve_results and sheet.status in (SheetStatus.SUBMITTED, SheetStatus.APPROVED),
                "can_approve": user.can_approve_results and sheet.status == SheetStatus.SUBMITTED and not (
                    profile.approver_must_differ_from_submitter and sheet.submitted_by_id == user.pk),
                "can_publish": user.can_publish_results and sheet.status == SheetStatus.APPROVED and not (
                    profile.publisher_must_differ_from_submitter and sheet.submitted_by_id == user.pk),
            },
        }

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        return Response(self._detail(self.get_object()))

    @extend_schema(request=OpenResitSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path="open")
    def open_resit(self, request):
        payload = OpenResitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        offering = CourseOffering.objects.filter(pk=payload.validated_data["offering"]).first()
        if offering is None:
            raise DomainError("Course offering not found.", code="not_found", status_code=404)
        sheet = resits.open_resit(offering, actor=request.user,
                                  registration_deadline=payload.validated_data.get("registration_deadline"))
        return Response(self._detail(sheet), status=status.HTTP_201_CREATED)

    @extend_schema(request=ResitRegisterSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def register(self, request, pk=None):
        sheet = self.get_object()
        payload = ResitRegisterSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        resits.register_entries(sheet, payload.validated_data["entries"], actor=request.user,
                                reason=payload.validated_data.get("reason", ""))
        return Response(self._detail(sheet))

    @extend_schema(request=ResitScoresInputSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["put"])
    def scores(self, request, pk=None):
        sheet = self.get_object()
        payload = ResitScoresInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        changed = resits.save_scores(sheet, payload.validated_data["scores"], actor=request.user)
        return Response({"changed": changed, **self._detail(sheet)})

    def _transition(self, request, fn):
        sheet = self.get_object()
        payload = TransitionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        fn(sheet, actor=request.user, comment=payload.validated_data.get("comment", ""))
        return Response(self._detail(sheet))

    @extend_schema(request=TransitionSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return self._transition(request, resits.submit)

    @extend_schema(request=TransitionSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="return")
    def return_for_correction(self, request, pk=None):
        return self._transition(request, resits.return_for_correction)

    @extend_schema(request=TransitionSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._transition(request, resits.approve)

    @extend_schema(request=TransitionSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return self._transition(request, resits.publish)


class MyResitsViewSet(viewsets.ViewSet):
    """The signed-in student's resit opportunities and registrations."""

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.STUDENT,)
    write_roles = (Role.STUDENT,)

    def _student(self, request):
        student = Student.objects.filter(user=request.user).first()
        if student is None:
            raise DomainError("No student record is linked to this account.", code="not_found", status_code=404)
        # Resit entries show the original and resit grades, so the same hold as My results applies.
        if not StatusPolicy.for_status(student.status).can_view_results:
            raise DomainError("Result access is not available for your current academic status. "
                              "Please contact the registry.", code="results_blocked", status_code=403)
        return student

    @extend_schema(responses=MyResitSerializer(many=True))
    def list(self, request):
        return Response(MyResitSerializer(resits.student_entries(self._student(request)), many=True).data)

    @extend_schema(request=None, responses=MyResitSerializer,
                   parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"])
    def register(self, request, pk=None):
        student = self._student(request)
        entry = resits.student_entries(student).filter(pk=pk).first()
        if entry is None:
            raise DomainError("Resit not found.", code="not_found", status_code=404)
        resits.register_entries(entry.sheet, [entry.id], actor=request.user)
        entry.refresh_from_db()
        return Response(MyResitSerializer(entry).data)
