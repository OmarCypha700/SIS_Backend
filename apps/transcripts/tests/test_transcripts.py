import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.institutions.models import InstitutionProfile
from apps.progression.tests.test_regulations import grade_offering, register, staff  # noqa: F401 - fixture
from apps.results import services as results
from apps.transcripts.models import IssuedTranscript
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def graded(world, staff):
    """world.student with one published result (CS101) and one unpublished (MATH101)."""
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    grade_offering(world, staff, world.offerings["CS101"], {world.student: (30, 70)})
    # MATH101 is submitted but not yet approved or published.
    offering = world.offerings["MATH101"]
    sheet = results.get_or_create_sheet(offering)
    results.replace_components(sheet, [{"name": "Exam", "weight": 100, "max_score": 100}], actor=world.lecturer)
    reg = world.student.course_registrations.get(offering=offering)
    results.save_marks(sheet, [{"registration": reg.id, "component": offering.assessment_components.get().id,
                                "score": 64}], actor=world.lecturer)
    results.submit_sheet(sheet, actor=world.lecturer)
    return world


def issue(client, student, **extra):
    return client.post("/api/v1/transcripts/", {"student": student.pk, "purpose": "Graduate application", **extra},
                       format="json")


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "navy").save(buffer, "PNG")
    return SimpleUploadedFile("sig.png", buffer.getvalue(), content_type="image/png")


def test_admin_issues_prints_and_revokes(graded, admin_user):
    api = client_for(admin_user)
    preview = api.get("/api/v1/transcripts/preview/", {"student": graded.student.pk}).data
    assert preview["user_can_issue"] and preview["is_interim"] and preview["unpublished_results"] == 1
    assert preview["digital_signing_available"] is False

    response = issue(api, graded.student)
    assert response.status_code == 201
    data = response.data
    assert data["serial"].startswith("TC/TR/") and data["serial"].endswith("/000001") and data["is_interim"]
    transcript = IssuedTranscript.objects.get()
    courses = [c["course_code"] for s in transcript.snapshot["semesters"] for c in s["courses"]]
    assert courses == ["CS101"]  # unpublished MATH101 is never printed

    first = api.get(f"/api/v1/transcripts/{transcript.pk}/pdf/")
    assert first.status_code == 200 and first["Content-Type"] == "application/pdf" and first.content[:4] == b"%PDF"
    api.get(f"/api/v1/transcripts/{transcript.pk}/pdf/")
    transcript.refresh_from_db()
    assert transcript.print_count == 2

    assert api.post(f"/api/v1/transcripts/{transcript.pk}/revoke/", {}, format="json").status_code == 400
    revoked = api.post(f"/api/v1/transcripts/{transcript.pk}/revoke/", {"reason": "Result amended"}, format="json")
    assert revoked.data["status"] == "REVOKED"
    assert api.get(f"/api/v1/transcripts/{transcript.pk}/pdf/").status_code == 409  # cannot print a revoked one
    assert set(AuditLog.objects.filter(action__startswith="transcript.").values_list("action", flat=True)) == {
        "transcript.issued", "transcript.printed", "transcript.revoked"}


def test_serials_increase_and_codes_are_unique(graded, admin_user):
    api = client_for(admin_user)
    serials = [issue(api, graded.student).data["serial"] for _ in range(3)]
    assert [s.rsplit("/", 1)[1] for s in serials] == ["000001", "000002", "000003"]
    assert len(set(IssuedTranscript.objects.values_list("verification_code", flat=True))) == 3


def test_registrar_needs_permission_from_an_administrator(graded, admin_user):
    registrar = make_user(Role.REGISTRAR)
    assert registrar.can_issue_transcripts is False
    api = client_for(registrar)
    assert api.get("/api/v1/transcripts/").status_code == 200  # may view the register
    assert api.get("/api/v1/transcripts/preview/", {"student": graded.student.pk}).data["user_can_issue"] is False
    assert issue(api, graded.student).status_code == 403

    admin = client_for(admin_user)
    assert admin.patch(f"/api/v1/users/{registrar.pk}/", {"can_issue_transcripts": True}, format="json").status_code == 200
    assert issue(client_for(registrar), graded.student).status_code == 201
    admin.patch(f"/api/v1/users/{registrar.pk}/", {"can_issue_transcripts": False}, format="json")
    assert issue(client_for(registrar), graded.student).status_code == 403
    # Transcripts already issued stay valid after the permission is withdrawn.
    assert IssuedTranscript.objects.get().status == "VALID"


def test_only_registry_roles_can_hold_the_permission(admin_user, lecturer):
    response = client_for(admin_user).patch(f"/api/v1/users/{lecturer.pk}/", {"can_issue_transcripts": True},
                                            format="json")
    assert response.status_code == 400 and "can_issue_transcripts" in response.data["errors"]
    assert make_user(Role.ADMIN).can_issue_transcripts is True
    for role in (Role.LECTURER, Role.RESULTS_OFFICER, Role.STUDENT):
        user = make_user(role)
        assert client_for(user).get("/api/v1/transcripts/").status_code == 403


