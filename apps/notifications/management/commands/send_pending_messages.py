import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications import delivery, services
from apps.notifications.models import MessageStatus, Notification, OutgoingMessage, PushSubscription


class Command(BaseCommand):
    help = ("Send queued email and push notifications, merging each person's backlog of alert emails into one "
            "digest. Run once a day as a scheduled task (more often if your host allows).")

    def add_arguments(self, parser):
        parser.add_argument("--no-prune", action="store_true", help="Keep old notifications and outbox rows.")

    def handle(self, *args, **options):
        now = timezone.now()
        recovered = delivery.recover_stale(now)
        expired = delivery.expire_alerts(now) + delivery.expire_resets(now)
        digests = services.build_digests()
        stats = delivery.drain()
        self.stdout.write(
            f"Sent {stats['sent']}, failed {stats['failed']}, deferred (over today's email allowance) "
            f"{stats['deferred']}, skipped {stats['skipped']}. Digests built: {digests}. "
            f"Expired alerts: {expired}. Recovered: {recovered}."
        )
        if not options["no_prune"]:
            read_cutoff = now - datetime.timedelta(days=180)
            removed = Notification.objects.filter(read_at__lt=read_cutoff).delete()[0]
            removed_out = OutgoingMessage.objects.filter(
                status__in=[MessageStatus.SENT, MessageStatus.SKIPPED, MessageStatus.FAILED],
                updated_at__lt=now - datetime.timedelta(days=90)).delete()[0]
            dead = PushSubscription.objects.filter(failures__gte=10).delete()[0]
            self.stdout.write(f"Pruned {removed} old notifications, {removed_out} outbox rows, "
                              f"{dead} failing devices.")
