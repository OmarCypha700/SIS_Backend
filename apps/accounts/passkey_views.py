from django.contrib.auth import login
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import passkeys
from .models import User
from .serializers import CurrentUserSerializer


class PasskeyCredentialSerializer(serializers.Serializer):
    credential = serializers.JSONField(help_text="The PublicKeyCredential from the browser, as JSON.")
    name = serializers.CharField(max_length=80, required=False, allow_blank=True)


class PasskeyListView(APIView):
    """My passkeys (one per device I set up)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response([{"id": p.id, "name": p.name, "created_at": p.created_at, "last_used_at": p.last_used_at}
                         for p in request.user.passkeys.all()])


class PasskeyDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        passkeys.remove(request.user, pk, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasskeyRegisterOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        return Response(passkeys.registration_options(request))


class PasskeyRegisterView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=PasskeyCredentialSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        payload = PasskeyCredentialSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        passkey = passkeys.register(request, payload.validated_data["credential"], payload.validated_data.get("name", ""))
        return Response({"id": passkey.id, "name": passkey.name, "created_at": passkey.created_at,
                         "last_used_at": None}, status=status.HTTP_201_CREATED)


@method_decorator(csrf_protect, name="dispatch")
class PasskeySignInOptionsView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        return Response(passkeys.sign_in_options(request))


@method_decorator(csrf_protect, name="dispatch")
class PasskeySignInView(APIView):
    """Sign in with a passkey: no email or password, the device's fingerprint, face or PIN instead."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=PasskeyCredentialSerializer, responses=CurrentUserSerializer)
    def post(self, request):
        payload = PasskeyCredentialSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = passkeys.authenticate(request, payload.validated_data["credential"])
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        user = User.objects.select_related("student_profile__programme", "student_profile__current_level").get(pk=user.pk)
        return Response(CurrentUserSerializer(user).data)
