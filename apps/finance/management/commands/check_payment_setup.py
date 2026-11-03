from django.core.management.base import BaseCommand, CommandError

from apps.finance import paystack


class Command(BaseCommand):
    help = "Check that online fee payment is set up: the key is present and the payment provider accepts it."

    def handle(self, *args, **options):
        ok, message = paystack.check_setup()
        if not ok:
            raise CommandError(message)
        self.stdout.write(self.style.SUCCESS(message))
