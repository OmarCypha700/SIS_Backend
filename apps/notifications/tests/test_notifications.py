import datetime

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.notifications import delivery, services
from apps.notifications.models import (
    Category, Channel, Kind, MessageStatus, Notification, OutgoingMessage, PushSubscription,
)
from apps.progression.tests.test_regulations import register, staff  # noqa: F401 - fixture
from apps.results import services as results
from conftest import client_for, make_student, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def pushes(settings, monkeypatch):
    """Turns push on and records what would have been sent."""
    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = "test-public", "test-private"
    sent = []

    def fake_webpush(subscription_info, data, **kwargs):
        sent.append({"endpoint": subscription_info["endpoint"], "data": data})

    monkeypatch.setattr("pywebpush.webpush", fake_webpush)
    return sent


def subscribe(user, n=1):
    return PushSubscription.objects.create(user=user, endpoint=f"https://push.example.test/{user.pk}/{n}",
                                           p256dh="key", auth="auth")


def titles(user):
    return list(Notification.objects.filter(recipient=user).values_list("title", flat=True))


def test_result_workflow_notifies_the_right_people(world, staff, pushes, django_capture_on_commit_callbacks):
    offering = world.offerings["CS101"]
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    subscribe(world.student.user)
    sheet = results.get_or_create_sheet(offering)
    results.replace_components(sheet, [{"name": "Exam", "weight": 100, "max_score": 100}], actor=world.lecturer)
    component = offering.assessment_components.get()
    reg = world.student.course_registrations.get(offering=offering)
    results.save_marks(sheet, [{"registration": reg.id, "component": component.id, "score": 70}], actor=world.lecturer)

    with django_capture_on_commit_callbacks(execute=True):
        results.submit_sheet(sheet, actor=world.lecturer)
    assert titles(staff.officer) == ["Results submitted for CS101"]
    assert titles(world.lecturer) == []  # the actor is never notified
    assert [m.to[0] for m in mail.outbox] == [staff.officer.email]
    assert "70" not in mail.outbox[0].body  # no marks in email
    assert f"/results/sheets/{sheet.pk}" in mail.outbox[0].body

    with django_capture_on_commit_callbacks(execute=True):
        results.approve_sheet(sheet, actor=staff.officer)
    assert titles(staff.registrar) == ["Results ready to publish: CS101"]
    assert titles(world.lecturer) == ["Results approved for CS101"]

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        results.publish_sheet(sheet, actor=staff.registrar)
    assert titles(world.student.user) == ["New result: CS101"]
    assert titles(world.other_student.user) == []  # not registered for the course
    assert world.student.email not in [m.to[0] for m in mail.outbox]  # students: in-app and push only
    assert len(pushes) == 1 and '"unread": 1' in pushes[0]["data"] and "70" not in pushes[0]["data"]


def test_returned_results_tell_the_lecturer_why_to_look(world, staff, django_capture_on_commit_callbacks):
    offering = world.offerings["CS101"]
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    sheet = results.get_or_create_sheet(offering)
    results.replace_components(sheet, [{"name": "Exam", "weight": 100, "max_score": 100}], actor=world.lecturer)
    reg = world.student.course_registrations.get(offering=offering)
    results.save_marks(sheet, [{"registration": reg.id, "component": offering.assessment_components.get().id,
                                "score": 50}], actor=world.lecturer)
    results.submit_sheet(sheet, actor=world.lecturer)
    results.return_sheet(sheet, actor=staff.officer, comment="Check the exam scores")
    notification = Notification.objects.get(recipient=world.lecturer, kind=Kind.RESULTS_RETURNED)
    assert notification.link == f"/results/courses/{offering.pk}"


def test_nothing_is_sent_when_the_action_rolls_back(world, django_capture_on_commit_callbacks):
    officer = make_user(Role.RESULTS_OFFICER)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                services.notify([officer], kind=Kind.TEST, title="Never")
                raise RuntimeError("rolled back")
    assert not Notification.objects.exists() and not OutgoingMessage.objects.exists() and not mail.outbox


