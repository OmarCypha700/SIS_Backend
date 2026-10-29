from django.db import transaction

from .models import InstitutionProfile, StudentNumberSequence


class Year(int):
    """
    A year token whose width keeps the last digits: {YEAR} gives 2026, {YEAR:02} gives 26 and
    {YEAR:2} gives 26 (2005 gives 05). Other format specs behave as for ordinary numbers.
    """

    def __format__(self, spec):
        if spec.isdigit():
            width = int(spec)
            return str(int(self) % 10 ** width).zfill(width)
        return format(int(self), spec)


def render_format(fmt, *, short, year, programme="", seq=1):
    """Render a numbering format: {SHORT}, {YEAR} / {YEAR:02}, {PROG} and {SEQ:04}."""
    return fmt.format(SHORT=short, YEAR=Year(year), PROG=programme, SEQ=seq)


def render_student_number(fmt, *, short, year, programme, seq):
    return render_format(fmt, short=short, year=year, programme=programme, seq=seq)


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
