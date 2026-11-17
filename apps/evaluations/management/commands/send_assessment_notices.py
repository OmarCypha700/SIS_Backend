from django.core.management.base import BaseCommand

from apps.evaluations.services import send_reminders_and_feedback


class Command(BaseCommand):
    help = ("Tutor assessment: remind students 3 days before an assessment closes, and tell tutors when their "
            "feedback is ready (runs daily, as part of run_daily_tasks).")

    def handle(self, *args, **options):
        reminded, ready = send_reminders_and_feedback()
        self.stdout.write(f"Tutor assessment: {reminded} reminder(s), {ready} feedback notice(s).")
