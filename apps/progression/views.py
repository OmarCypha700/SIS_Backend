from django.db.models import Count
from django_filters import rest_framework as django_filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.academics.models import Semester
from apps.accounts.models import Role
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission

from . import services
from .models import ProgressionDecision, ProgressionReview
from .serializers import (
    ExcludeSerializer, GenerateReviewSerializer, OverrideSerializer, ProgressionDecisionSerializer,
    ProgressionReviewSerializer,
)

READERS = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)
REGISTRY = (Role.ADMIN, Role.REGISTRAR)


class ProgressionReviewViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [RoleBasedPermission]
    read_roles = READERS
    write_roles = REGISTRY
    serializer_class = ProgressionReviewSerializer
    filterset_fields = ["status", "semester"]

    def get_queryset(self):
        return (ProgressionReview.objects.select_related("semester__academic_year", "generated_by", "applied_by")
                .annotate(decision_count=Count("decisions")).order_by("-semester__start_date"))

    def _detail(self, review):
        review = self.get_queryset().get(pk=review.pk)
        return {**ProgressionReviewSerializer(review).data, "summary": services.summary(review)}

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        return Response(self._detail(self.get_object()))

    @extend_schema(request=GenerateReviewSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"])
    def generate(self, request):
        payload = GenerateReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        semester = Semester.objects.filter(pk=payload.validated_data["semester"]).first()
        if semester is None:
            raise DomainError("Semester not found.", code="not_found", status_code=404)
        review = services.generate_review(semester, actor=request.user)
        return Response(self._detail(review), status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        review = self.get_object()
        counts = services.apply_review(review, actor=request.user)
        return Response({**self._detail(review), "applied_counts": counts})


class DecisionFilter(django_filters.FilterSet):
    class Meta:
        model = ProgressionDecision
        fields = ["review", "final_standing", "final_placement", "excluded", "results_complete", "programme", "level"]


class ProgressionDecisionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [RoleBasedPermission]
    read_roles = READERS
    write_roles = REGISTRY
    serializer_class = ProgressionDecisionSerializer
    filterset_class = DecisionFilter
    search_fields = ["student__student_number", "student__first_name", "student__last_name"]
    ordering_fields = ["cgpa", "student__last_name", "outstanding_failed_credits"]

    def get_queryset(self):
        return (ProgressionDecision.objects.select_related("student", "programme", "level", "overridden_by", "review")
                .order_by("student__last_name", "student__first_name"))

    @extend_schema(request=OverrideSerializer, responses=ProgressionDecisionSerializer)
    @action(detail=True, methods=["post"])
    def override(self, request, pk=None):
        decision = self.get_object()
        payload = OverrideSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.override_decision(decision, actor=request.user, **payload.validated_data)
        return Response(self.get_serializer(self.get_queryset().get(pk=decision.pk)).data)

    @extend_schema(request=ExcludeSerializer, responses=ProgressionDecisionSerializer)
    @action(detail=True, methods=["post"])
    def exclude(self, request, pk=None):
        decision = self.get_object()
        payload = ExcludeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.set_excluded(decision, excluded=payload.validated_data["excluded"],
                              reason=payload.validated_data.get("reason", ""), actor=request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=decision.pk)).data)
