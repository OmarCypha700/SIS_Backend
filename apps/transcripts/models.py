from django.conf import settings
from django.db import models


class TranscriptStatus(models.TextChoices):
    VALID = "VALID", "Valid"
    REVOKED = "REVOKED", "Revoked"


class SigningMode(models.TextChoices):
    HAND = "HAND", "Printed for hand signing and stamping"
    DIGITAL = "DIGITAL", "Uploaded signature and seal"


class IssuedTranscript(models.Model):
    """
    The issuance register. Each row is one official transcript: its serial number, the exact
    record printed on it (`snapshot`), and a verification code printed as a QR code. Reprints
    render the same snapshot, so a copy printed later is identical to the original.
    """

    serial = models.CharField(max_length=60, unique=True)
    year = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    verification_code = models.CharField(max_length=16, unique=True)
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="transcripts")
    purpose = models.CharField(max_length=255, help_text="Who it is for or why it was requested")
    signing = models.CharField(max_length=10, choices=SigningMode.choices, default=SigningMode.HAND)
    is_interim = models.BooleanField(default=False)
    interim_reason = models.CharField(max_length=255, blank=True)
    snapshot = models.JSONField(help_text="The academic record exactly as printed")
    content_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=TranscriptStatus.choices, default=TranscriptStatus.VALID,
                              db_index=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    issued_at = models.DateTimeField(auto_now_add=True, db_index=True)
    print_count = models.PositiveIntegerField(default=0)
    last_printed_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["year", "sequence"], name="unique_transcript_sequence")]

    def __str__(self):
        return self.serial

    @property
    def display_code(self):
        return f"{self.verification_code[:5]}-{self.verification_code[5:]}"
