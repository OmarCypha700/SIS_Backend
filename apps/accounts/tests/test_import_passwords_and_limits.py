"""Temporary passwords on import, the forced first change, and the limits added after the security audit."""
import datetime
import io

import pytest
from django.core import mail
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.notifications import delivery
from apps.notifications.models import Category, OutgoingMessage, PushSubscription
from common.credentials import generate_temporary_password, password_errors
from common.tabular import read_table, table_response
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


def csv_file(text, name="rows.csv"):
    upload = io.BytesIO(text.encode("utf-8"))
    upload.name = name
    upload.size = len(upload.getvalue())
    return upload


def test_generated_passwords_are_readable_and_pass_the_rules():
    passwords = {generate_temporary_password() for _ in range(50)}
    assert len(passwords) == 50
    for password in passwords:
        assert not set(password) & set("0O1lI")
        assert password_errors(password, email="a@example.test") == []


def test_student_import_generates_one_time_passwords(world, registrar, api):
    rows = [{"first_name": "Ama", "last_name": "Boateng", "email": "ama@example.test", "programme_code": "BSC-CS",
             "level": "Level 100", "admission_year": "2025"}]
    response = client_for(registrar).post("/api/v1/students/import/confirm/",
                                          {"rows": rows, "password_mode": "generate"}, format="json")
    assert response.status_code == 201, response.data
    [slip] = response.data["credentials"]
    assert slip["email"] == "ama@example.test" and slip["identifier"]
    user = User.objects.get(email="ama@example.test")
    assert user.must_change_password and user.check_password(slip["password"])
    assert not mail.outbox
    logged = str(list(AuditLog.objects.values())) + str(list(OutgoingMessage.objects.values()))
    assert slip["password"] not in logged


def test_student_import_without_option_keeps_old_behaviour(world, registrar):
    rows = [{"first_name": "Kofi", "last_name": "Ansah", "email": "kofi@example.test", "programme_code": "BSC-CS",
             "level": "Level 100", "admission_year": "2025"}]
    response = client_for(registrar).post("/api/v1/students/import/confirm/", {"rows": rows}, format="json")
    assert response.status_code == 201 and response.data["credentials"] == []
    assert not User.objects.get(email="kofi@example.test").has_usable_password()


def test_password_column_is_checked_in_preview_and_required_in_column_mode(world, registrar):
    client = client_for(registrar)
    text = ("first_name,last_name,email,programme_code,level,admission_year,password\n"
            "Esi,Owusu,esi@example.test,BSC-CS,Level 100,2025,short\n"
            "Yaw,Darko,yaw@example.test,BSC-CS,Level 100,2025,Gold-Lantern-77\n"
            "Abena,Kyei,abena@example.test,BSC-CS,Level 100,2025,\n")
    preview = client.post("/api/v1/students/import/preview/", {"file": csv_file(text)}, format="multipart")
    assert preview.status_code == 200
    valid = [r["valid"] for r in preview.data["rows"]]
    assert valid == [False, True, True]
    assert "password" in preview.data["rows"][0]["errors"]

    rows = [r["input"] for r in preview.data["rows"][1:]]
    missing = client.post("/api/v1/students/import/confirm/", {"rows": rows, "password_mode": "column"},
                          format="json")
    assert missing.status_code == 400 and missing.data["code"] == "import_invalid"
    done = client.post("/api/v1/students/import/confirm/", {"rows": rows[:1], "password_mode": "column"},
                       format="json")
    assert done.status_code == 201 and done.data["credentials"][0]["password"] == "Gold-Lantern-77"


def test_staff_import_with_generated_passwords_sends_no_email(admin_user, django_capture_on_commit_callbacks):
    rows = [{"email": f"t{n}@example.test", "first_name": "Tutor", "last_name": f"N{n}", "role": "LECTURER"}
            for n in range(2)]
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(admin_user).post("/api/v1/users/import/confirm/",
                                               {"rows": rows, "password_mode": "generate"}, format="json")
    assert response.status_code == 201, response.data
    assert len(response.data["credentials"]) == 2 and response.data["emails_sent"] is False
    assert not mail.outbox and not OutgoingMessage.objects.exists()
    assert all(u.must_change_password for u in User.objects.filter(email__startswith="t"))


def test_unknown_password_mode_is_refused(admin_user):
    rows = [{"email": "x@example.test", "first_name": "X", "last_name": "Y", "role": "LECTURER"}]
    response = client_for(admin_user).post("/api/v1/users/import/confirm/",
                                           {"rows": rows, "password_mode": "sms"}, format="json")
    assert response.status_code == 400


def test_temporary_password_must_be_changed_before_using_the_api(api):
    user = make_user(Role.LECTURER)
    user.set_password("Temp-Pass-2345")
    user.must_change_password = True
    user.save()
    client = client_for(user)
    blocked = client.get("/api/v1/notifications/unread-count/")
    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
    assert client.get("/api/v1/auth/me/").status_code == 200
    changed = client.post("/api/v1/auth/password/change/",
                          {"current_password": "Temp-Pass-2345", "new_password": "Brand-New-Secret-91"}, format="json")
    assert changed.status_code == 204, changed.content
    assert client.get("/api/v1/notifications/unread-count/").status_code == 200


