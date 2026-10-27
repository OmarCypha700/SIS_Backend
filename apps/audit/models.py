from django.conf import settings
from django.db import models


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit records cannot be modified.")

    def delete(self):
        raise PermissionError("Audit records cannot be deleted.")


class AuditLog(models.Model):
    """
    Append-only record of sensitive actions.

    The application refuses to update or delete rows through the ORM. That does not stop
    someone with direct database access; see docs/security.md for operational controls.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    entity_type = models.CharField(max_length=64, blank=True, db_index=True)
    entity_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=255, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}:{self.entity_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit records cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit records cannot be deleted.")
