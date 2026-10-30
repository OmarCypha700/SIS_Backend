import datetime

import pytest
from django.utils import timezone

from apps.academics.models import CourseOffering, PeriodStatus, Semester
from apps.accounts.models import Role
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


def semester_payload(world, **overrides):
    now = timezone.now()
    today = now.date()
    data = {
        "academic_year": world.year.pk, "name": "Second Semester", "term_number": 2,
        "start_date": str(today + datetime.timedelta(days=100)), "end_date": str(today + datetime.timedelta(days=200)),
        "registration_start": (now + datetime.timedelta(days=90)).isoformat(),
        "registration_end": (now + datetime.timedelta(days=110)).isoformat(),
    }
    data.update(overrides)
    return data


def test_semester_date_validation(world, admin_user):
    client = client_for(admin_user)
    now = timezone.now()
    bad_window = semester_payload(world, registration_end=(now + datetime.timedelta(days=80)).isoformat())
    response = client.post("/api/v1/semesters/", bad_window, format="json")
    assert response.status_code == 400 and "registration_end" in response.data["errors"]
    outside_year = semester_payload(world, end_date=str(world.year.end_date + datetime.timedelta(days=5)))
    assert client.post("/api/v1/semesters/", outside_year, format="json").status_code == 400
    duplicate_term = semester_payload(world, term_number=1)
    assert client.post("/api/v1/semesters/", duplicate_term, format="json").status_code == 400
    assert client.post("/api/v1/semesters/", semester_payload(world), format="json").status_code == 201


def test_only_one_current_semester(world, admin_user):
    client = client_for(admin_user)
    created = client.post("/api/v1/semesters/", semester_payload(world), format="json").data
    response = client.post(f"/api/v1/semesters/{created['id']}/set-current/")
    assert response.status_code == 200 and response.data["is_current"]
    assert list(Semester.objects.filter(is_current=True).values_list("pk", flat=True)) == [created["id"]]


def test_closed_period_is_protected(world, admin_user):
    client = client_for(admin_user)
    world.semester.status = PeriodStatus.CLOSED
    world.semester.save()
    response = client.patch(f"/api/v1/semesters/{world.semester.pk}/", {"name": "Renamed"}, format="json")
    assert response.status_code == 400
    response = client.patch(f"/api/v1/semesters/{world.semester.pk}/", {"status": "ACTIVE"}, format="json")
    assert response.status_code == 200
    assert client.delete(f"/api/v1/academic-years/{world.year.pk}/").status_code == 409


def test_course_code_unique_and_prerequisite_cycles_rejected(world, admin_user):
    client = client_for(admin_user)
    response = client.post("/api/v1/courses/", {"code": "cs101", "title": "Dup", "credits": 3,
                                                "department": world.department.pk}, format="json")
    assert response.status_code == 400
    intro = world.courses["CS101"]
    advanced = world.courses["CS201"]
    response = client.patch(f"/api/v1/courses/{intro.pk}/", {"prerequisites": [advanced.pk]}, format="json")
    assert response.status_code == 400
    response = client.patch(f"/api/v1/courses/{intro.pk}/", {"prerequisites": [intro.pk]}, format="json")
    assert response.status_code == 400


def test_deleting_referenced_department_is_refused(world, admin_user):
    response = client_for(admin_user).delete(f"/api/v1/departments/{world.department.pk}/")
    assert response.status_code == 409
    assert response.data["code"] == "protected"


def test_programme_course_level_must_belong_to_programme(world, admin_user):
    from apps.academics.models import Level
    level = Level.objects.create(name="Level 400", order=4)
    response = client_for(admin_user).post("/api/v1/programme-courses/", {
        "programme": world.programme.pk, "course": world.courses["CS201"].pk, "level": level.pk, "term_number": 1,
        "category": "ELECTIVE",
    }, format="json")
    assert response.status_code == 400


def test_lecturer_sees_only_assigned_offerings_and_cannot_edit(world):
    other = make_user(Role.LECTURER)
    mine = client_for(world.lecturer).get("/api/v1/course-offerings/").data
    assert {row["course_code"] for row in mine["results"]} == {"CS101", "MATH101"}
    assert client_for(other).get("/api/v1/course-offerings/").data["count"] == 0
    offering = world.offerings["CS101"]
    assert client_for(other).get(f"/api/v1/course-offerings/{offering.pk}/").status_code == 404
    response = client_for(world.lecturer).patch(f"/api/v1/course-offerings/{offering.pk}/", {"capacity": 1}, format="json")
    assert response.status_code == 403


def test_registrar_assigns_lecturers(world, registrar):
    new_lecturer = make_user(Role.LECTURER)
    admin = make_user(Role.ADMIN)
    offering = world.offerings["CS105"]
    response = client_for(registrar).put(f"/api/v1/course-offerings/{offering.pk}/lecturers/",
                                         {"lecturers": [{"lecturer": new_lecturer.pk, "is_coordinator": True}]},
                                         format="json")
    assert response.status_code == 200
    assert response.data["assignments"][0]["lecturer"] == new_lecturer.pk
    response = client_for(registrar).put(f"/api/v1/course-offerings/{offering.pk}/lecturers/",
                                         {"lecturers": [{"lecturer": admin.pk}]}, format="json")
    assert response.status_code == 400  # administrators are not teaching staff


def test_generate_offerings_from_curriculum(world, admin_user):
    CourseOffering.objects.filter(semester=world.semester).delete()
    response = client_for(admin_user).post("/api/v1/course-offerings/generate/", {"semester": world.semester.pk},
                                           format="json")
    assert response.status_code == 200
    assert response.data["created"] == 5


def test_current_period_available_to_students(world):
    response = client_for(world.student.user).get("/api/v1/academic/current-period/")
    assert response.status_code == 200
    assert response.data["semester"]["id"] == world.semester.pk
    assert response.data["semester"]["registration_open"] is True
