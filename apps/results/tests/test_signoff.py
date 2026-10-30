"""Who can sign off results when the people who approve or publish also teach."""
import pytest

from apps.accounts.models import Role
from apps.institutions.models import InstitutionProfile
from apps.notifications.models import Kind, Notification
from apps.progression.tests.test_regulations import register
from apps.results import services as results
from apps.results import signoff
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


def submitted_by(world, teacher):
    """CS101 marked and submitted by `teacher` (who is assigned to teach it)."""
    offering = world.offerings["CS101"]
    offering.assignments.update_or_create(lecturer=teacher)
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    sheet = results.get_or_create_sheet(offering)
    results.replace_components(sheet, [{"name": "Exam", "weight": 100, "max_score": 100}], actor=teacher)
    reg = world.student.course_registrations.get(offering=offering)
    results.save_marks(sheet, [{"registration": reg.id, "component": offering.assessment_components.get().id,
                                "score": 70}], actor=teacher)
    return results.submit_sheet(sheet, actor=teacher)


def tutor_who_is_also_results_officer():
    # A tutor given the results officer's approval permission directly (an appointment works the same way).
    return make_user(Role.LECTURER, can_approve_results=True)


def test_warns_and_alerts_admins_when_only_the_submitter_can_approve(world, admin_user):
    tutor = tutor_who_is_also_results_officer()
    make_user(Role.REGISTRAR)  # can publish, cannot approve
    sheet = submitted_by(world, tutor)

    warnings = signoff.gaps(sheet)
    assert [w["step"] for w in warnings] == ["approve"]
    assert "No one other than" in warnings[0]["message"] and "Can approve results" in warnings[0]["message"]
    alert = Notification.objects.get(recipient=admin_user, kind=Kind.SIGNOFF_BLOCKED)
    assert alert.link == f"/results/sheets/{sheet.pk}"

    detail = client_for(tutor).get(f"/api/v1/result-sheets/{sheet.pk}/").data
    assert detail["signoff_warnings"] == warnings and detail["permissions"]["can_approve"] is False
    dashboard = client_for(admin_user).get("/api/v1/dashboard/").data
    assert [b["course_code"] for b in dashboard["signoff_blocked"]] == ["CS101"]

    # The administrator gives a second person the permission: the warning clears.
    vice_principal = make_user(Role.LECTURER)
    client_for(admin_user).patch(f"/api/v1/users/{vice_principal.pk}/", {"can_approve_results": True}, format="json")
    assert signoff.gaps(sheet) == []
    vice_principal.refresh_from_db()
    results.approve_sheet(sheet, actor=vice_principal)


def test_no_warning_when_someone_else_can_sign_off(world, admin_user):
    make_user(Role.RESULTS_OFFICER)
    make_user(Role.REGISTRAR)
    sheet = submitted_by(world, world.lecturer)
    assert signoff.gaps(sheet) == []
    assert not Notification.objects.filter(kind=Kind.SIGNOFF_BLOCKED).exists()


def test_registrar_may_publish_own_course_unless_the_rule_is_on(world):
    officer = make_user(Role.RESULTS_OFFICER)
    registrar_tutor = make_user(Role.LECTURER, can_publish_results=True)
    sheet = submitted_by(world, registrar_tutor)
    results.approve_sheet(sheet, actor=officer)
    profile = InstitutionProfile.load()
    profile.publisher_must_differ_from_submitter = True
    profile.save()
    sheet.refresh_from_db()
    # Administrators can also publish by default, so with the rule on someone else is still available.
    assert signoff.gaps(sheet) == [] or signoff.gaps(sheet)[0]["step"] == "publish"
    detail = client_for(registrar_tutor).get(f"/api/v1/result-sheets/{sheet.pk}/").data
    assert detail["permissions"]["can_publish"] is False
    response = client_for(registrar_tutor).post(f"/api/v1/result-sheets/{sheet.pk}/publish/", {}, format="json")
    assert response.status_code == 403 and "someone other than" in response.data["detail"]

    profile.publisher_must_differ_from_submitter = False
    profile.save()
    assert client_for(registrar_tutor).post(f"/api/v1/result-sheets/{sheet.pk}/publish/", {},
                                            format="json").status_code == 200


def test_publish_gap_is_reported_when_the_submitter_is_the_only_publisher(world, admin_user):
    officer = make_user(Role.RESULTS_OFFICER)
    registrar_tutor = make_user(Role.LECTURER, can_publish_results=True)
    admin_user.can_publish_results = False
    admin_user.save()
    profile = InstitutionProfile.load()
    profile.publisher_must_differ_from_submitter = True
    profile.save()
    sheet = submitted_by(world, registrar_tutor)
    results.approve_sheet(sheet, actor=officer)
    sheet.refresh_from_db()
    assert [w["step"] for w in signoff.gaps(sheet)] == ["publish"]
    assert Notification.objects.filter(recipient=admin_user, kind=Kind.SIGNOFF_BLOCKED).exists()


def test_setting_is_editable(admin_user):
    response = client_for(admin_user).patch("/api/v1/institution/settings/",
                                            {"publisher_must_differ_from_submitter": True}, format="json")
    assert response.status_code == 200 and response.data["publisher_must_differ_from_submitter"] is True
