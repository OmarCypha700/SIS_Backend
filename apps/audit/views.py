from django.db.models import Q
from django_filters import rest_framework as django_filters
from rest_framework import serializers, viewsets

from apps.accounts.models import Role
from common.permissions import RoleBasedPermission

from .models import AuditLog

# Entity types each non-admin role may review in the audit trail.
ROLE_ENTITY_PREFIXES = {
    Role.REGISTRAR: ("students.", "registration.", "academics.", "results."),
    Role.RESULTS_OFFICER: ("results.",),
}


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "created_at", "actor", "actor_name", "actor_email", "action", "entity_type", "entity_id",
                  "summary", "before", "after", "reason", "metadata", "ip_address", "request_id"]
        read_only_fields = fields


class AuditLogFilter(django_filters.FilterSet):
    since = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    until = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")
    action = django_filters.CharFilter(field_name="action", lookup_expr="startswith")

    class Meta:
        model = AuditLog
        fields = ["entity_type", "entity_id", "actor"]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [RoleBasedPermission]
    read_roles = (Role.ADMIN, Role.REGISTRAR, Role.RESULTS_OFFICER)
    filterset_class = AuditLogFilter
    search_fields = ["summary", "actor_email", "entity_id", "action"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        queryset = AuditLog.objects.select_related("actor")
        prefixes = ROLE_ENTITY_PREFIXES.get(getattr(self.request.user, "role", None))
        if prefixes is not None:
            condition = Q()
            for prefix in prefixes:
                condition |= Q(entity_type__startswith=prefix)
            queryset = queryset.filter(condition)
        return queryset
