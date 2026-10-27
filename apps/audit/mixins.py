from .services import diff, log_action, snapshot


class AuditedModelMixin:
    """Records create/update/delete of a viewset's model in the audit log."""

    audit_name = None  # e.g. "course"; defaults to the model name

    def _audit_name(self, instance):
        return self.audit_name or instance._meta.model_name

    def perform_create(self, serializer):
        instance = serializer.save()
        log_action(f"{self._audit_name(instance)}.created", actor=self.request.user, instance=instance,
                   summary=f"Created {instance}", after=snapshot(instance))

    def perform_update(self, serializer):
        before = snapshot(serializer.instance)
        instance = serializer.save()
        before, after = diff(before, snapshot(instance))
        if after:
            log_action(f"{self._audit_name(instance)}.updated", actor=self.request.user, instance=instance,
                       summary=f"Updated {instance}", before=before, after=after)

    def perform_destroy(self, instance):
        before = snapshot(instance)
        label, pk, name = str(instance), instance.pk, self._audit_name(instance)
        entity_type = instance._meta.label_lower
        instance.delete()
        log_action(f"{name}.deleted", actor=self.request.user, entity_type=entity_type, entity_id=pk,
                   summary=f"Deleted {label}", before=before)
