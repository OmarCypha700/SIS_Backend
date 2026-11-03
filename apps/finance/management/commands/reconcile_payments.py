from django.core.management.base import BaseCommand

from apps.finance import paystack


class Command(BaseCommand):
    help = "Check online payments left pending with Paystack, and mark ones abandoned for a day."

    def handle(self, *args, **options):
        stats = paystack.reconcile()
        self.stdout.write(f"Payments checked {stats['checked']}, confirmed {stats['succeeded']}, "
                          f"marked not completed {stats['abandoned']}.")
