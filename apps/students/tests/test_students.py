import io

import pytest

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.registration.models import CourseRegistration, SemesterRegistration
from apps.students.models import Student, StudentProgrammeHistory
from conftest import PASSWORD, client_for, make_student, make_user

pytestmark = pytest.mark.django_db


def student_payload(world, **overrides):
    data = {
        "first_name": "Abena", "last_name": "Boateng", "email": "abena.boateng@example.test",
        "programme": world.programme.pk, "current_level": world.l100.pk, "admission_year": 2025,
        "gender": "F", "date_of_birth": "2004-02-01",
    }
    data.update(overrides)
    return data


def test_registrar_creates_student_with_generated_number(world, registrar):
    client = client_for(registrar)
    response = client.post("/api/v1/students/", student_payload(world), format="json")
    assert response.status_code == 201, response.data
    assert response.data["student_number"] == "TC/2025/0001"
    student = Student.objects.get(pk=response.data["id"])
    assert student.user.role == Role.STUDENT
    assert StudentProgrammeHistory.objects.filter(student=student, change_type="ADMISSION").exists()
    assert AuditLog.objects.filter(action="student.created", entity_id=str(student.pk)).exists()
    second = client.post("/api/v1/students/", student_payload(world, email="second@example.test"), format="json")
    assert second.data["student_number"] == "TC/2025/0002"


def test_duplicates_are_rejected(world, registrar):
    client = client_for(registrar)
    response = client.post("/api/v1/students/", student_payload(world, email=world.student.email), format="json")
    assert response.status_code == 400 and "email" in response.data["errors"]
    response = client.post("/api/v1/students/", student_payload(world, student_number=world.student.student_number),
                           format="json")
    assert response.status_code == 400 and "student_number" in response.data["errors"]


def test_required_fields_and_invalid_references(world, registrar):
    client = client_for(registrar)
    response = client.post("/api/v1/students/", {"first_name": "X"}, format="json")
    assert response.status_code == 400
    assert {"last_name", "email", "programme", "current_level", "admission_year"} <= set(response.data["errors"])
    response = client.post("/api/v1/students/", student_payload(world, programme=999999), format="json")
    assert response.status_code == 400 and "programme" in response.data["errors"]


def test_identity_and_status_fields_cannot_be_patched(world, registrar):
    client = client_for(registrar)
    response = client.patch(f"/api/v1/students/{world.student.pk}/", {
        "status": "GRADUATED", "student_number": "HACK/1", "programme": world.programme.pk, "phone": "0244000000",
    }, format="json")
    assert response.status_code == 200
    world.student.refresh_from_db()
    assert world.student.status == "ACTIVE"
    assert world.student.student_number != "HACK/1"
    assert world.student.phone == "0244000000"


def test_status_and_placement_changes_are_recorded(world, registrar):
    client = client_for(registrar)
    response = client.post(f"/api/v1/students/{world.student.pk}/change-status/",
                           {"status": "DEFERRED", "reason": "Medical"}, format="json")
    assert response.status_code == 200 and response.data["status"] == "DEFERRED"
    response = client.post(f"/api/v1/students/{world.student.pk}/change-placement/",
                           {"programme": world.programme.pk, "current_level": world.l200.pk, "reason": "Promoted"},
                           format="json")
    assert response.status_code == 200
    history = client.get(f"/api/v1/students/{world.student.pk}/history/").data
    assert [h["change_type"] for h in history][:2] == ["LEVEL", "STATUS"]
    assert AuditLog.objects.filter(action="student.status_changed", reason="Medical").exists()
    missing_reason = client.post(f"/api/v1/students/{world.student.pk}/change-status/", {"status": "ACTIVE"},
                                 format="json")
    assert missing_reason.status_code == 400


def test_students_cannot_access_other_records(world):
    client = client_for(world.student.user)
    assert client.get("/api/v1/students/").status_code == 403
    assert client.get(f"/api/v1/students/{world.other_student.pk}/").status_code == 403
    assert client.patch(f"/api/v1/students/{world.other_student.pk}/", {"phone": "1"}, format="json").status_code == 403
    me = client.get("/api/v1/students/me/")
    assert me.status_code == 200 and me.data["id"] == world.student.pk


def test_student_can_update_contact_details_only(world):
    client = client_for(world.student.user)
    response = client.patch("/api/v1/students/me/", {"phone": "0501112222", "status": "GRADUATED",
                                                     "first_name": "Changed"}, format="json")
    assert response.status_code == 200
    world.student.refresh_from_db()
    assert world.student.phone == "0501112222"
    assert world.student.status == "ACTIVE" and world.student.first_name == "Kofi"


