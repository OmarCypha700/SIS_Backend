"""
A starting grading scheme commonly used by Ghanaian tertiary institutions.

It is only a convenience for new installations: administrators can edit it or create
their own scheme, and nothing in the application depends on these particular values.
"""
from decimal import Decimal

from django.db import transaction

from .models import GradeBand, GradingScheme

STANDARD_BANDS = [
    ("A", 80, 100, "4.00", True, "Excellent"),
    ("B+", 75, 79, "3.50", True, "Very good"),
    ("B", 70, 74, "3.00", True, "Good"),
    ("C+", 65, 69, "2.50", True, "Fairly good"),
    ("C", 60, 64, "2.00", True, "Average"),
    ("D+", 55, 59, "1.50", True, "Below average"),
    ("D", 50, 54, "1.00", True, "Marginal pass"),
    ("F", 0, 49, "0.00", False, "Fail"),
]


@transaction.atomic
def create_standard_scheme(name="Standard 4.0 scale", make_default=True):
    scheme, created = GradingScheme.objects.get_or_create(
        name=name, defaults={"description": "Whole-number totals, inclusive boundaries.", "round_to_decimals": 0}
    )
    if created:
        GradeBand.objects.bulk_create([
            GradeBand(scheme=scheme, letter=letter, min_score=Decimal(low), max_score=Decimal(high),
                      grade_point=Decimal(point), is_pass=passed, remark=remark)
            for letter, low, high, point, passed, remark in STANDARD_BANDS
        ])
    if make_default and not GradingScheme.objects.filter(is_default=True).exclude(pk=scheme.pk).exists():
        scheme.is_default = True
        scheme.save(update_fields=["is_default", "updated_at"])
    return scheme
