from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Everything that runs once a day, as a single scheduled task (PythonAnywhere's free plan allows one): "
            "check pending online payments, then send queued email and push notifications.")

    def handle(self, *args, **options):
        for name in ("reconcile_payments", "send_pending_messages"):
            try:
                call_command(name, stdout=self.stdout, stderr=self.stderr)
            except Exception as exc:  # noqa: BLE001 - one failing job must not stop the others
                self.stderr.write(f"{name} failed: {exc}")
