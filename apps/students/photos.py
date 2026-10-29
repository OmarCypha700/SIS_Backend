"""
Student photos.

* Stored privately (never under /media/) and served only through the API to people allowed to
  see the student: the student themself, registry staff, results officers, and lecturers of
  courses the student is registered on.
* Every upload is re-encoded on the server: turned upright, cropped to a 3:4 passport shape,
  resized, stripped of hidden data (GPS location, camera details) and saved as WebP, with a small
  square thumbnail for lists. Staff can upload ordinary JPG, PNG or WebP files.
* Bulk upload matches each file to a student by its name: the student number with punctuation
  and letter case ignored, so "NCHT-2026-0007.jpg" and "ncht20260007.png" both match NCHT/2026/0007.
"""
import io
import os
import re
import secrets
from collections import defaultdict

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.audit.services import log_action
from common.exceptions import DomainError

from .models import Student

FULL_SIZE = (480, 640)  # 3:4 portrait
THUMB_SIZE = (128, 128)
MAX_PIXELS = 50_000_000  # refuse decompression bombs before decoding
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}  # MPO: some phone cameras' JPEG variant
ACCEPTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_BATCH = 25


def normalise(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _encode(image, size, centering):
    fitted = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=centering)
    buffer = io.BytesIO()
    fitted.save(buffer, format="WEBP", quality=82, method=6)  # a new image: no EXIF or GPS is carried over
    return buffer.getvalue()


def process(upload):
    """Returns (full, thumbnail) WebP bytes, or raises DomainError with a readable reason."""
    if upload.size > settings.MAX_UPLOAD_PHOTO_BYTES:
        raise DomainError(f"The file is larger than {settings.MAX_UPLOAD_PHOTO_BYTES // (1024 * 1024)} MB.",
                          code="too_large")
    try:
        image = Image.open(upload)
        if image.format not in ACCEPTED_FORMATS:
            raise DomainError("Use a JPG, PNG or WebP photo.", code="unsupported")
        if image.width * image.height > MAX_PIXELS:
            raise DomainError("The photo's dimensions are too large.", code="too_large")
        image.load()
    except DomainError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise DomainError("This is not a readable image. Use a JPG, PNG or WebP photo.", code="invalid_image") from None
    image = ImageOps.exif_transpose(image)  # phone photos are often stored sideways
    if image.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", image.size, "white")
        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    # Faces sit slightly above the middle of a portrait, so crop a little higher than centre.
    return _encode(image, FULL_SIZE, (0.5, 0.4)), _encode(image, THUMB_SIZE, (0.5, 0.3))


def _version(student):
    return os.path.splitext(os.path.basename(student.photo.name))[0].rsplit("-", 1)[-1] if student.photo else None


def photo_url(student, size="full"):
    version = _version(student)
    if not version:
        return None
    return f"/api/v1/students/{student.pk}/photo/?size={size}&v={version}"


def _delete_files(names):
    storage = Student._meta.get_field("photo").storage
    for name in names:
        if name:
            try:
                storage.delete(name)
            except OSError:
                pass  # a stale file is harmless; it is not reachable without a database row


@transaction.atomic
def set_photo(student, upload, *, actor, source="manual"):
    full, thumb = process(upload)
    replaced = bool(student.photo)
    old = [student.photo.name, student.photo_thumbnail.name]
    token = secrets.token_hex(5)
    student.photo.save(f"{student.pk}-{token}.webp", ContentFile(full), save=False)
    student.photo_thumbnail.save(f"{student.pk}-thumb-{token}.webp", ContentFile(thumb), save=False)
    student.save(update_fields=["photo", "photo_thumbnail", "updated_at"])
    transaction.on_commit(lambda: _delete_files(old))
    log_action("student.photo_updated", actor=actor, instance=student,
               summary=f"{'Replaced' if replaced else 'Added'} photo for {student.student_number}",
               metadata={"source": source, "replaced": replaced})
    return student


@transaction.atomic
def remove_photo(student, *, actor):
    if not student.photo:
        return student
    old = [student.photo.name, student.photo_thumbnail.name]
    student.photo = student.photo_thumbnail = ""
    student.save(update_fields=["photo", "photo_thumbnail", "updated_at"])
    transaction.on_commit(lambda: _delete_files(old))
    log_action("student.photo_removed", actor=actor, instance=student, summary=f"Removed photo for {student.student_number}")
    return student


def delete_files_for(student):
    """Used when a record created in error is deleted."""
    names = [student.photo.name, student.photo_thumbnail.name]
    transaction.on_commit(lambda: _delete_files(names))


# --- Bulk upload ------------------------------------------------------------------

def _index():
    index = defaultdict(list)
    for student in Student.objects.only("id", "student_number", "first_name", "middle_name", "last_name", "photo"):
        index[normalise(student.student_number)].append(student)
    return index


def match(filenames, index=None):
    """For each file name: the student it belongs to, or why it cannot be used."""
    index = index if index is not None else _index()
    results, claimed = [], defaultdict(list)
    for position, filename in enumerate(filenames):
        base = os.path.basename(filename or "")
        stem, extension = os.path.splitext(base)
        row = {"filename": base, "status": "matched", "message": "", "student": None}
        candidates = index.get(normalise(stem), [])
        if extension.lower() not in ACCEPTED_EXTENSIONS:
            row.update(status="invalid", message="Not a JPG, PNG or WebP file.")
        elif not normalise(stem):
            row.update(status="unmatched", message="The file name has no student number.")
        elif not candidates:
            row.update(status="unmatched", message="No student has this number. Check the file name.")
        elif len(candidates) > 1:
            numbers = ", ".join(s.student_number for s in candidates)
            row.update(status="ambiguous", message=f"Matches more than one student ({numbers}). Rename the file.")
        else:
            student = candidates[0]
            row["student"] = {"id": student.pk, "student_number": student.student_number,
                              "full_name": student.full_name, "has_photo": bool(student.photo)}
            claimed[student.pk].append(position)
        results.append(row)
    for positions in claimed.values():
        if len(positions) > 1:
            for position in positions:
                results[position].update(status="duplicate",
                                         message="More than one file is for this student. Keep one.")
    return results


def bulk_upload(files, *, replace, actor):
    """Process one batch of uploaded files. Each photo is saved independently."""
    if len(files) > MAX_BATCH:
        raise DomainError(f"Send at most {MAX_BATCH} photos per batch.", code="batch_too_large")
    index = _index()
    rows = match([f.name for f in files], index)
    outcome = []
    for upload, row in zip(files, rows):
        result = {"filename": row["filename"], "student": row["student"]}
        if row["status"] != "matched":
            outcome.append({**result, "status": "skipped", "message": row["message"]})
            continue
        student = Student.objects.get(pk=row["student"]["id"])
        if student.photo and not replace:
            outcome.append({**result, "status": "skipped",
                            "message": "Already has a photo. Tick “Replace existing photos” to replace it."})
            continue
        try:
            set_photo(student, upload, actor=actor, source="bulk")
        except DomainError as exc:
            outcome.append({**result, "status": "skipped", "message": exc.message})
            continue
        outcome.append({**result, "status": "uploaded", "message": "Replaced" if row["student"]["has_photo"] else "Added"})
    return outcome