def test_alerts_stop_at_the_budget_but_password_resets_still_send(settings, api, django_capture_on_commit_callbacks):
    settings.EMAIL_DAILY_LIMIT, settings.EMAIL_SECURITY_RESERVE = 10, 3
    officers = [make_user(Role.RESULTS_OFFICER) for _ in range(9)]
    with django_capture_on_commit_callbacks(execute=True):
        services.notify(officers, kind=Kind.TEST, title="Check this")
    assert len(mail.outbox) == 7  # 10 - 3 reserved
    assert OutgoingMessage.objects.filter(channel=Channel.EMAIL, status=MessageStatus.PENDING).count() == 2

    with django_capture_on_commit_callbacks(execute=True):
        api.post("/api/v1/auth/password/reset/", {"email": officers[0].email}, format="json")
    assert len(mail.outbox) == 8 and mail.outbox[-1].subject == "Reset your password"
    reset = OutgoingMessage.objects.get(category=Category.SECURITY)
    assert reset.body == "" and "token" not in str(reset.payload)  # the link is never stored

    # Next day: the allowance resets and the daily task sends what waited.
    OutgoingMessage.objects.filter(status=MessageStatus.SENT).update(sent_at=timezone.now() - datetime.timedelta(days=1))
    call_command("send_pending_messages")
    assert len(mail.outbox) == 10
    assert not OutgoingMessage.objects.filter(status=MessageStatus.PENDING).exists()


def test_backlog_becomes_one_digest_per_person(settings):
    settings.EMAIL_DAILY_LIMIT, settings.EMAIL_SECURITY_RESERVE = 0, 0  # nothing can be sent today
    officer = make_user(Role.RESULTS_OFFICER)
    for n in range(3):
        services.notify([officer], kind=Kind.TEST, title=f"Item {n}", link=f"/results/sheets/{n}")
    assert services.build_digests() == 1
    digest = OutgoingMessage.objects.get(status=MessageStatus.PENDING, channel=Channel.EMAIL)
    assert "3 updates" in digest.subject and "Item 2" in digest.body and "/results/sheets/1" in digest.body
    assert OutgoingMessage.objects.filter(status=MessageStatus.SKIPPED).count() == 3


def test_opting_out_stops_alerts_but_not_security_email(api, django_capture_on_commit_callbacks):
    officer = make_user(Role.RESULTS_OFFICER, email_alerts=False)
    with django_capture_on_commit_callbacks(execute=True):
        services.notify([officer], kind=Kind.TEST, title="Check this")
        api.post("/api/v1/auth/password/reset/", {"email": officer.email}, format="json")
    assert [m.subject for m in mail.outbox] == ["Reset your password"]
    assert titles(officer) == ["Check this"]  # still in the app


def test_mail_failure_never_breaks_the_action(monkeypatch, django_capture_on_commit_callbacks):
    def broken(self, fail_silently=False):
        raise ConnectionError("Brevo is down")

    monkeypatch.setattr("django.core.mail.EmailMessage.send", broken)
    officer = make_user(Role.RESULTS_OFFICER)
    with django_capture_on_commit_callbacks(execute=True):
        services.notify([officer], kind=Kind.TEST, title="Check this")
    message = OutgoingMessage.objects.get(channel=Channel.EMAIL)
    assert message.status == MessageStatus.PENDING and message.attempts == 1 and "Brevo is down" in message.last_error
    for _ in range(delivery.MAX_ATTEMPTS - 1):
        delivery.drain()
    message.refresh_from_db()
    assert message.status == MessageStatus.FAILED


def test_expired_push_subscription_is_removed(world, settings, monkeypatch, django_capture_on_commit_callbacks):
    from pywebpush import WebPushException

    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = "test-public", "test-private"

    class Gone:
        status_code = 410

    def gone(*args, **kwargs):
        raise WebPushException("gone", response=Gone())

    monkeypatch.setattr("pywebpush.webpush", gone)
    subscribe(world.student.user)
    with django_capture_on_commit_callbacks(execute=True):
        services.notify([world.student.user], kind=Kind.TEST, title="Hello")
    assert not PushSubscription.objects.exists()
    assert OutgoingMessage.objects.get(channel=Channel.PUSH).status == MessageStatus.SKIPPED


def test_large_fan_out_is_sent_in_batches(world, pushes, django_capture_on_commit_callbacks):
    students = [make_student(world.programme, world.l100) for _ in range(40)]
    for s in students:
        subscribe(s.user)
    with django_capture_on_commit_callbacks(execute=True):
        services.notify([s.user for s in students], kind=Kind.TEST, title="Hello")
    assert len(pushes) == delivery.IMMEDIATE_LIMIT

    response = client_for(students[0].user).get("/api/v1/notifications/unread-count/")
    assert response.data == {"unread": 1}
    assert len(pushes) == delivery.IMMEDIATE_LIMIT + delivery.POLL_LIMIT
    client_for(students[1].user).get("/api/v1/notifications/unread-count/")  # rate-limited: no extra drain
    assert len(pushes) == delivery.IMMEDIATE_LIMIT + delivery.POLL_LIMIT

    call_command("send_pending_messages")
    assert len(pushes) == 40


