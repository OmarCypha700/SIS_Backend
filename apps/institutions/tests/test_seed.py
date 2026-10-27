import pytest
from django.core.management import CommandError, call_command

from apps.registration.models import SemesterRegistration
from apps.results.models import CourseResult
from apps.students.models import Student

pytestmark = pytest.mark.django_db


def test_seed_demo_builds_a_consistent_demo(settings):
    settings.DEBUG = True
    call_command("seed_demo", students=6)
    assert Student.objects.count() == 6
    assert CourseResult.objects.filter(is_published=True).exists()
    assert SemesterRegistration.objects.filter(semester__is_current=True).exists()
    with pytest.raises(CommandError):
        call_command("seed_demo", students=2)  # refuses to run twice


def test_seed_demo_refuses_without_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_demo")
