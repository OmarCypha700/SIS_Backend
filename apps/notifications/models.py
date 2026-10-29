from django.conf import settings
from django.db import models


class Kind(models.TextChoices):
    RESULTS_SUBMITTED = "RESULTS_SUBMITTED", "Results submitted"
    RESULTS_RETURNED = "RESULTS_RETURNED", "Results returned"
    RESULTS_APPROVED = "RESULTS_APPROVED", "Results approved"
    RESULTS_PUBLISHED = "RESULTS_PUBLISHED", "Results published"
    AMENDMENT_REQUESTED = "AMENDMENT_REQUESTED", "Amendment requested"
    AMENDMENT_REVIEWED = "AMENDMENT_REVIEWED", "Amendment reviewed"
    RESIT_OPENED = "RESIT_OPENED", "Resit opened"
    RESIT_UPDATED = "RESIT_UPDATED", "Resit updated"
    REGISTRATION_PENDING = "REGISTRATION_PENDING", "Registration awaiting approval"
    REGISTRATION_REVIEWED = "REGISTRATION_REVIEWED", "Registration reviewed"
    TEACHING_ASSIGNED = "TEACHING_ASSIGNED", "Teaching assignment"
    PROGRESSION_APPLIED = "PROGRESSION_APPLIED", "Progression decision"
    STATUS_CHANGED = "STATUS_CHANGED", "Status changed"
    SIGNOFF_BLOCKED = "SIGNOFF_BLOCKED", "Sign-off needs attention"
    TEST = "TEST", "Test notification"


class Notification(models.Model):
    """An in-app notification for one person. Push and email copies are sent from the outbox."""

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=40, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500, blank=True)
    link = models.CharField(max_length=300, blank=True, help_text="Frontend path, e.g. /results/sheets/12")
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "read_at"], name="notification_unread_idx")]

    def __str__(self):
        return f"{self.title} -> {self.recipient_id}"


class PushSubscription(models.Model):
    """A browser or installed app that receives web push for a user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"Push subscription {self.pk} for {self.user_id}"


class Channel(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    PUSH = "PUSH", "Push"


class Category(models.TextChoices):
    # Security mail (password reset, account setup) may use the whole daily email allowance.
    SECURITY = "SECURITY", "Security"
    ALERT = "ALERT", "Alert"


class MessageStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"


class OutgoingMessage(models.Model):
    """
    Outbox for email and push. Also the ledger for the daily email allowance: the number of
    emails sent today is counted from these rows, so it is correct across web workers.
    """

    channel = models.CharField(max_length=10, choices=Channel.choices)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.ALERT)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                  related_name="outgoing_messages")
    to = models.EmailField(blank=True)
    subscription = models.ForeignKey(PushSubscription, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="messages")
    notification = models.ForeignKey(Notification, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="messages")
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=MessageStatus.choices, default=MessageStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["status", "channel", "created_at"], name="outbox_queue_idx")]

    def __str__(self):
        return f"{self.channel} {self.status} {self.pk}"