def test_lecturer_sees_only_their_students_without_personal_details(world):
    reg = SemesterRegistration.objects.create(student=world.student, semester=world.semester,
                                              programme=world.programme, level=world.l100)
    CourseRegistration.objects.create(semester_registration=reg, student=world.student,
                                      offering=world.offerings["CS101"])
    client = client_for(world.lecturer)
    data = client.get("/api/v1/students/").data
    assert [row["id"] for row in data["results"]] == [world.student.pk]
    assert "date_of_birth" not in data["results"][0]
    assert client.get(f"/api/v1/students/{world.other_student.pk}/").status_code == 404
    assert client.post("/api/v1/students/", student_payload(world), format="json").status_code == 403


def test_list_pagination_search_filter_and_sort(world, registrar):
    for i in range(30):
        make_student(world.programme, world.l200 if i % 2 else world.l100)
    client = client_for(registrar)
    page = client.get("/api/v1/students/?page_size=10&page=2&ordering=-student_number").data
    assert page["count"] == 32 and page["total_pages"] == 4 and len(page["results"]) == 10
    filtered = client.get(f"/api/v1/students/?level={world.l200.pk}").data
    assert filtered["count"] == 15
    found = client.get(f"/api/v1/students/?search={world.student.student_number}").data
    assert found["count"] == 1


def test_list_query_count_is_constant(world, registrar, django_assert_max_num_queries):
    for _ in range(25):
        make_student(world.programme, world.l100)
    client = client_for(registrar)
    with django_assert_max_num_queries(8):
        client.get("/api/v1/students/?page_size=25")


def test_deactivated_student_cannot_sign_in(world, registrar, api):
    client = client_for(registrar)
    response = client.post(f"/api/v1/students/{world.student.pk}/deactivate/", {"reason": "Withdrawn"}, format="json")
    assert response.status_code == 200 and response.data["is_active"] is False
    login = api.post("/api/v1/auth/login/", {"email": world.student.email, "password": PASSWORD}, format="json")
    assert login.status_code == 400
    assert Student.objects.filter(pk=world.student.pk).exists()  # never hard-deleted
    assert client.delete(f"/api/v1/students/{world.student.pk}/").status_code == 405


def csv_file(text):
    upload = io.BytesIO(text.encode("utf-8"))
    upload.name = "students.csv"
    return upload


def test_csv_import_preview_and_confirm(world, registrar):
    client = client_for(registrar)
    text = (
        "first_name,last_name,email,programme_code,level,admission_year,date_of_birth\n"
        "Yaw,Mensah,yaw@example.test,bsc-cs,Level 100,2025,2005-01-01\n"
        f"Dup,Email,{world.student.email},BSC-CS,Level 100,2025,\n"
        "Bad,Programme,bad@example.test,NOPE,Level 100,2025,\n"
        "Again,Same,yaw@example.test,BSC-CS,Level 100,2025,\n"
    )
    preview = client.post("/api/v1/students/import/preview/", {"file": csv_file(text)}, format="multipart")
    assert preview.status_code == 200, preview.data
    assert preview.data["valid"] == 1 and preview.data["invalid"] == 3
    rows = [r["input"] for r in preview.data["rows"]]

    rejected = client.post("/api/v1/students/import/confirm/", {"rows": rows}, format="json")
    assert rejected.status_code == 400 and rejected.data["code"] == "import_invalid"
    assert not User.objects.filter(email="yaw@example.test").exists()  # all-or-nothing

    valid_rows = [r["input"] for r in preview.data["rows"] if r["valid"]]
    created = client.post("/api/v1/students/import/confirm/", {"rows": valid_rows}, format="json")
    assert created.status_code == 201 and created.data["created"] == 1
    assert Student.objects.get(email="yaw@example.test").programme == world.programme


def test_import_rejects_missing_columns(world, registrar):
    response = client_for(registrar).post("/api/v1/students/import/preview/",
                                          {"file": csv_file("first_name\nA\n")}, format="multipart")
    assert response.status_code == 400


def test_csv_export_escapes_formulas(world, registrar):
    world.student.first_name = "=HYPERLINK(\"http://evil\")"
    world.student.save()
    response = client_for(registrar).get("/api/v1/students/export/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    body = response.content.decode()
    assert "'=HYPERLINK" in body
    assert AuditLog.objects.filter(action="student.exported").exists()


def test_results_officer_has_read_only_summary_access(world):
    officer = make_user(Role.RESULTS_OFFICER)
    client = client_for(officer)
    response = client.get(f"/api/v1/students/{world.student.pk}/")
    assert response.status_code == 200 and "residential_address" not in response.data
    assert client.post("/api/v1/students/", {}, format="json").status_code == 403
