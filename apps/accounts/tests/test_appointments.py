"""Offices held through appointments: tutors who also serve as registrar or results officer."""
import datetime

import pytest
from django.utils import timezone

from apps.accounts import appointments
from apps.accounts.models import Appointment, Office, Role, User
from apps.audit.models import AuditLog
from apps.notifications.models import Kind, Notification
from apps.results import services as results
from apps.results.models import SheetStatus
from apps.results.tests.test_signoff import submitted_by
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def fresh(user):
    return User.objects.get(pk=user.pk)


def appoint(user, office, admin, **extra):
    return appointments.appoint(user, office=office, starts_on=extra.pop("starts_on", TODAY), actor=admin, **extra)


def test_a_tutor_appointed_results_officer_keeps_teaching_and_gains_the_office(world, admin_user):
    tutor = make_user(Role.LECTURER)
    appoint(tutor, Office.RESULTS_OFFICER, admin_user, reference="GCH/APT/2026/14")
    tutor = fresh(tutor)
    assert tutor.roles == {Role.LECTURER, Role.RESULTS_OFFICER}
    assert tutor.has_capability("can_approve_results") and not tutor.can_approve_results  # from the office only

    # A colleague's course: the tutor approves it as results officer.
    sheet = submitted_by(world, world.lecturer)
    response = client_for(tutor).post(f"/api/v1/result-sheets/{sheet.pk}/approve/", {}, format="json")
    assert response.status_code == 200, response.data
    entry = AuditLog.objects.filter(action__startswith="results.", actor=tutor).latest("id")
    assert entry.actor_roles == "LECTURER RESULTS_OFFICER"

    # The tutor's own course: still entered and submitted as its lecturer.
    me = client_for(tutor).get("/api/v1/auth/me/").data
    assert set(me["roles"]) == {"LECTURER", "RESULTS_OFFICER"}
    assert me["offices"][0]["title"] == "Results Officer" and me["capabilities"]["can_approve_results"] is True


def test_ending_the_appointment_removes_the_office_at_once(world, admin_user):
    tutor = make_user(Role.LECTURER)
    appointment = appoint(tutor, Office.RESULTS_OFFICER, admin_user)
    sheet = submitted_by(world, world.lecturer)
    signed_in = client_for(tutor)
    assert signed_in.get(f"/api/v1/result-sheets/{sheet.pk}/").data["permissions"]["can_approve"] is True

    response = client_for(admin_user).post(f"/api/v1/appointments/{appointment.pk}/end/",
                                           {"reason": "Reshuffle by the Principal"}, format="json")
    assert response.status_code == 200 and response.data["state"] == "ended"
    # The same open session, next request: the office is gone; teaching their own course is not.
    # Out of scope again: a colleague's sheet is not even visible, let alone approvable.
    assert signed_in.post(f"/api/v1/result-sheets/{sheet.pk}/approve/", {}, format="json").status_code == 404
    assert signed_in.get(f"/api/v1/result-sheets/{sheet.pk}/").status_code == 404
    assert set(signed_in.get("/api/v1/auth/me/").data["roles"]) == {"LECTURER"}
    assert Notification.objects.filter(recipient=tutor, kind=Kind.APPOINTMENT).count() == 2


def test_relieved_of_post_with_the_account_disabled_keeps_their_record(world, admin_user):
    registrar_tutor = make_user(Role.LECTURER)
    appointment = appoint(registrar_tutor, Office.REGISTRAR, admin_user)
    client_for(registrar_tutor).get("/api/v1/students/")  # acted as registrar
    response = client_for(admin_user).post(f"/api/v1/appointments/{appointment.pk}/end/",
                                           {"reason": "Relieved pending a malpractice inquiry",
                                            "deactivate_account": True}, format="json")
    assert response.status_code == 200
    assert fresh(registrar_tutor).is_active is False
    appointment.refresh_from_db()
    assert appointment.ends_on == TODAY and appointment.end_reason.startswith("Relieved")
    assert AuditLog.objects.filter(action="appointment.ended", entity_id=str(appointment.pk)).exists()
    # History is permanent: the record cannot be changed or the account deleted.
    assert client_for(admin_user).patch(f"/api/v1/appointments/{appointment.pk}/", {"notes": "x"},
                                        format="json").status_code == 409
    blockers = client_for(admin_user).get(f"/api/v1/users/{registrar_tutor.pk}/deletion-check/").data["blockers"]
    assert any("held an office" in b for b in blockers)


def test_office_alerts_go_to_the_current_holder_not_a_former_one(world, admin_user):
    former, current = make_user(Role.LECTURER), make_user(Role.LECTURER)
    old = appoint(former, Office.RESULTS_OFFICER, admin_user, starts_on=TODAY - datetime.timedelta(days=200))
    appointments.end_appointment(old, reason="Term ended", actor=admin_user)
    appoint(current, Office.RESULTS_OFFICER, admin_user, acting=True)
    submitted_by(world, world.lecturer)
    alerted = set(Notification.objects.filter(kind=Kind.RESULTS_SUBMITTED).values_list("recipient", flat=True))
    assert current.pk in alerted and former.pk not in alerted
    assert fresh(current).capacity_codes() == ["LECTURER", "ACTING_RESULTS_OFFICER"]


