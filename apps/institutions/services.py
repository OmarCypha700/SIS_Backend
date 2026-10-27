from django.db import transaction

from .models import InstitutionProfile, StudentNumberSequence


def render_student_number(fmt, *, short, year, programme, seq):
    return fmt.format(SHORT=short, YEAR=year, PROG=programme, SEQ=seq)


@transaction.atomic
def next_student_number(*, admission_year, programme_code):
    """Generate the next student number using the institution's configured format."""
    profile = InstitutionProfile.load()
    fmt = profile.student_number_format
    key = f"{admission_year}:{programme_code}" if "{PROG" in fmt else str(admission_year)
    sequence, _ = StudentNumberSequence.objects.select_for_update().get_or_create(key=key)
    from apps.students.models import Student

    while True:
        sequence.last_value += 1
        candidate = render_student_number(
            fmt, short=profile.short_name, year=admission_year, programme=programme_code, seq=sequence.last_value
        )
        # Skip numbers already used, e.g. by imported records.
        if not Student.objects.filter(student_number=candidate).exists():
            break
    sequence.save(update_fields=["last_value"])
    return candidate