def test_public_verification(graded, admin_user, api):
    transcript_id = issue(client_for(admin_user), graded.student).data["id"]
    transcript = IssuedTranscript.objects.get(pk=transcript_id)
    code = transcript.display_code.lower()  # codes are accepted in any case, with or without the dash
    data = api.get(f"/api/v1/transcripts/verify/{code}/").data
    assert data["status"] == "VALID" and data["serial"] == transcript.serial
    assert data["student_name"] == graded.student.full_name and data["is_interim"] is True
    assert "semesters" not in data and "courses" not in str(data)  # no individual grades
    assert api.get("/api/v1/transcripts/verify/AAAAA-BBBBB/").status_code == 404

    client_for(admin_user).post(f"/api/v1/transcripts/{transcript_id}/revoke/", {"reason": "Superseded"}, format="json")
    data = api.get(f"/api/v1/transcripts/verify/{transcript.verification_code}/").data
    assert data["status"] == "REVOKED" and data["revoked_on"] and "reason" not in str(data).lower()


def test_cannot_issue_without_results_purpose_or_signature(world, graded, admin_user):
    api = client_for(admin_user)
    assert issue(api, world.other_student).status_code == 400  # nothing published
    assert api.post("/api/v1/transcripts/", {"student": graded.student.pk, "purpose": " "},
                    format="json").status_code == 400
    response = issue(api, graded.student, signing="DIGITAL")
    assert response.status_code == 400 and "Upload the signature" in response.data["detail"]


def test_signature_and_seal_are_private_and_printed(graded, admin_user, registrar, api):
    admin = client_for(admin_user)
    for kind in ("signature", "seal"):
        assert admin.put(f"/api/v1/institution/transcript-images/{kind}/", {"file": png()},
                         format="multipart").status_code == 204
    profile = InstitutionProfile.load()
    assert profile.transcript_signature and profile.transcript_seal
    settings_data = admin.get("/api/v1/institution/settings/").data
    assert settings_data["has_transcript_signature"] and "transcript_signature" not in settings_data
    image = admin.get("/api/v1/institution/transcript-images/seal/")
    assert image.status_code == 200 and image["Cache-Control"] == "private, no-store"
    image.close()  # release the file handle (Windows cannot delete open files)
    assert client_for(registrar).get("/api/v1/institution/transcript-images/seal/").status_code == 403
    assert api.get("/api/v1/institution/transcript-images/seal/").status_code in (401, 403)
    assert "/media/" not in profile.transcript_signature.path.replace("\\", "/").split("private")[0][-8:]

    created = issue(admin, graded.student, signing="DIGITAL")
    assert created.status_code == 201
    assert admin.get(f"/api/v1/transcripts/{created.data['id']}/pdf/").status_code == 200
    assert admin.delete("/api/v1/institution/transcript-images/seal/").status_code == 204
    assert not InstitutionProfile.load().transcript_seal


def test_reprints_stay_identical_and_changes_are_flagged(graded, staff, admin_user):
    api = client_for(admin_user)
    transcript_id = issue(api, graded.student).data["id"]
    assert api.get(f"/api/v1/transcripts/{transcript_id}/record-changed/").data == {"changed": False}
    sheet = results.get_or_create_sheet(graded.offerings["MATH101"])
    results.approve_sheet(sheet, actor=staff.officer)
    results.publish_sheet(sheet, actor=staff.registrar)  # a new result is published after issue
    assert api.get(f"/api/v1/transcripts/{transcript_id}/record-changed/").data == {"changed": True}
    snapshot = IssuedTranscript.objects.get(pk=transcript_id).snapshot
    assert len(snapshot["semesters"][0]["courses"]) == 1  # the issued copy is unchanged


def test_register_search_and_export(graded, admin_user):
    api = client_for(admin_user)
    serial = issue(api, graded.student).data["serial"]
    assert api.get("/api/v1/transcripts/", {"search": graded.student.student_number}).data["count"] == 1
    assert api.get("/api/v1/transcripts/", {"search": serial}).data["count"] == 1
    assert api.get("/api/v1/transcripts/", {"status": "REVOKED"}).data["count"] == 0
    export = api.get("/api/v1/transcripts/export/", {"file_format": "csv"})
    assert export.status_code == 200 and serial in export.content.decode()


def test_serial_format_is_validated(admin_user):
    api = client_for(admin_user)
    bad = api.patch("/api/v1/institution/settings/", {"transcript_serial_format": "TR-{YEAR}"}, format="json")
    assert bad.status_code == 400
    good = api.patch("/api/v1/institution/settings/", {"transcript_serial_format": "{SHORT}-{YEAR}-{SEQ:05}"},
                     format="json")
    assert good.status_code == 200