def test_reset_requests_do_not_drain_the_email_allowance(api, django_capture_on_commit_callbacks):
    user = make_user(Role.LECTURER)
    for _ in range(3):
        with django_capture_on_commit_callbacks(execute=True):
            response = api.post("/api/v1/auth/password/reset/", {"email": user.email}, format="json")
        assert response.status_code == 200
    assert len(mail.outbox) == 1  # repeats within 15 minutes are absorbed

    resets = OutgoingMessage.objects.filter(category=Category.SECURITY)
    for _ in range(3):
        resets.update(created_at=timezone.now() - datetime.timedelta(minutes=20), status="SENT")
        from apps.notifications.services import request_password_reset_email
        request_password_reset_email(user)
    assert resets.count() == 3  # daily cap per person


def test_old_queued_reset_links_expire():
    user = make_user(Role.LECTURER)
    OutgoingMessage.objects.create(channel="EMAIL", category=Category.SECURITY, recipient=user, to=user.email,
                                   payload={"template": "password_reset"})
    OutgoingMessage.objects.update(created_at=timezone.now() - datetime.timedelta(hours=25))
    assert delivery.expire_resets() == 1


def test_reset_requests_are_limited_per_email_whatever_the_address(api, settings):
    user = make_user(Role.LECTURER)
    codes = [api.post("/api/v1/auth/password/reset/", {"email": user.email}, format="json",
                      HTTP_X_FORWARDED_FOR=f"10.0.0.{n}").status_code for n in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


@pytest.mark.parametrize("endpoint,ok", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://web.push.apple.com/abc", True),
    ("https://169.254.169.254/latest/meta-data", False),
    ("https://evil.example.com/push", False),
])
def test_push_subscriptions_only_for_browser_push_services(settings, endpoint, ok):
    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = "pub", "priv"
    settings.PUSH_ALLOWED_HOSTS = ["fcm.googleapis.com", ".push.apple.com"]
    user = make_user(Role.STUDENT)
    response = client_for(user).post("/api/v1/notifications/push/subscribe/",
                                     {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}, format="json")
    assert (response.status_code == 201) is ok


def test_devices_per_person_are_capped(settings):
    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = "pub", "priv"
    settings.PUSH_MAX_DEVICES_PER_USER = 3
    user = make_user(Role.STUDENT)
    client = client_for(user)
    for n in range(5):
        client.post("/api/v1/notifications/push/subscribe/",
                    {"endpoint": f"https://fcm.googleapis.com/fcm/send/{n}", "keys": {"p256dh": "k", "auth": "a"}},
                    format="json")
    assert PushSubscription.objects.filter(user=user).count() == 3


def test_spreadsheet_with_a_huge_declared_size_stops_early():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["first_name"])
    sheet.cell(row=1_000_000, column=1, value="far away")
    buffer = io.BytesIO()
    workbook.save(buffer)
    upload = io.BytesIO(buffer.getvalue())
    upload.name, upload.size = "big.xlsx", len(buffer.getvalue())
    with pytest.raises(ValueError, match="at most 10 rows"):
        read_table(upload, max_bytes=10_000_000, max_rows=10)


def test_csv_blank_lines_count_towards_the_limit():
    upload = csv_file("first_name\n" + "\n" * 100 + "A\n")
    with pytest.raises(ValueError):
        read_table(upload, max_bytes=1_000_000, max_rows=10)
    assert read_table(csv_file("first_name\n\nA\n"), max_bytes=1000, max_rows=10) == [{"first_name": "A"}]


def test_export_headers_are_escaped():
    response = table_response("x", ["=cmd()", "Name"], [["a", "b"]])
    assert response.content.decode("utf-8-sig").splitlines()[0] == "'=cmd(),Name"


def test_reset_email_is_sent_after_the_response(api):
    user = make_user(Role.LECTURER)
    from apps.notifications import services as notifications
    queued = notifications.request_password_reset_email(user)
    response = notifications.send_after_response(HttpResponse("ok"), queued)
    assert not mail.outbox
    response.close()  # the server closes the response after sending it
    assert len(mail.outbox) == 1 and mail.outbox[0].subject == "Reset your password"


def test_api_docs_are_for_administrators():
    assert client_for(make_user(Role.STUDENT)).get("/api/v1/docs/").status_code == 403
    assert client_for(make_user(Role.ADMIN)).get("/api/v1/schema/").status_code == 200


def test_huge_branding_images_are_refused(admin_user):
    from PIL import Image as PILImage
    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile

    from common.validators import validate_branding_image

    buffer = io.BytesIO()
    PILImage.new("1", (5000, 5000)).save(buffer, format="PNG")  # tiny file, 25 megapixels
    upload = SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")
    with pytest.raises(ValidationError, match="megapixels"):
        validate_branding_image(upload)


def test_app_icon_is_drawn_once_per_branding(api, monkeypatch):
    from apps.institutions import app_icon

    calls = []
    real = app_icon.render
    monkeypatch.setattr(app_icon, "render", lambda *a: calls.append(a) or real(*a))
    for _ in range(3):
        assert api.get("/api/v1/institution/app-icon/?size=192&purpose=any&x=1").status_code == 200
    assert len(calls) == 1
