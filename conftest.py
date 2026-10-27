"""Shared pytest fixtures: users for every role and a small, fully configured institution."""
import datetime
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academics.models import (
    AcademicYear, Course, CourseCategory, CourseOffering, Department, LecturerAssignment, Level, PeriodStatus,
    Programme, ProgrammeCourse, Semester,
)
from apps.accounts.models import Role, User
from apps.institutions.models import InstitutionProfile
from apps.results.defaults import create_standard_scheme
from apps.students.models import Student

PASSWORD = "Correct-Horse-42!"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


_counter = {"n": 0}


def make_user(role, **extra):
    _counter["n"] += 1
    n = _counter["n"]
    defaults = {"first_name": f"{role.title()}", "last_name": f"User{n}"}
    defaults.update(extra)
    email = defaults.pop("email", f"{role.lower()}{n}@example.test")
    return User.objects.create_user(email=email, password=PASSWORD, role=role, **defaults)


@pytest.fixture
def user_factory(db):
    return make_user


@pytest.fixture
def admin_user(db):
    return make_user(Role.ADMIN)


@pytest.fixture
def registrar(db):
    return make_user(Role.REGISTRAR)


@pytest.fixture
def results_officer(db):
    return make_user(Role.RESULTS_OFFICER)


@pytest.fixture
def lecturer(db):
    return make_user(Role.LECTURER)


def client_for(user):
    client = APIClient()
    client.force_login(user)
    return client


@pytest.fixture
def as_user():
    return client_for


def make_student(programme, level, *, number=None, email=None, status="ACTIVE", **extra):
    _counter["n"] += 1
    n = _counter["n"]
    email = email or f"student{n}@example.test"
    user = User.objects.create_user(email=email, password=PASSWORD, role=Role.STUDENT,
                                    first_name="Kofi", last_name=f"Student{n}")
    return Student.objects.create(
        user=user, student_number=number or f"TST/2025/{n:04d}", first_name="Kofi", last_name=f"Student{n}",
        email=email, admission_year=2025, programme=programme, current_level=level, status=status, **extra,
    )


@pytest.fixture
def world(db):
    """
    One department, one programme (Level 100/200), five courses, a current semester whose
    registration window is open now, offerings with an assigned lecturer, and two students.
    """
    profile = InstitutionProfile.load()
    profile.name, profile.short_name = "Test College", "TC"
    profile.save()
    scheme = create_standard_scheme()
    now = timezone.now()
    today = now.date()
    year = AcademicYear.objects.create(name="2025/2026", start_date=today - datetime.timedelta(days=60),
                                       end_date=today + datetime.timedelta(days=300), status=PeriodStatus.ACTIVE)
    semester = Semester.objects.create(
        academic_year=year, name="First Semester", term_number=1, start_date=today - datetime.timedelta(days=30),
        end_date=today + datetime.timedelta(days=90), registration_start=now - datetime.timedelta(days=10),
        registration_end=now + datetime.timedelta(days=10), add_drop_deadline=now + datetime.timedelta(days=20),
        status=PeriodStatus.ACTIVE, is_current=True,
    )
    dept = Department.objects.create(code="CS", name="Computer Science")
    l100 = Level.objects.create(name="Level 100", order=1)
    l200 = Level.objects.create(name="Level 200", order=2)
    programme = Programme.objects.create(code="BSC-CS", name="BSc Computer Science", department=dept,
                                         min_credits_per_semester=3, max_credits_per_semester=12)
    programme.levels.set([l100, l200])

    def course(code, credits=3):
        return Course.objects.create(code=code, title=f"Course {code}", credits=credits, department=dept)

    intro, maths, elective, advanced, other = (course("CS101"), course("MATH101"), course("CS105"),
                                               course("CS201"), course("CS107"))
    advanced.prerequisites.add(intro)
    for c, lvl, cat in [(intro, l100, CourseCategory.COMPULSORY), (maths, l100, CourseCategory.COMPULSORY),
                        (elective, l100, CourseCategory.ELECTIVE), (other, l100, CourseCategory.ELECTIVE),
                        (advanced, l200, CourseCategory.COMPULSORY)]:
        ProgrammeCourse.objects.create(programme=programme, course=c, level=lvl, term_number=1, category=cat)

    lecturer = make_user(Role.LECTURER)
    offerings = {}
    for c in (intro, maths, elective, advanced, other):
        offering = CourseOffering.objects.create(course=c, semester=semester)
        offering.programmes.add(programme)
        offerings[c.code] = offering
    LecturerAssignment.objects.create(offering=offerings["CS101"], lecturer=lecturer, is_coordinator=True)
    LecturerAssignment.objects.create(offering=offerings["MATH101"], lecturer=lecturer)

    student = make_student(programme, l100)
    other_student = make_student(programme, l100)
    return SimpleNamespace(
        profile=profile, scheme=scheme, year=year, semester=semester, department=dept, l100=l100, l200=l200,
        programme=programme, courses={c.code: c for c in (intro, maths, elective, advanced, other)},
        offerings=offerings, lecturer=lecturer, student=student, other_student=other_student,
    )


@pytest.fixture
def make_student_fn(db):
    return make_student
