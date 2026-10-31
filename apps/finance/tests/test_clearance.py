"""Financial clearance before registration, kept by the Accounts Office."""
import io

import pytest

from apps.accounts.models import Office, Role
from apps.accounts import appointments
from apps.audit.models import AuditLog
from apps.finance.models import FinancialClearance
from apps.institutions.models import InstitutionProfile
from apps.notifications.models import Kind, Notification
from apps.progression.tests.test_regulations import register
from apps.registration.models import SemesterRegistration
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def accounts(db):
    return make_user(Role.ACCOUNTS_OFFICER)


def require(**values):
    profile = InstitutionProfile.load()
    profile.require_financial_clearance = True
    for key, value in values.items():
        setattr(profile, key, value)
    profile.save()


def offering_ids(world, *codes):
    return [world.offerings[c].pk for c in codes]


def test_registration_waits_for_clearance_when_required(world, accounts):
    require()
    student_api = client_for(world.student.user)
    options = student_api.get("/api/v1/registrations/current/").data
    assert "not been financially cleared" in options["financial_clearance"]["message"]
    response = student_api.post("/api/v1/registrations/submit/", {"offering_ids": offering_ids(world, "CS101", "MATH101")},
                                format="json")
    assert response.status_code == 403 and response.data["code"] == "not_cleared"

    office = client_for(accounts)
    listed = office.get("/api/v1/financial-clearances/?cleared=false").data
    assert {r["id"] for r in listed["results"]} >= {world.student.pk, world.other_student.pk}
    assert "email" not in listed["results"][0]  # identity, programme and level only
    response = office.post("/api/v1/financial-clearances/set/",
                           {"semester": world.semester.pk, "students": [world.student.pk], "cleared": True,
                            "note": "Receipt 004512"}, format="json")
    assert response.data["changed"] == 1
    assert Notification.objects.filter(recipient=world.student.user, kind=Kind.FINANCIAL_CLEARANCE).exists()
    assert AuditLog.objects.filter(action="finance.cleared", actor=accounts).exists()

    assert student_api.get("/api/v1/registrations/current/").data["financial_clearance"]["message"] is None
    assert student_api.post("/api/v1/registrations/submit/", {"offering_ids": offering_ids(world, "CS101", "MATH101")},
                            format="json").status_code == 200


def test_off_by_default_and_revoking_needs_a_reason(world, accounts):
    register(world, world.student, world.semester, ["CS101", "MATH101"])  # nothing required yet
    office = client_for(accounts)
    body = {"semester": world.semester.pk, "students": [world.student.pk], "cleared": False}
    assert office.post("/api/v1/financial-clearances/set/", body, format="json").status_code == 400
    assert office.post("/api/v1/financial-clearances/set/", {**body, "note": "Cheque bounced"},
                       format="json").status_code == 200
    assert not FinancialClearance.objects.get(student=world.student).cleared
    assert SemesterRegistration.objects.filter(student=world.student).exists()  # a registration is never undone


def test_only_an_administrator_can_register_an_uncleared_student(world, registrar, admin_user):
    require()
    body = {"student": world.student.pk, "semester": world.semester.pk,
            "offering_ids": offering_ids(world, "CS101", "MATH101"), "reason": "Late registration"}
    response = client_for(registrar).post("/api/v1/registrations/on-behalf/", body, format="json")
    assert response.status_code == 403 and response.data["code"] == "not_cleared"
    assert client_for(admin_user).post("/api/v1/registrations/on-behalf/", body, format="json").status_code == 200
    assert AuditLog.objects.filter(action="registration.clearance_override").exists()

    rows = client_for(registrar).post("/api/v1/registrations/bulk/preview/",
                                      {"semester": world.semester.pk, "students": [world.other_student.pk]},
                                      format="json").data["results"]
    assert rows[0]["outcome"] == "not_cleared"
    waiting = client_for(registrar).get(f"/api/v1/registrations/not-registered/?semester={world.semester.pk}"
                                        "&cleared=false").data["results"]
    assert [r["id"] for r in waiting] == [world.other_student.pk] and waiting[0]["financially_cleared"] is False


def test_results_can_be_withheld_until_cleared(world, accounts):
    require(withhold_results_until_cleared=True)
    student_api = client_for(world.student.user)
    response = student_api.get("/api/v1/my-results/")
    assert response.status_code == 403 and response.data["code"] == "results_withheld"
    client_for(accounts).post("/api/v1/financial-clearances/set/",
                              {"semester": world.semester.pk, "students": [world.student.pk], "cleared": True},
                              format="json")
    assert student_api.get("/api/v1/my-results/").status_code == 200


def test_import_from_the_accounting_system(world, accounts):
    csv = (f"student_number,cleared,note\n{world.student.student_number},yes,Receipt 1\n"
           f"{world.other_student.student_number},no,Balance outstanding\nNOPE/1,yes,\n")
    upload = io.BytesIO(csv.encode())
    upload.name = "clearance.csv"
    response = client_for(accounts).post("/api/v1/financial-clearances/import/",
                                         {"file": upload, "semester": world.semester.pk}, format="multipart")
    assert response.status_code == 200, response.data
    assert response.data["cleared"] == 1 and response.data["not_cleared"] == 1 and response.data["unknown"] == ["NOPE/1"]
    summary = client_for(accounts).get("/api/v1/financial-clearances/summary/").data
    assert summary["cleared"] == 1


def test_accounts_officer_sees_no_academic_or_personal_records(world, accounts, admin_user):
    office = client_for(accounts)
    assert office.get("/api/v1/students/").status_code == 403
    assert office.get("/api/v1/my-results/").status_code == 403
    assert office.get("/api/v1/result-sheets/").status_code == 403
    assert office.get("/api/v1/dashboard/").data["kind"] == "accounts"
    assert client_for(world.lecturer).get("/api/v1/financial-clearances/").status_code == 403
    # An appointed Accounts Officer (e.g. a registry clerk) can do the same while appointed.
    clerk = make_user(Role.LECTURER)
    appointments.appoint(clerk, office=Office.ACCOUNTS_OFFICER, starts_on=world.semester.start_date, actor=admin_user)
    assert client_for(clerk).get("/api/v1/financial-clearances/").status_code == 200
