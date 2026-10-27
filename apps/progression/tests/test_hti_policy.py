"""
The Health Training Institutions examination policy, clause by clause, using its preset.

(d) proceed with CGPA >= 1.5 and at most two referred courses
(f) repeat the class with CGPA < 1.5 or three referred courses
(g) withdrawal with CGPA <= 1.0, a second repeat, or more than three referred courses
"""
import datetime
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.academics.models import AcademicYear, Course, CourseCategory, CourseOffering, ProgrammeCourse, Semester
from apps.institutions.models import AcademicRegulation
from apps.institutions.regulation_presets import PRESETS
from apps.progression import services as progression
from apps.progression.models import Placement, ProgressionDecision, ProgressionReview, Standing
from apps.progression.tests.test_regulations import grade_offering, register, staff  # noqa: F401 - fixture
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db

PASS_A = (36, 90)   # 36 + 54 = 90 -> A, 4.0
PASS_C = (24, 60)   # 24 + 36 = 60 -> C, 2.0
PASS_D = (20, 50)   # 20 + 30 = 50 -> D, 1.0
FAIL = (5, 10)      # 5 + 6 = 11 -> F, 0.0


@pytest.fixture
def hti(world):
    call_command("apply_regulation_preset", "health_training_institutions")
    world.programme.max_credits_per_semester = 30
    world.programme.save()
    return world


def add_courses(world, specs):
    """specs: [(code, credits)] -> Level 100, term 1 electives offered this semester."""
    for code, credits in specs:
        course = Course.objects.create(code=code, title=code, credits=credits, department=world.department)
        ProgrammeCourse.objects.create(programme=world.programme, course=course, level=world.l100, term_number=1,
                                       category=CourseCategory.ELECTIVE)
        CourseOffering.objects.create(course=course, semester=world.semester).programmes.add(world.programme)


def sit(world, staff, grades):
    """grades: {course_code: (ca, exam)} for world.student; publishes every course."""
    register(world, world.student, world.semester, list(grades))
    for code, marks in grades.items():
        grade_offering(world, staff, CourseOffering.objects.get(course__code=code, semester=world.semester),
                       {world.student: marks})
    review = progression.generate_review(world.semester, actor=staff.registrar)
    return review, review.decisions.get(student=world.student)


def test_preset_values_and_endpoint(hti, admin_user):
    regulation = AcademicRegulation.load()
    assert regulation.promotion_min_cgpa == Decimal("1.50")
    assert regulation.max_referred_courses_to_proceed == 2
    assert regulation.dismissal_cgpa_below == Decimal("1.00") and regulation.dismissal_cgpa_inclusive
    assert regulation.dismiss_when_referred_courses_over == 3 and regulation.dismiss_on_second_repeat
    assert regulation.dismissal_status == "WITHDRAWN" and regulation.resits_enabled
    assert regulation.probation_cgpa_below is None  # the policy has no probation stage
    presets = client_for(admin_user).get("/api/v1/academic-regulations/presets/").data
    assert presets[0]["key"] == "health_training_institutions"
    assert presets[0]["values"] == PRESETS["health_training_institutions"]["values"]


def test_d_proceeds_with_two_referred_courses(hti, staff):
    _, decision = sit(hti, staff, {"CS101": PASS_A, "MATH101": PASS_A, "CS105": FAIL, "CS107": FAIL})
    assert decision.cgpa == Decimal("2.00")
    assert (decision.proposed_standing, decision.proposed_placement) == (Standing.GOOD, Placement.PROMOTE)


def test_f_three_referred_courses_repeat_the_class(hti, staff):
    add_courses(hti, [("CS109", 3)])
    _, decision = sit(hti, staff, {"CS101": PASS_A, "MATH101": PASS_A, "CS105": FAIL, "CS107": FAIL, "CS109": FAIL})
    assert decision.cgpa == Decimal("1.60")
    assert (decision.proposed_standing, decision.proposed_placement) == (Standing.GOOD, Placement.REPEAT)
    assert "3 referred courses (maximum 2)" in " ".join(decision.reasons)


def test_f_cgpa_below_one_point_five_repeats_the_class(hti, staff):
    _, decision = sit(hti, staff, {"CS101": PASS_D, "MATH101": PASS_D, "CS105": PASS_D, "CS107": PASS_C})
    assert decision.cgpa == Decimal("1.25")  # no referred courses, but CGPA too low
    assert (decision.proposed_standing, decision.proposed_placement) == (Standing.GOOD, Placement.REPEAT)
    assert "minimum 1.50" in " ".join(decision.reasons)


def test_g_cgpa_of_one_or_less_is_withdrawn_on_apply(hti, staff):
    review, decision = sit(hti, staff, {"CS101": PASS_A, "MATH101": FAIL, "CS105": FAIL, "CS107": FAIL})
    assert decision.cgpa == Decimal("1.00")  # exactly 1.0 counts ("1.0 or less")
    assert decision.proposed_standing == Standing.DISMISS
    progression.apply_review(review, actor=staff.registrar)
    hti.student.refresh_from_db()
    assert hti.student.status == "WITHDRAWN"


def test_g_more_than_three_referred_courses_is_withdrawal(hti, staff):
    add_courses(hti, [("CS111", 1), ("CS112", 1)])
    _, decision = sit(hti, staff, {"CS101": PASS_A, "MATH101": PASS_A, "CS105": FAIL, "CS107": FAIL,
                                   "CS111": FAIL, "CS112": FAIL})
    assert decision.cgpa > Decimal("1.00")  # withdrawn for the number of referred courses, not the CGPA
    assert decision.proposed_standing == Standing.DISMISS
    assert "4 referred courses (more than 3)" in " ".join(decision.reasons)


def test_g_second_repeat_is_withdrawal(hti, staff):
    # Last year's review already made the student repeat Level 100.
    last_year = AcademicYear.objects.create(
        name="Previous", start_date=hti.year.start_date - datetime.timedelta(days=365),
        end_date=hti.year.start_date - datetime.timedelta(days=1), status="CLOSED",
    )
    earlier = Semester.objects.create(
        academic_year=last_year, name="Earlier", term_number=1, start_date=last_year.start_date,
        end_date=last_year.start_date + datetime.timedelta(days=90), registration_start=hti.semester.registration_start,
        registration_end=hti.semester.registration_end, status="CLOSED",
    )
    old_review = ProgressionReview.objects.create(semester=earlier, status="APPLIED")
    ProgressionDecision.objects.create(
        review=old_review, student=hti.student, programme=hti.programme, level=hti.l100,
        proposed_standing=Standing.GOOD, proposed_placement=Placement.REPEAT,
        final_standing=Standing.GOOD, final_placement=Placement.REPEAT, applied=True,
    )
    _, decision = sit(hti, staff, {"CS101": PASS_D, "MATH101": PASS_D, "CS105": PASS_D, "CS107": PASS_C})
    assert decision.proposed_standing == Standing.DISMISS
    assert "second repeat" in " ".join(decision.reasons)


def test_rules_validate_against_each_other(admin_user):
    api = client_for(admin_user)
    bad = api.patch("/api/v1/academic-regulations/", {"promotion_min_cgpa": "1.00", "dismissal_cgpa_below": "1.50"},
                    format="json")
    assert bad.status_code == 400 and "minimum CGPA for promotion" in str(bad.data)
    bad = api.patch("/api/v1/academic-regulations/", {"max_referred_courses_to_proceed": 3,
                                                       "dismiss_when_referred_courses_over": 2}, format="json")
    assert bad.status_code == 400
    assert client_for(make_user("LECTURER")).get("/api/v1/academic-regulations/presets/").status_code == 403
