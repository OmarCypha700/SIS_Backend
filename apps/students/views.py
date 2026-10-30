from django.conf import settings
from django.http import FileResponse, Http404
from django_filters import rest_framework as django_filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.accounts.models import Role, User
from apps.accounts.serializers import validate_new_password
from apps.audit.services import log_action
from apps.results.services import build_academic_record
from common import pdf
from common.pdf import pdf_response
from common.tabular import read_table, requested_format, table_response
from common.bulk import run_bulk
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission

from . import photos, services
from .models import Student
from .serializers import (
    IMPORT_COLUMNS, PlacementChangeSerializer, ReasonSerializer, StatusChangeSerializer, StudentHistorySerializer,
    StudentBulkSerializer, StudentSelfSerializer, StudentSerializer, StudentSummarySerializer,
)

REGISTRY = (Role.ADMIN, Role.REGISTRAR)


class StudentFilter(django_filters.FilterSet):
    programme = django_filters.NumberFilter(field_name="programme")
    level = django_filters.NumberFilter(field_name="current_level")
    department = django_filters.NumberFilter(field_name="programme__department")
    entry_academic_year = django_filters.NumberFilter(field_name="entry_academic_year")

    class Meta:
        model = Student
        fields = ["status", "admission_year", "is_archived", "gender"]


class StudentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER, Role.LECTURER)
    write_roles = REGISTRY
    action_roles = {
        "me": (Role.STUDENT,),
        "export": REGISTRY,
        "history": REGISTRY,
        "academic_record": (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER),
        "transcript_pdf": (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER),
        # Reading follows the queryset scope (students: own; lecturers: their classes); changes are registry-only.
        "photo": (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER, Role.LECTURER, Role.STUDENT),
        "photos_match": REGISTRY,
        "photos_upload": REGISTRY,
        "deletion_check": (Role.ADMIN,),
        "delete_record": (Role.ADMIN,),
        "bulk": REGISTRY,
    }
    filterset_class = StudentFilter
    search_fields = ["student_number", "first_name", "last_name", "middle_name", "email"]
    ordering_fields = ["student_number", "last_name", "first_name", "admission_year", "created_at", "status"]
    ordering = ["last_name", "first_name"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = Student.objects.select_related("programme", "current_level", "user")
        user = self.request.user
        if getattr(user, "role", None) == Role.LECTURER:
            # Only students registered on one of the lecturer's offerings.
            queryset = queryset.filter(
                course_registrations__offering__assignments__lecturer=user,
                course_registrations__status="ACTIVE",
            ).distinct()
        elif getattr(user, "role", None) == Role.STUDENT:
            queryset = queryset.filter(user=user)
        return queryset

    def get_serializer_class(self):
        if self.action == "me":
            return StudentSelfSerializer
        if getattr(self.request.user, "role", None) in (Role.LECTURER, Role.RESULTS_OFFICER):
            return StudentSummarySerializer
        return StudentSerializer

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        password = data.pop("password", None)
        if password:
            validate_new_password(password, User(email=data["email"], first_name=data["first_name"],
                                                 last_name=data["last_name"]), field="password")
        serializer.instance = services.create_student(data, actor=self.request.user, password=password)

    def perform_update(self, serializer):
        serializer.instance = services.update_student(
            serializer.instance, serializer.validated_data, actor=self.request.user
        )

    @extend_schema(request=StatusChangeSerializer, responses=StudentSerializer)
    @action(detail=True, methods=["post"], url_path="change-status")
    def change_status(self, request, pk=None):
        student = self.get_object()
        payload = StatusChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.change_status(student, actor=request.user, **payload.validated_data)
        return Response(StudentSerializer(student, context=self.get_serializer_context()).data)

    @extend_schema(request=PlacementChangeSerializer, responses=StudentSerializer)
    @action(detail=True, methods=["post"], url_path="change-placement")
    def change_placement(self, request, pk=None):
        student = self.get_object()
        payload = PlacementChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        services.change_placement(student, programme=data["programme"], level=data["current_level"],
                                  reason=data["reason"], actor=request.user)
        return Response(StudentSerializer(student, context=self.get_serializer_context()).data)

    def _set_active(self, request, active):
        student = self.get_object()
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.set_active(student, active=active, reason=payload.validated_data["reason"], actor=request.user)
        return Response(StudentSerializer(student, context=self.get_serializer_context()).data)

    @extend_schema(request=ReasonSerializer, responses=StudentSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._set_active(request, False)

    @extend_schema(request=ReasonSerializer, responses=StudentSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._set_active(request, True)

    @extend_schema(methods=["GET"], parameters=[OpenApiParameter("size", str, enum=["full", "thumb"])],
                   responses={(200, "image/webp"): OpenApiTypes.BINARY})
    @extend_schema(methods=["PUT"], request={"multipart/form-data": {
        "type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}}, responses=StudentSerializer)
    @extend_schema(methods=["DELETE"], responses={204: None})
    @action(detail=True, methods=["get", "put", "delete"], parser_classes=[MultiPartParser, FormParser])
    def photo(self, request, pk=None):
        student = self.get_object()
        if request.method == "GET":
            field = student.photo_thumbnail if request.query_params.get("size") == "thumb" else student.photo
            if not field:
                raise Http404
            try:
                handle = field.open("rb")
            except FileNotFoundError:
                raise Http404 from None
            response = FileResponse(handle, content_type="image/webp")
            # The address changes whenever the photo changes, so browsers may keep it for a week.
            response["Cache-Control"] = "private, max-age=604800"
            return response
        if request.user.role not in REGISTRY:
            raise DomainError("Only the registry can change student photos.", code="permission_denied", status_code=403)
        if request.method == "DELETE":
            photos.remove_photo(student, actor=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        upload = request.FILES.get("file")
        if upload is None:
            raise DomainError("Choose a photo to upload.", code="file_required")
        photos.set_photo(student, upload, actor=request.user)
        return Response(StudentSerializer(student, context=self.get_serializer_context()).data)

    @extend_schema(request={"application/json": {"type": "object", "properties": {
        "filenames": {"type": "array", "items": {"type": "string"}}}}}, responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="photos/match")
    def photos_match(self, request):
        """Match photo file names to students before uploading anything."""
        names = request.data.get("filenames")
        if not isinstance(names, list) or not names or len(names) > 2000:
            raise DomainError("Send between 1 and 2000 file names.", code="invalid")
        return Response({"files": photos.match([str(n)[:255] for n in names])})

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {
        "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
        "replace": {"type": "boolean"}}}}, responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="photos/upload", parser_classes=[MultiPartParser, FormParser])
    def photos_upload(self, request):
        """Upload one batch of photos named by student number. Each is matched again on the server."""
        files = request.FILES.getlist("files")
        if not files:
            raise DomainError("Choose at least one photo.", code="file_required")
        replace = str(request.data.get("replace", "false")).lower() == "true"
        return Response({"files": photos.bulk_upload(files, replace=replace, actor=request.user)})

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"], url_path="deletion-check")
    def deletion_check(self, request, pk=None):
        blockers = services.deletion_blockers(self.get_object())
        return Response({"can_delete": not blockers, "blockers": blockers})

    @extend_schema(request=ReasonSerializer, responses={204: None})
    @action(detail=True, methods=["post"], url_path="delete-record")
    def delete_record(self, request, pk=None):
        """Permanently remove a record created in error. Refused once it has any history."""
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.delete_in_error(self.get_object(), reason=payload.validated_data["reason"], actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=StudentBulkSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"])
    def bulk(self, request):
        """Apply one action to the selected students. Records that cannot be changed are skipped and listed."""
        payload = StudentBulkSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        action_name, reason, actor = data["action"], data["reason"], request.user
        if action_name == "delete" and actor.role != Role.ADMIN:
            raise DomainError("Only administrators can delete records.", code="permission_denied", status_code=403)
        operations = {
            "deactivate": lambda s: services.set_active(s, active=False, reason=reason, actor=actor),
            "activate": lambda s: services.set_active(s, active=True, reason=reason, actor=actor),
            "change_status": lambda s: services.change_status(s, status=data.get("status"), reason=reason, actor=actor),
            "delete": lambda s: services.delete_in_error(s, reason=reason, actor=actor),
        }
        outcome = run_bulk(self.get_queryset(), data["ids"], operations[action_name],
                           label=lambda s: f"{s.student_number} {s.full_name}")
        log_action(f"student.bulk_{action_name}", actor=actor, entity_type="students.student", reason=reason,
                   summary=f"Bulk {action_name.replace('_', ' ')}: {len(outcome['done'])} done, "
                           f"{len(outcome['skipped'])} skipped",
                   metadata={"done": [d["id"] for d in outcome["done"]], "status": data.get("status")})
        return Response(outcome)

    @extend_schema(responses=StudentHistorySerializer(many=True))
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        student = self.get_object()
        entries = student.history.select_related("from_programme", "to_programme", "from_level", "to_level", "changed_by")
        return Response(StudentHistorySerializer(entries, many=True).data)

    @action(detail=True, methods=["get"], url_path="academic-record")
    def academic_record(self, request, pk=None):
        student = self.get_object()
        return Response(build_academic_record(student, published_only=False))

    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY},
                   parameters=[OpenApiParameter("published_only", bool)])
    @action(detail=True, methods=["get"], url_path="transcript-pdf")
    def transcript_pdf(self, request, pk=None):
        student = self.get_object()
        published_only = request.query_params.get("published_only", "true").lower() != "false"
        record = build_academic_record(student, published_only=published_only)
        content = pdf.transcript(record, include_unpublished=not published_only)
        log_action("student.transcript_generated", actor=request.user, instance=student,
                   summary=f"Generated transcript PDF for {student.student_number}",
                   metadata={"published_only": published_only})
        return pdf_response(content, f"transcript-{student.student_number}.pdf")

    @extend_schema(methods=["GET"], responses=StudentSelfSerializer)
    @extend_schema(methods=["PATCH"], request=StudentSelfSerializer, responses=StudentSelfSerializer)
    @action(detail=False, methods=["get", "patch"])
    def me(self, request):
        student = Student.objects.select_related("programme", "current_level").filter(user=request.user).first()
        if student is None:
            raise DomainError("No student record is linked to this account.", status_code=404, code="not_found")
        if request.method == "PATCH":
            serializer = StudentSelfSerializer(student, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            services.update_student(student, serializer.validated_data, actor=request.user)
        return Response(StudentSelfSerializer(student, context=self.get_serializer_context()).data)

    @extend_schema(parameters=[OpenApiParameter("search", str), OpenApiParameter("status", str)], responses={200: None})
    @action(detail=False, methods=["get"])
    def export(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        rows = (
            [s.student_number, s.first_name, s.middle_name, s.last_name, s.email, s.phone, s.gender,
             s.date_of_birth, s.programme.code, s.current_level.name, s.admission_year, s.admission_date, s.status]
            for s in queryset.iterator(chunk_size=500)
        )
        log_action("student.exported", actor=request.user, entity_type="students.student",
                   summary="Exported student records", metadata={"filters": dict(request.query_params)})
        return table_response("students", IMPORT_COLUMNS, rows, requested_format(request), sheet_title="Students")

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}})
    @action(detail=False, methods=["post"], url_path="import/preview")
    def import_preview(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise serializers.ValidationError({"file": "Upload a CSV file."})
        try:
            rows = read_table(upload, max_bytes=settings.MAX_UPLOAD_CSV_BYTES, max_rows=2000)
        except ValueError as exc:
            raise serializers.ValidationError({"file": str(exc)}) from exc
        missing = {"first_name", "last_name", "email", "programme_code", "level", "admission_year"} - set(rows[0].keys() if rows else [])
        if not rows:
            raise serializers.ValidationError({"file": "The file contains no data rows."})
        if missing:
            raise serializers.ValidationError({"file": f"Missing required columns: {', '.join(sorted(missing))}."})
        if len(rows) > 2000:
            raise serializers.ValidationError({"file": "Import at most 2000 rows at a time."})
        results = services.validate_import_rows(rows)
        return Response({
            "total": len(results),
            "valid": sum(1 for r in results if r["valid"]),
            "invalid": sum(1 for r in results if not r["valid"]),
            "rows": [{k: v for k, v in r.items() if k != "data"} for r in results],
        })

    @extend_schema(request={"application/json": {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object"}}}}})
    @action(detail=False, methods=["post"], url_path="import/confirm")
    def import_confirm(self, request):
        rows = request.data.get("rows")
        if not isinstance(rows, list) or not rows:
            raise serializers.ValidationError({"rows": "Provide the rows returned by the preview."})
        if len(rows) > 2000:
            raise serializers.ValidationError({"rows": "Import at most 2000 rows at a time."})
        rows = [{str(k): ("" if v is None else str(v)) for k, v in row.items()} for row in rows if isinstance(row, dict)]
        mode = str(request.data.get("password_mode") or "none")
        created, credentials = services.import_students(rows, actor=request.user, password_mode=mode)
        # Credentials are returned once for printing and never stored.
        return Response({"created": len(created), "credentials": credentials}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="import/template")
    def import_template(self, request):
        return table_response("student-import-template", IMPORT_COLUMNS, [
            ["", "Ama", "", "Mensah", "ama.mensah@example.com", "0240000000", "F", "2004-05-14", "PROG-CODE",
             "Level 100", "2025", "2025-09-01", "ACTIVE", ""],
        ], requested_format(request), sheet_title="Students")
