"""
Official transcripts: issuing, reprinting, revoking and public verification.

* Only active administrators or registrars with `can_issue_transcripts` may issue, reprint or revoke.
* Only published results appear. A transcript is marked interim while the programme is not
  completed or results are still pending.
* The printed record is frozen in the register (`snapshot`), so every reprint is identical and a
  verifier sees exactly what was issued, even if results are amended later.
"""
import hashlib
import json
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import TRANSCRIPT_ISSUER_ROLES
from apps.audit.services import log_action
from apps.institutions.models import InstitutionProfile
from apps.institutions.services import render_format
from apps.results.models import CourseResult
from apps.results.services import build_academic_record
from apps.students.models import StudentStatus
from common.exceptions import DomainError

from .models import IssuedTranscript, SigningMode, TranscriptStatus

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I confusion
FINAL_STATUSES = (StudentStatus.COMPLETED, StudentStatus.GRADUATED)


def can_issue(user):
    return bool(user and user.is_active and user.role in TRANSCRIPT_ISSUER_ROLES and user.can_issue_transcripts)


def require_issuer(user):
    if not can_issue(user):
        raise DomainError("You are not authorised to issue official transcripts. An administrator can grant this "
                          "permission under Staff accounts.", code="permission_denied", status_code=403)


def verification_url(code):
    return f"{settings.FRONTEND_URL.rstrip('/')}/verify/{code}"


def interim_reason(student):
    pending = CourseResult.objects.filter(student=student, is_published=False).exists()
    if student.status not in FINAL_STATUSES:
        return "Programme in progress" + (" and some results are pending publication" if pending else "")
    if pending:
        return "Some results are pending publication"
    return ""


def preview(student):
    """What an issue would contain, for the confirmation dialog."""
    record = build_academic_record(student, published_only=True)
    reason = interim_reason(student)
    unpublished = CourseResult.objects.filter(student=student, is_published=False).count()
    profile = InstitutionProfile.load()
    return {
        "student": record["student"],
        "summary": record["summary"],
        "semesters": len(record["semesters"]),
        "is_interim": bool(reason),
        "interim_reason": reason,
        "unpublished_results": unpublished,
        "digital_signing_available": bool(profile.transcript_signature and profile.transcript_seal),
        "can_issue": bool(record["semesters"]),
        "previously_issued": IssuedTranscript.objects.filter(student=student).count(),
    }


def _hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


def _serial(profile, student, year, sequence):
    return render_format(profile.transcript_serial_format, short=profile.short_name, year=year, seq=sequence,
                         programme=student.programme.code)


def issue(student, *, purpose, signing, actor):
    require_issuer(actor)
    if not (purpose or "").strip():
        raise DomainError("State who the transcript is for or why it was requested.", code="purpose_required")
    profile = InstitutionProfile.load()
    if signing == SigningMode.DIGITAL and not (profile.transcript_signature and profile.transcript_seal):
        raise DomainError("Upload the signature and seal under Institution settings first, or print for hand signing.",
                          code="no_signature")
    record = build_academic_record(student, published_only=True)
    if not record["semesters"]:
        raise DomainError("This student has no published results, so there is nothing to put on a transcript.",
                          code="no_results")
    reason = interim_reason(student)
    year = timezone.localdate().year
    for _ in range(5):  # retry if two transcripts are issued at the same moment
        try:
            with transaction.atomic():
                last = IssuedTranscript.objects.filter(year=year).aggregate(m=Max("sequence"))["m"] or 0
                transcript = IssuedTranscript.objects.create(
                    serial=_serial(profile, student, year, last + 1), year=year, sequence=last + 1,
                    verification_code="".join(secrets.choice(CODE_ALPHABET) for _ in range(10)),
                    student=student, purpose=purpose.strip()[:255], signing=signing, is_interim=bool(reason),
                    interim_reason=reason, snapshot=record, content_hash=_hash(record), issued_by=actor,
                )
                log_action("transcript.issued", actor=actor, instance=transcript,
                           summary=f"Issued transcript {transcript.serial} for {student.student_number}",
                           metadata={"purpose": transcript.purpose, "interim": transcript.is_interim,
                                     "signing": signing, "cgpa": record["summary"]["cgpa"]})
                return transcript
        except IntegrityError:
            continue
    raise DomainError("Could not allocate a serial number. Please try again.", code="serial_conflict", status_code=409)


@transaction.atomic
def record_print(transcript, *, actor):
    """Every download of the official PDF is counted and logged; revoked transcripts cannot be printed."""
    require_issuer(actor)
    transcript = IssuedTranscript.objects.select_for_update().get(pk=transcript.pk)
    if transcript.status == TranscriptStatus.REVOKED:
        raise DomainError("This transcript has been revoked and can no longer be printed. Issue a new one.",
                          code="revoked", status_code=409)
    transcript.print_count += 1
    transcript.last_printed_at = timezone.now()
    transcript.save(update_fields=["print_count", "last_printed_at"])
    log_action("transcript.printed", actor=actor, instance=transcript,
               summary=f"Printed {transcript.serial} (copy {transcript.print_count})")
    return transcript


@transaction.atomic
def revoke(transcript, *, reason, actor):
    require_issuer(actor)
    if not (reason or "").strip():
        raise DomainError("Give a reason for revoking this transcript.", code="reason_required")
    transcript = IssuedTranscript.objects.select_for_update().get(pk=transcript.pk)
    if transcript.status == TranscriptStatus.REVOKED:
        raise DomainError("This transcript has already been revoked.", code="already_revoked", status_code=409)
    transcript.status, transcript.revoked_by, transcript.revoked_at = TranscriptStatus.REVOKED, actor, timezone.now()
    transcript.revoke_reason = reason.strip()
    transcript.save(update_fields=["status", "revoked_by", "revoked_at", "revoke_reason"])
    log_action("transcript.revoked", actor=actor, instance=transcript, reason=transcript.revoke_reason,
               summary=f"Revoked transcript {transcript.serial}")
    return transcript


def record_changed_since_issue(transcript):
    """True when the student's published record no longer matches what was printed."""
    current = build_academic_record(transcript.student, published_only=True)
    return _hash(current) != transcript.content_hash


def public_summary(transcript):
    """What anyone holding the transcript can confirm. Deliberately no individual grades."""
    snapshot = transcript.snapshot
    student = snapshot["student"]
    last = snapshot["semesters"][-1] if snapshot["semesters"] else None
    return {
        "institution": InstitutionProfile.load().name,
        "serial": transcript.serial,
        "status": transcript.status,
        "issued_on": timezone.localtime(transcript.issued_at).date().isoformat(),
        "revoked_on": timezone.localtime(transcript.revoked_at).date().isoformat() if transcript.revoked_at else None,
        "is_interim": transcript.is_interim,
        "student_name": student["full_name"],
        "student_number": student["student_number"],
        "programme": f"{student['programme']} ({student['programme_code']})",
        "cgpa": snapshot["summary"]["cgpa"],
        "credits_earned": snapshot["summary"]["credits_earned"],
        "covers_up_to": f"{last['academic_year']} {last['semester']}" if last else None,
    }


def normalise_code(code):
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())
