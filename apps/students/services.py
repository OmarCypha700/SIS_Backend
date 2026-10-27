from django.db import transaction
from django.db.models.functions import Lower

from apps.accounts.models import Role, User
from apps.audit.services import diff, log_action, snapshot
from apps.institutions.services import next_student_number
from common.exceptions import DomainError

from .models import Student, StudentProgrammeHistory
from .serializers import StudentImportRowSerializer

@transaction.atomic
def create_student(data, *, actor, password=None):
    data = dict(data)
    if not data.get("student_number"):
        data["student_number"] = next_student_number(
            admission_year=data["admission_year"], programme_code=data["programme"].code
        )
    user = User.objects.create_user(
        email=data["email"], password=password or None, role=Role.STUDENT,
        first_name=data["first_name"], last_name=data["last_name"], must_change_password=bool(password),
    )
    student = Student.objects.create(user=user, **data)
    StudentProgrammeHistory.objects.create(
        student=student, change_type=StudentProgrammeHistory.ChangeType.ADMISSION, to_programme=student.programme,
        to_level=student.current_level, to_status=student.status, changed_by=actor, reason="Record created",
    )
    log_action("student.created", actor=actor, instance=student, summary=f"Created student {student.student_number}",
               after=snapshot(student))
    return student


@transaction.atomic
def update_student(student, data, *, actor):
    before = snapshot(student)
    for key, value in data.items():
        setattr(student, key, value)
    student.save()
    user = student.user
    user.email, user.first_name, user.last_name = student.email, student.first_name, student.last_name
    user.save(update_fields=["email", "first_name", "last_name", "updated_at"])
    before, after = diff(before, snapshot(student))
    if after:
        log_action("student.updated", actor=actor, instance=student,
                   summary=f"Updated student {student.student_number}", before=before, after=after)
    return student


@transaction.atomic
def change_status(student, *, status, reason, actor):
    if student.status == status:
        raise DomainError("The student already has this status.")
    previous = student.status
    student.status = status
    student.save(update_fields=["status", "updated_at"])
    StudentProgrammeHistory.objects.create(
        student=student, change_type=StudentProgrammeHistory.ChangeType.STATUS, from_status=previous,
        to_status=status, reason=reason, changed_by=actor,
    )
    log_action("student.status_changed", actor=actor, instance=student, reason=reason,
               summary=f"{student.student_number}: {previous} -> {status}",
               before={"status": previous}, after={"status": status})
    return student


@transaction.atomic
def change_placement(student, *, programme, level, reason, actor):
    if student.programme_id == programme.pk and student.current_level_id == level.pk:
        raise DomainError("The student is already in this programme and level.")
    change_type = (StudentProgrammeHistory.ChangeType.PROGRAMME if student.programme_id != programme.pk
                   else StudentProgrammeHistory.ChangeType.LEVEL)
    before = {"programme": student.programme_id, "current_level": student.current_level_id}
    StudentProgrammeHistory.objects.create(
        student=student, change_type=change_type, from_programme=student.programme, to_programme=programme,
        from_level=student.current_level, to_level=level, reason=reason, changed_by=actor,
    )
    student.programme, student.current_level = programme, level
    student.save(update_fields=["programme", "current_level", "updated_at"])
    log_action("student.placement_changed", actor=actor, instance=student, reason=reason,
               summary=f"{student.student_number}: placement changed",
               before=before, after={"programme": programme.pk, "current_level": level.pk})
    return student


@transaction.atomic
def set_active(student, *, active, reason, actor):
    user = student.user
    user.is_active = active
    user.save(update_fields=["is_active", "updated_at"])
    student.is_archived = not active
    student.save(update_fields=["is_archived", "updated_at"])
    log_action("student.activated" if active else "student.deactivated", actor=actor, instance=student,
               reason=reason, summary=f"{'Re-activated' if active else 'Deactivated'} {student.student_number}")
    return student


# --- CSV import -----------------------------------------------------------

def validate_import_rows(rows):
    """
    Validate every row without writing anything.

    Returns a list of {"row": n, "data": {...} | None, "errors": {...}, "warnings": [...]}.
    """
    results = []
    seen_emails, seen_numbers = {}, {}
    validated = []
    for index, raw in enumerate(rows, start=2):  # row 1 is the header
        serializer = StudentImportRowSerializer(data=raw)
        if serializer.is_valid():
            validated.append((index, raw, serializer.validated_data, {}))
        else:
            validated.append((index, raw, None, {k: [str(m) for m in v] for k, v in serializer.errors.items()}))

    emails = [v["email"] for _, _, v, _ in validated if v]
    numbers = [v["student_number"] for _, _, v, _ in validated if v and v["student_number"]]
    existing_emails = set(User.objects.filter(email__in=emails).values_list("email", flat=True))
    existing_numbers = set(Student.objects.filter(student_number__in=numbers).values_list("student_number", flat=True))
    names = {(v["first_name"].lower(), v["last_name"].lower(), v.get("date_of_birth")) for _, _, v, _ in validated if v}
    possible_duplicates = set()
    if names:
        last_names = {n[1] for n in names}
        for first, last, dob in Student.objects.filter(last_name__in=[n.title() for n in last_names] + list(last_names)) \
                .values_list("first_name", "last_name", "date_of_birth"):
            possible_duplicates.add((first.lower(), last.lower(), dob))

    for index, raw, data, errors in validated:
        warnings = []
        if data:
            email, number = data["email"], data["student_number"]
            if email in existing_emails:
                errors.setdefault("email", []).append("An account with this email already exists.")
            if email in seen_emails:
                errors.setdefault("email", []).append(f"Duplicate of row {seen_emails[email]} in this file.")
            seen_emails.setdefault(email, index)
            if number:
                if number in existing_numbers:
                    errors.setdefault("student_number", []).append("This student number is already in use.")
                if number in seen_numbers:
                    errors.setdefault("student_number", []).append(f"Duplicate of row {seen_numbers[number]} in this file.")
                seen_numbers.setdefault(number, index)
            if (data["first_name"].lower(), data["last_name"].lower(), data.get("date_of_birth")) in possible_duplicates:
                warnings.append("A student with the same name and date of birth already exists.")
        results.append({
            "row": index,
            "input": raw,
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "data": data,
        })
    return results


@transaction.atomic
def import_students(rows, *, actor):
    results = validate_import_rows(rows)
    invalid = [r for r in results if not r["valid"]]
    if invalid:
        raise DomainError(
            f"{len(invalid)} row(s) have errors. Nothing was imported.", code="import_invalid",
            errors=[{"row": r["row"], "errors": r["errors"]} for r in invalid],
        )
    created = []
    for result in results:
        data = dict(result["data"])
        data["programme"] = data.pop("programme_code")
        data["current_level"] = data.pop("level")
        created.append(create_student(data, actor=actor))
    log_action("student.imported", actor=actor, entity_type="students.student",
               summary=f"Imported {len(created)} students", metadata={"count": len(created)})
    return created
