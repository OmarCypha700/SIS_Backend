"""
Bulk mark entry from a spreadsheet (CSV or Excel).

The lecturer downloads a template that already lists the class and one column per assessment
component, fills it in offline, and uploads it. The upload is only *checked* here: the result
is a preview with per-row problems and the list of mark changes. Saving goes through the normal
`save_marks` service, so the same permissions, deadline, locking and audit rules apply.
"""
import re
from decimal import Decimal, InvalidOperation

from common.exceptions import DomainError

from .grading import plain
from .services import _active_registrations, forbidden, is_assigned_lecturer

STUDENT_NUMBER = "student_number"
STUDENT_NAME = "student_name"
_SUFFIX = re.compile(r"\s*\(\s*/\s*[\d.]+\s*\)\s*$")


def _student_key(header):
    candidate = header.strip().replace(" ", "_")
    return candidate if candidate in (STUDENT_NUMBER, STUDENT_NAME) else header


def component_header(component):
    return f"{component.name} (/{plain(component.max_score)})"


def _normalise(header):
    return _SUFFIX.sub("", header or "").strip().lower()


def template_rows(sheet):
    components = list(sheet.offering.assessment_components.all())
    registrations = list(_active_registrations(sheet.offering))
    from .models import StudentMark

    marks = {(m.registration_id, m.component_id): m.score
             for m in StudentMark.objects.filter(registration__in=registrations, component__in=components)}
    header = [STUDENT_NUMBER, STUDENT_NAME, *[component_header(c) for c in components]]
    rows = [[r.student.student_number, r.student.full_name, *[marks.get((r.id, c.id)) for c in components]]
            for r in registrations]
    return header, rows


def check_permissions(sheet, actor):
    if not is_assigned_lecturer(actor, sheet.offering):
        raise forbidden("Only lecturers assigned to this course can import marks.")
    if not sheet.is_editable:
        raise DomainError(f"Results are {sheet.get_status_display().lower()} and can no longer be edited.",
                          code="sheet_locked", status_code=409)
    if not sheet.offering.semester.result_entry_is_open():
        raise DomainError("The result-entry deadline for this semester has passed.", code="entry_closed", status_code=409)


def preview_import(sheet, records, *, actor):
    """records: list of dicts from common.tabular.read_table. Returns the preview payload."""
    check_permissions(sheet, actor)
    components = list(sheet.offering.assessment_components.all())
    if not components:
        raise DomainError("Set up the assessment components before importing marks.", code="no_components")
    if not records:
        raise DomainError("The file contains no data rows.", code="empty_file")

    # Accept "Student Number" or "student number" as well as the template's student_number.
    records = [{_student_key(key): value for key, value in record.items()} for record in records]
    headers = list(records[0].keys())
    if STUDENT_NUMBER not in headers:
        raise DomainError("The file must have a 'student_number' column.", code="missing_column")
    by_name = {_normalise(c.name): c for c in components}
    column_map, ignored = {}, []
    for header in headers:
        if header in (STUDENT_NUMBER, STUDENT_NAME):
            continue
        component = by_name.get(_normalise(header))
        if component is None:
            ignored.append(header)
        else:
            column_map[header] = component
    if not column_map:
        raise DomainError("No column matches an assessment component. Use the downloaded template.",
                          code="no_matching_columns")

    registrations = {r.student.student_number.upper(): r for r in _active_registrations(sheet.offering)}
    from .models import StudentMark

    current = {(m.registration_id, m.component_id): m.score
               for m in StudentMark.objects.filter(registration__in=registrations.values(), component__in=components)}

    rows, entries, seen = [], [], {}
    for index, record in enumerate(records, start=2):
        number = (record.get(STUDENT_NUMBER) or "").strip().upper()
        errors, values = [], {}
        registration = registrations.get(number)
        if not number:
            errors.append("Student number is missing.")
        elif registration is None:
            errors.append("This student is not registered for this course.")
        elif number in seen:
            errors.append(f"Duplicate of row {seen[number]}.")
        seen.setdefault(number, index)
        row_entries = []
        for header, component in column_map.items():
            raw = (record.get(header) or "").strip()
            values[str(component.id)] = raw
            if raw == "":
                continue  # blank cells leave existing marks unchanged
            try:
                score = Decimal(raw).quantize(Decimal("0.01"))
            except InvalidOperation:
                errors.append(f"{component.name}: '{raw}' is not a number.")
                continue
            if score < 0 or score > component.max_score:
                errors.append(f"{component.name}: {raw} is outside 0–{plain(component.max_score)}.")
                continue
            if registration is not None and current.get((registration.id, component.id)) != score:
                row_entries.append({"registration": registration.id, "component": component.id, "score": str(score)})
        if not errors:
            entries.extend(row_entries)
        rows.append({
            "row": index,
            "student_number": number,
            "student_name": registration.student.full_name if registration else (record.get(STUDENT_NAME) or ""),
            "values": values,
            "changes": len(row_entries) if not errors else 0,
            "errors": errors,
        })

    missing = sorted(set(registrations) - set(seen))
    return {
        "components": [{"id": c.id, "name": c.name, "max_score": str(c.max_score)} for c in column_map.values()],
        "ignored_columns": ignored,
        "unmapped_components": [c.name for c in components if c not in column_map.values()],
        "rows": rows,
        "valid_rows": sum(1 for r in rows if not r["errors"]),
        "invalid_rows": sum(1 for r in rows if r["errors"]),
        "students_not_in_file": [{"student_number": n, "student_name": registrations[n].student.full_name} for n in missing],
        "entries": entries,
    }
