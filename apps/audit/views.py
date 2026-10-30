from django.db.models import Q
from django_filters import rest_framework as django_filters
from rest_framework import serializers, viewsets

from apps.accounts.models import Office, Role
from common.permissions import RoleBasedPermission, has_role

from .models import AuditLog

CAPACITY_LABELS = {**dict(Role.choices), **{f"ACTING_{o}": f"Acting {label}" for o, label in Office.choices}}


def capacity_display(codes):
    return ", ".join(CAPACITY_LABELS.get(code, code) for code in (codes or "").split())


# Entity types each non-admin role may review in the audit trail.
ROLE_ENTITY_PREFIXES = {
    Role.REGISTRAR: ("students.", "registration.", "academics.", "results."),
    Role.RESULTS_OFFICER: ("results.",),
}


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True, default=None)
    actor_capacity = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "created_at", "actor", "actor_name", "actor_email", "actor_roles", "actor_capacity",
                  "action", "entity_type", "entity_id",
                  "summary", "before", "after", "reason", "metadata", "ip_address", "request_id"]
        read_only_fields = fields

    def get_actor_capacity(self, log) -> str:
        return capacity_display(log.actor_roles)


class AuditLogFilter(django_filters.FilterSet):
    since = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    until = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")
    action = django_filters.CharFilter(field_name="action", lookup_expr="startswith")
    # "Everything done as Registrar" (acting included), whoever held the office at the time.
    actor_role = django_filters.ChoiceFilter(choices=Role.choices, method="filter_actor_role")

    def filter_actor_role(self, queryset, name, value):
        return queryset.filter(actor_roles__icontains=value)

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
        user = self.request.user
        if has_role(user, Role.ADMIN):
            return queryset
        # Office holders see what each of their offices covers.
        prefixes = {p for role in getattr(user, "roles", ()) for p in ROLE_ENTITY_PREFIXES.get(role, ())}
        if prefixes:
            condition = Q()
            for prefix in prefixes:
                condition |= Q(entity_type__startswith=prefix)
            queryset = queryset.filter(condition)
        return queryset
