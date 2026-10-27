"""Pure calculation helpers: weighted totals, grade lookup and GPA. No database writes."""
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from apps.institutions.models import CgpaRetakePolicy

ZERO = Decimal("0")


def plain(value):
    """Decimal as plain text without exponent or trailing zeros: 40.00 -> "40", 12.50 -> "12.5"."""
    text = format(Decimal(value).normalize(), "f")
    return text


def weighted_total(components, marks_by_component):
    """
    Sum of (score / max_score) * weight over components. Missing scores contribute zero;
    callers decide whether missing required marks are acceptable.
    """
    total = ZERO
    for component in components:
        score = marks_by_component.get(component.id)
        if score is None:
            continue
        total += (Decimal(score) / Decimal(component.max_score)) * Decimal(component.weight)
    return total


def round_total(total, scheme):
    return Decimal(total).quantize(scheme.step, rounding=ROUND_HALF_UP)


def find_band(rounded_total, bands):
    """bands: iterable of GradeBand. Boundaries are inclusive at both ends."""
    for band in bands:
        if band.min_score <= rounded_total <= band.max_score:
            return band
    return None


def grade(total, scheme, bands):
    rounded = round_total(total, scheme)
    band = find_band(rounded, bands)
    if band is None:
        raise ValueError(f"No grade band covers a total of {rounded} in scheme '{scheme.name}'.")
    return rounded, band


def cap_band(band, bands, max_grade_point):
    """
    Limit a grade to the best band whose grade point does not exceed `max_grade_point`.
    Returns (band, capped). The underlying total is not changed, only the grade awarded.
    """
    if max_grade_point is None or band.grade_point <= Decimal(max_grade_point):
        return band, False
    allowed = [b for b in bands if b.grade_point <= Decimal(max_grade_point)]
    if not allowed:
        return band, False
    return max(allowed, key=lambda b: (b.grade_point, b.min_score)), True


def gpa(entries, places=2):
    """entries: iterable of (grade_point, credits). Returns None when no credits were attempted."""
    points = ZERO
    credits = 0
    for grade_point, credit in entries:
        points += Decimal(grade_point) * credit
        credits += credit
    if credits == 0:
        return None
    return (points / credits).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def apply_retake_policy(results, policy):
    """
    results: CourseResult-like objects in chronological order (oldest first), each with
    `course_id` and `grade_point`. Returns the subset that counts towards the CGPA.
    """
    if policy == CgpaRetakePolicy.ALL:
        return list(results)
    chosen = OrderedDict()
    for result in results:
        key = result.offering.course_id
        current = chosen.get(key)
        if current is None or policy == CgpaRetakePolicy.LATEST or (
            policy == CgpaRetakePolicy.BEST and result.grade_point > current.grade_point
        ):
            chosen[key] = result
    return list(chosen.values())
