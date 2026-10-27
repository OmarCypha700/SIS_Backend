from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "apps.audit"
    label = "audit"

    def ready(self):
        from . import signals  # noqa: F401