def test_users_only_see_and_change_their_own_notifications(world):
    mine, theirs = world.student.user, world.other_student.user
    services.notify([mine, theirs], kind=Kind.TEST, title="Hello")
    other_id = Notification.objects.get(recipient=theirs).pk
    client = client_for(mine)
    listing = client.get("/api/v1/notifications/")
    assert [n["title"] for n in listing.data["results"]] == ["Hello"] and listing.data["count"] == 1
    assert client.post(f"/api/v1/notifications/{other_id}/read/").status_code == 404
    own_id = listing.data["results"][0]["id"]
    assert client.post(f"/api/v1/notifications/{own_id}/read/").data["is_read"] is True
    assert client.get("/api/v1/notifications/", {"unread": 1}).data["count"] == 0
    assert Notification.objects.get(pk=other_id).read_at is None
    services.notify([mine], kind=Kind.TEST, title="Again")
    assert client.post("/api/v1/notifications/read-all/").data["updated"] == 1


def test_push_subscription_endpoints(world, pushes):
    student, other = world.student.user, world.other_student.user
    body = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert client_for(student).get("/api/v1/notifications/push/public-key/").data == {
        "enabled": True, "public_key": "test-public"}
    assert client_for(student).post("/api/v1/notifications/push/subscribe/", body, format="json").status_code == 201
    # A shared device that changes hands belongs to whoever subscribed last.
    client_for(other).post("/api/v1/notifications/push/subscribe/", body, format="json")
    assert PushSubscription.objects.get().user == other
    client_for(student).post("/api/v1/notifications/push/unsubscribe/", {"endpoint": body["endpoint"]}, format="json")
    assert PushSubscription.objects.exists()  # not theirs to remove
    insecure = {**body, "endpoint": "http://push.example.test/x"}
    assert client_for(student).post("/api/v1/notifications/push/subscribe/", insecure, format="json").status_code == 400
    assert client_for(other).post("/api/v1/notifications/push/test/").data == {"devices": 1}


def test_push_is_off_without_keys(world, settings):
    settings.VAPID_PUBLIC_KEY = settings.VAPID_PRIVATE_KEY = ""
    client = client_for(world.student.user)
    assert client.get("/api/v1/notifications/push/public-key/").data["enabled"] is False
    body = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert client.post("/api/v1/notifications/push/subscribe/", body, format="json").status_code == 409


def test_delivery_status_is_for_administrators(admin_user, registrar):
    data = client_for(admin_user).get("/api/v1/notifications/delivery-status/").data
    assert data["email"]["limit"] == 300 and data["email"]["sent_today"] == 0
    assert client_for(registrar).get("/api/v1/notifications/delivery-status/").status_code == 403


def test_preferences_are_editable_on_me(lecturer):
    client = client_for(lecturer)
    response = client.patch("/api/v1/auth/me/", {"email_alerts": False, "notification_sound": False}, format="json")
    assert response.data["email_alerts"] is False and response.data["notification_sound"] is False
    assert client.patch("/api/v1/auth/me/", {"role": "ADMIN"}, format="json").data["role"] == Role.LECTURER


def test_staff_import_reports_waiting_setup_emails(settings, admin_user, django_capture_on_commit_callbacks):
    settings.EMAIL_DAILY_LIMIT, settings.EMAIL_SECURITY_RESERVE = 1, 0
    rows = [{"email": f"new{n}@example.test", "first_name": "New", "last_name": f"Staff{n}", "role": "LECTURER"}
            for n in range(3)]
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(admin_user).post("/api/v1/users/import/confirm/", {"rows": rows}, format="json")
    # In production the import commits before the response, so the count includes what was just sent;
    # inside a test transaction the sending happens afterwards.
    assert response.status_code == 201 and "emails_waiting" in response.data
    assert services.pending_security_emails(list(User.objects.filter(email__startswith="new"))) == 2
    assert len(mail.outbox) == 1 and "Choose your password" in mail.outbox[0].body


def test_lecturer_hears_about_a_new_teaching_assignment(world, admin_user):
    offering = world.offerings["CS105"]
    response = client_for(admin_user).put(f"/api/v1/course-offerings/{offering.pk}/lecturers/",
                                          {"lecturers": [{"lecturer": world.lecturer.pk}]}, format="json")
    assert response.status_code == 200
    assert titles(world.lecturer) == ["You have been assigned to CS105"]


@pytest.mark.parametrize("query,status", [("size=192", 200), ("size=512&purpose=maskable", 200),
                                          ("size=96&purpose=badge", 200), ("size=7", 404), ("purpose=x", 404)])
def test_app_icon(api, query, status):
    response = api.get(f"/api/v1/institution/app-icon/?{query}")
    assert response.status_code == status
    if status == 200:
        assert response["Content-Type"] == "image/png" and response.content[:4] == b"\x89PNG"
        again = api.get(f"/api/v1/institution/app-icon/?{query}", HTTP_IF_NONE_MATCH=response["ETag"])
        assert again.status_code == 304
