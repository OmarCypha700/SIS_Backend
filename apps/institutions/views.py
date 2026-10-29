import mimetypes
import os

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotModified
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.audit.services import diff, log_action, snapshot
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission

from . import app_icon
from .models import AcademicRegulation, InstitutionProfile
from .regulation_presets import preset_list
from .serializers import AcademicRegulationSerializer, InstitutionBrandingSerializer, InstitutionProfileSerializer


class InstitutionBrandingView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=InstitutionBrandingSerializer, summary="Public institution branding")
    def get(self, request):
        return Response(InstitutionBrandingSerializer(InstitutionProfile.load()).data)


class AppIconView(APIView):
    """Icons for the installable app, drawn from the logo or initials and the primary colour."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(parameters=[OpenApiParameter("size", int, enum=list(app_icon.SIZES)),
                               OpenApiParameter("purpose", str, enum=list(app_icon.PURPOSES))],
                   responses={(200, "image/png"): OpenApiTypes.BINARY})
    def get(self, request):
        try:
            size = int(request.query_params.get("size", 192))
        except ValueError:
            size = 0
        purpose = request.query_params.get("purpose", "any")
        if size not in app_icon.SIZES or purpose not in app_icon.PURPOSES:
            return HttpResponse(status=404)
        profile = InstitutionProfile.load()
        tag = f'"{app_icon.etag(profile, size, purpose)}"'
        if request.headers.get("If-None-Match") == tag:
            return HttpResponseNotModified()
        response = HttpResponse(app_icon.render(profile, size, purpose), content_type="image/png")
        response["ETag"] = tag
        response["Cache-Control"] = "public, max-age=86400"
        return response


class TranscriptImageView(APIView):
    """
    The signature and seal printed on official transcripts. Stored privately (never under /media/),
    so only administrators can see, replace or remove them here.
    """

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN,)
    write_roles = (Role.ADMIN,)
    parser_classes = [MultiPartParser, FormParser]
    FIELDS = {"signature": "transcript_signature", "seal": "transcript_seal"}

    def _field(self, kind):
        if kind not in self.FIELDS:
            raise Http404
        return self.FIELDS[kind]

    @extend_schema(responses={(200, "image/*"): OpenApiTypes.BINARY})
    def get(self, request, kind):
        image = getattr(InstitutionProfile.load(), self._field(kind))
        if not image:
            raise Http404
        try:
            handle = image.open("rb")
        except FileNotFoundError:
            raise Http404 from None
        content_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
        response = FileResponse(handle, content_type=content_type)
        response["Cache-Control"] = "private, no-store"
        return response

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {"file": {"type": "string",
                                                                                        "format": "binary"}}}},
                   responses={204: None})
    def put(self, request, kind):
        field = self._field(kind)
        upload = request.FILES.get("file")
        if upload is None:
            raise DomainError("Choose an image file.", code="file_required")
        profile = InstitutionProfile.load()
        for validator in profile._meta.get_field(field).validators:
            try:
                validator(upload)
            except DjangoValidationError as exc:
                raise DomainError(" ".join(exc.messages), code="invalid_file") from None
        old = getattr(profile, field)
        if old:
            old.delete(save=False)
        getattr(profile, field).save(f"{kind}{os.path.splitext(upload.name)[1].lower()}", upload, save=True)
        log_action(f"institution.transcript_{kind}_uploaded", actor=request.user, instance=profile,
                   summary=f"Uploaded the transcript {kind} image")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={204: None})
    def delete(self, request, kind):
        field = self._field(kind)
        profile = InstitutionProfile.load()
        image = getattr(profile, field)
        if image:
            image.delete(save=True)
            log_action(f"institution.transcript_{kind}_removed", actor=request.user, instance=profile,
                       summary=f"Removed the transcript {kind} image")
        return Response(status=status.HTTP_204_NO_CONTENT)


class InstitutionSettingsView(APIView):
    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)
    write_roles = (Role.ADMIN,)
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(responses=InstitutionProfileSerializer)
    def get(self, request):
        return Response(InstitutionProfileSerializer(InstitutionProfile.load()).data)

    @extend_schema(request=InstitutionProfileSerializer, responses=InstitutionProfileSerializer)
    def patch(self, request):
        profile = InstitutionProfile.load()
        before = snapshot(profile)
        serializer = InstitutionProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        changed_before, changed_after = diff(before, snapshot(profile))
        if changed_after:
            log_action("institution.updated", actor=request.user, instance=profile,
                       summary="Updated institution settings", before=changed_before, after=changed_after)
        return Response(serializer.data)


class AcademicRegulationPresetsView(APIView):
    """Ready-made settings an administrator can load into the form, review and save."""

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)

    @extend_schema(responses={200: None})
    def get(self, request):
        return Response(preset_list())


class AcademicRegulationView(APIView):
    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER, Role.LECTURER)
    write_roles = (Role.ADMIN,)

    @extend_schema(responses=AcademicRegulationSerializer)
    def get(self, request):
        return Response(AcademicRegulationSerializer(AcademicRegulation.load()).data)

    @extend_schema(request=AcademicRegulationSerializer, responses=AcademicRegulationSerializer)
    def patch(self, request):
        regulation = AcademicRegulation.load()
        before = snapshot(regulation)
        serializer = AcademicRegulationSerializer(regulation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        changed_before, changed_after = diff(before, snapshot(regulation))
        if changed_after:
            log_action("regulations.updated", actor=request.user, instance=regulation,
                       summary="Updated academic regulations", before=changed_before, after=changed_after)
        return Response(serializer.data)