def test_office_holders_my_courses_lists_only_assigned_courses_and_marks_stay_locked(world, admin_user):
    officer = make_user(Role.LECTURER)
    appoint(officer, Office.RESULTS_OFFICER, admin_user)
    signed_in = client_for(officer)
    assert signed_in.get("/api/v1/course-offerings/").data["count"] == len(world.offerings)  # oversight view
    assert signed_in.get("/api/v1/course-offerings/?mine=true").data["count"] == 0  # "My courses"

    sheet = submitted_by(world, world.lecturer)
    sheet.status = SheetStatus.DRAFT
    sheet.save(update_fields=["status"])
    detail = signed_in.get(f"/api/v1/result-sheets/{sheet.pk}/").data
    assert detail["permissions"]["can_enter_marks"] is False and detail["permissions"]["can_submit"] is False
    reg = world.student.course_registrations.get(offering=sheet.offering)
    marks = {"marks": [{"registration": reg.id, "component": detail["components"][0]["id"], "score": "10"}]}
    assert signed_in.put(f"/api/v1/result-sheets/{sheet.pk}/marks/", marks, format="json").status_code == 403

    world.offerings["CS105"].assignments.create(lecturer=officer)
    mine = signed_in.get("/api/v1/course-offerings/?mine=true").data["results"]
    assert [row["course_code"] for row in mine] == ["CS105"]


def test_a_future_appointment_starts_on_its_date(world, admin_user):
    tutor = make_user(Role.LECTURER)
    appoint(tutor, Office.REGISTRAR, admin_user, starts_on=TODAY + datetime.timedelta(days=7))
    assert fresh(tutor).roles == {Role.LECTURER}
    assert client_for(tutor).get("/api/v1/students/?page_size=1").status_code == 200  # lecturer view only
    assert Appointment.objects.current(TODAY + datetime.timedelta(days=7)).filter(user=tutor).exists()


def test_registrar_and_results_officer_accounts_can_teach(world, registrar, results_officer, admin_user):
    for teacher in (registrar, results_officer):
        offering = world.offerings["CS105"]
        response = client_for(admin_user).put(f"/api/v1/course-offerings/{offering.pk}/lecturers/",
                                              {"lecturers": [{"lecturer": teacher.pk}]}, format="json")
        assert response.status_code == 200, response.data
        assert results.is_assigned_lecturer(fresh(teacher), offering)
    lecturers = client_for(registrar).get("/api/v1/users/lecturers/?page_size=100").data["results"]
    assert {registrar.pk, results_officer.pk} <= {u["id"] for u in lecturers}

    sheet = submitted_by(world, fresh(results_officer))
    assert sheet.status == SheetStatus.SUBMITTED
    # Their dashboard shows the office overview plus the course they teach.
    dashboard = client_for(results_officer).get("/api/v1/dashboard/").data
    assert dashboard["kind"] == "results_officer" and dashboard["teaching"][0]["course_code"] == "CS101"


def test_appointments_are_validated_and_admin_only(world, admin_user, registrar):
    tutor = make_user(Role.LECTURER)
    admin = client_for(admin_user)
    body = {"user": tutor.pk, "office": "REGISTRAR", "starts_on": TODAY.isoformat()}
    assert client_for(registrar).post("/api/v1/appointments/", body, format="json").status_code == 403
    created = admin.post("/api/v1/appointments/", body, format="json")
    assert created.status_code == 201 and created.data["state"] == "current"
    assert created.data["appointed_by"] == admin_user.pk
    assert admin.post("/api/v1/appointments/", body, format="json").status_code == 409  # overlaps
    assert admin.post("/api/v1/appointments/", {**body, "user": registrar.pk}, format="json").status_code == 400
    student = world.student.user
    assert admin.post("/api/v1/appointments/", {**body, "user": student.pk}, format="json").status_code == 400
    bad_dates = {**body, "office": "RESULTS_OFFICER", "ends_on": (TODAY - datetime.timedelta(days=1)).isoformat()}
    assert admin.post("/api/v1/appointments/", bad_dates, format="json").status_code == 400
    assert admin.post(f"/api/v1/appointments/{created.data['id']}/end/", {"reason": ""},
                      format="json").status_code == 400
    listed = admin.get(f"/api/v1/appointments/?user={tutor.pk}&current=true").data
    assert listed["count"] == 1
    staff = admin.get(f"/api/v1/users/{tutor.pk}/").data
    assert staff["offices"][0]["office"] == "REGISTRAR"


def test_audit_log_filters_by_capacity(world, admin_user):
    tutor = make_user(Role.LECTURER)
    appoint(tutor, Office.RESULTS_OFFICER, admin_user, acting=True)
    sheet = submitted_by(world, world.lecturer)
    client_for(tutor).post(f"/api/v1/result-sheets/{sheet.pk}/approve/", {}, format="json")
    rows = client_for(admin_user).get("/api/v1/audit-logs/?actor_role=RESULTS_OFFICER").data["results"]
    assert rows and all("RESULTS_OFFICER" in r["actor_roles"] for r in rows)
    assert rows[0]["actor_capacity"] == "Lecturer, Acting Results Officer"
    # As acting results officer the tutor reviews the results audit trail.
    assert client_for(tutor).get("/api/v1/audit-logs/").status_code == 200


def test_long_open_appointments_are_flagged_for_review(world, admin_user):
    tutor = make_user(Role.LECTURER)
    appoint(tutor, Office.REGISTRAR, admin_user, starts_on=TODAY - datetime.timedelta(days=400))
    review = client_for(admin_user).get("/api/v1/dashboard/").data["appointments_review"]
    assert review[0]["reason"] == "open_ended" and review[0]["user"] == tutor.pk
