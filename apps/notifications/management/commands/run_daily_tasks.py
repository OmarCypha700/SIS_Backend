from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Everything that runs once a day, as a single scheduled task (PythonAnywhere's free plan allows one): "
            "check pending online payments, send queued email and push notifications, then remove expired "
            "sign-in sessions.")

    def handle(self, *args, **options):
        for name in ("reconcile_payments", "send_pending_messages", "clear_old_sessions"):
            try:
                call_command(name, stdout=self.stdout, stderr=self.stderr)
            except Exception as exc:  # noqa: BLE001 - one failing job must not stop the others
                self.stderr.write(f"{name} failed: {exc}")
