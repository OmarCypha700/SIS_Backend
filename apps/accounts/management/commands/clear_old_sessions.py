from django.core.management.base import BaseCommand

from apps.accounts import devices


class Command(BaseCommand):
    help = "Remove expired sign-in sessions and the signed-in device rows that describe them (runs daily)."

    def handle(self, *args, **options):
        removed = devices.clear_expired()
        self.stdout.write(f"Expired sessions removed ({removed} device record{'' if removed == 1 else 's'}).")
