from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from apps.institutions.models import CgpaRetakePolicy
from apps.results import grading
from apps.results.defaults import STANDARD_BANDS
from apps.results.models import validate_bands


def bands(scheme_rows=STANDARD_BANDS):
    return [SimpleNamespace(letter=l, min_score=Decimal(lo), max_score=Decimal(hi), grade_point=Decimal(gp), is_pass=p)
            for l, lo, hi, gp, p, _ in scheme_rows]


SCHEME0 = SimpleNamespace(name="whole", step=Decimal("1"))
SCHEME1 = SimpleNamespace(name="one-dp", step=Decimal("0.1"))


def band_dicts(rows):
    return [{"letter": l, "min_score": lo, "max_score": hi} for l, lo, hi in rows]


def test_standard_bands_are_valid():
    validate_bands(band_dicts([(l, lo, hi) for l, lo, hi, *_ in STANDARD_BANDS]), 0)


@pytest.mark.parametrize("rows, message", [
    ([("A", 50, 100), ("F", 0, 50)], "overlap"),
    ([("A", 51, 100), ("F", 0, 49)], "gap"),
    ([("A", 50, 100), ("F", 1, 49)], "start at 0"),
    ([("A", 50, 99), ("F", 0, 49)], "end at 100"),
    ([("A", 50, 100), ("A", 0, 49)], "only appear once"),
    ([("A", Decimal("49.5"), 100), ("F", 0, Decimal("49.4"))], "decimal places"),
])
def test_invalid_band_configurations(rows, message):
    with pytest.raises(ValidationError) as exc:
        validate_bands(band_dicts(rows), 0)
    assert message in str(exc.value)


def test_one_decimal_bands_must_be_contiguous_at_that_precision():
    validate_bands(band_dicts([("P", Decimal("50.0"), 100), ("F", 0, Decimal("49.9"))]), 1)
    with pytest.raises(ValidationError):
        validate_bands(band_dicts([("P", Decimal("50.0"), 100), ("F", 0, Decimal("49.8"))]), 1)


@pytest.mark.parametrize("total, letter", [
    ("100", "A"), ("80", "A"), ("79.5", "A"), ("79.49", "B+"), ("79", "B+"), ("75", "B+"),
    ("74.5", "B+"), ("50", "D"), ("49.5", "D"), ("49.49", "F"), ("0", "F"),
])
def test_exact_boundaries_and_half_up_rounding(total, letter):
    rounded, band = grading.grade(Decimal(total), SCHEME0, bands())
    assert band.letter == letter


def test_weighted_total():
    components = [SimpleNamespace(id=1, weight=Decimal("30"), max_score=Decimal("50")),
                  SimpleNamespace(id=2, weight=Decimal("70"), max_score=Decimal("100"))]
    assert grading.weighted_total(components, {1: Decimal("40"), 2: Decimal("80")}) == Decimal("80")
    assert grading.weighted_total(components, {1: Decimal("25")}) == Decimal("15")


def test_gpa_is_credit_weighted():
    assert grading.gpa([(Decimal("4"), 3), (Decimal("2"), 1)]) == Decimal("3.50")
    assert grading.gpa([(Decimal("4"), 2), (Decimal("3.5"), 3), (Decimal("0"), 1)], places=3) == Decimal("3.083")
    assert grading.gpa([]) is None


def _result(course_id, gp):
    return SimpleNamespace(offering=SimpleNamespace(course_id=course_id), grade_point=Decimal(gp))


def test_retake_policies():
    history = [_result(1, "0"), _result(2, "3"), _result(1, "2.5"), _result(1, "1.5")]
    latest = grading.apply_retake_policy(history, CgpaRetakePolicy.LATEST)
    best = grading.apply_retake_policy(history, CgpaRetakePolicy.BEST)
    every = grading.apply_retake_policy(history, CgpaRetakePolicy.ALL)
    assert sorted(r.grade_point for r in latest) == [Decimal("1.5"), Decimal("3")]
    assert sorted(r.grade_point for r in best) == [Decimal("2.5"), Decimal("3")]
    assert len(every) == 4
