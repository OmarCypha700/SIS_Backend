from drf_spectacular.utils import extend_schema
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.audit.services import diff, log_action, snapshot
from common.permissions import RoleBasedPermission

from .models import AcademicRegulation, InstitutionProfile
from .regulation_presets import preset_list
from .serializers import AcademicRegulationSerializer, InstitutionBrandingSerializer, InstitutionProfileSerializer


class InstitutionBrandingView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=InstitutionBrandingSerializer, summary="Public institution branding")
    def get(self, request):
        return Response(InstitutionBrandingSerializer(InstitutionProfile.load()).data)


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
