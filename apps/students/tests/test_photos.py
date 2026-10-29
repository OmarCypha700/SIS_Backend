import io
import os

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.institutions.services import render_format
from apps.progression.tests.test_regulations import register
from apps.students import photos
from conftest import client_for, make_student, make_user

pytestmark = pytest.mark.django_db


def jpeg(name="photo.jpg", size=(900, 1200), colour="steelblue", gps=False):
    buffer = io.BytesIO()
    image = Image.new("RGB", size, colour)
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    if gps:
        exif[0x8825] = {1: "N", 2: (5.0, 33.0, 0.0)}
    image.save(buffer, "JPEG", exif=exif.tobytes())
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def test_registry_uploads_and_photo_is_cleaned(world, registrar):
    api = client_for(registrar)
    response = api.put(f"/api/v1/students/{world.student.pk}/photo/", {"file": jpeg(gps=True)}, format="multipart")
    assert response.status_code == 200
    assert response.data["photo_url"].startswith(f"/api/v1/students/{world.student.pk}/photo/?size=full&v=")
    world.student.refresh_from_db()
    with world.student.photo.open("rb") as handle:
        stored = Image.open(handle)
        assert stored.format == "WEBP" and stored.size == (480, 640) and not stored.getexif()
    with world.student.photo_thumbnail.open("rb") as handle:
        assert Image.open(handle).size == (128, 128)
    # Stored outside the public media folder, so no /media/ address can reach it.
    assert not os.path.abspath(world.student.photo.path).startswith(os.path.abspath(settings.MEDIA_ROOT) + os.sep)

    image = api.get(response.data["thumbnail_url"])
    assert image.status_code == 200 and image["Content-Type"] == "image/webp"
    assert image["Cache-Control"].startswith("private")
    image.close()
    assert AuditLog.objects.filter(action="student.photo_updated").exists()


def test_replacing_changes_the_address_and_removes_old_files(world, registrar, django_capture_on_commit_callbacks):
    api = client_for(registrar)
    first = api.put(f"/api/v1/students/{world.student.pk}/photo/", {"file": jpeg()}, format="multipart").data
    world.student.refresh_from_db()
    old_name = world.student.photo.name
    with django_capture_on_commit_callbacks(execute=True):
        second = api.put(f"/api/v1/students/{world.student.pk}/photo/", {"file": jpeg(colour="tan")},
                         format="multipart").data
    assert first["photo_url"] != second["photo_url"]
    storage = world.student.photo.storage
    assert not storage.exists(old_name)  # removed after the change was saved
    assert api.delete(f"/api/v1/students/{world.student.pk}/photo/").status_code == 204
    world.student.refresh_from_db()
    assert not world.student.photo and api.get(f"/api/v1/students/{world.student.pk}/photo/").status_code == 404


def test_rejects_files_that_are_not_photos(world, registrar):
    api = client_for(registrar)
    fake = SimpleUploadedFile("photo.jpg", b"not an image", content_type="image/jpeg")
    response = api.put(f"/api/v1/students/{world.student.pk}/photo/", {"file": fake}, format="multipart")
    assert response.status_code == 400 and "not a readable image" in response.data["detail"]
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    response = api.put(f"/api/v1/students/{world.student.pk}/photo/",
                       {"file": SimpleUploadedFile("a.gif", gif.getvalue())}, format="multipart")
    assert response.status_code == 400 and "JPG, PNG or WebP" in response.data["detail"]


def test_who_can_see_and_change_photos(world, registrar):
    client_for(registrar).put(f"/api/v1/students/{world.student.pk}/photo/", {"file": jpeg()}, format="multipart")
    url = f"/api/v1/students/{world.student.pk}/photo/?size=thumb"

    def status(user):
        response = client_for(user).get(url)
        response.close()
        return response.status_code

    assert status(world.student.user) == 200                       # own photo
    assert status(world.other_student.user) == 404                 # someone else's
    assert status(make_user(Role.RESULTS_OFFICER)) == 200
    assert status(world.lecturer) == 404                           # not in their classes yet
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    assert status(world.lecturer) == 200                           # now teaches the student
    assert client_for(world.student.user).put(f"/api/v1/students/{world.student.pk}/photo/", {"file": jpeg()},
                                              format="multipart").status_code == 403
    assert client_for(world.lecturer).delete(f"/api/v1/students/{world.student.pk}/photo/").status_code == 403
    from rest_framework.test import APIClient
    assert APIClient().get(url).status_code in (401, 403)

    me = client_for(world.student.user).get("/api/v1/auth/me/").data
    assert me["student"]["thumbnail_url"].startswith(f"/api/v1/students/{world.student.pk}/photo/?size=thumb")


def test_matching_ignores_punctuation_and_case(world):
    a = make_student(world.programme, world.l100, number="NCHT/2026/0007")
    b = make_student(world.programme, world.l100, number="NCHT20260008")
    make_student(world.programme, world.l100, number="AB/12/3")
    make_student(world.programme, world.l100, number="AB/1/23")
    rows = photos.match(["NCHT-2026-0007.jpg", "ncht20260008.PNG", "NCHT_2026_0008 .webp", "NCHT2026007.jpg",
                         "AB123.jpg", "notes.txt", "NCHT20260007 (1).jpg"])
    statuses = [(r["filename"], r["status"]) for r in rows]
    assert statuses == [
        ("NCHT-2026-0007.jpg", "matched"),
        ("ncht20260008.PNG", "duplicate"),           # two files for the same student
        ("NCHT_2026_0008 .webp", "duplicate"),
        ("NCHT2026007.jpg", "unmatched"),           # a dropped leading zero is a different number
        ("AB123.jpg", "ambiguous"),                 # AB/12/3 and AB/1/23 look the same without punctuation
        ("notes.txt", "invalid"),
        ("NCHT20260007 (1).jpg", "unmatched"),      # extra text is never guessed away
    ]
    assert rows[0]["student"]["id"] == a.pk and rows[1]["student"]["id"] == b.pk


def test_bulk_upload_skips_existing_unless_replacing(world, registrar):
    a = make_student(world.programme, world.l100, number="NCHT/2026/0101")
    b = make_student(world.programme, world.l100, number="NCHT/2026/0102")
    api = client_for(registrar)
    match = api.post("/api/v1/students/photos/match/", {"filenames": ["NCHT-2026-0101.jpg", "x.jpg"]}, format="json")
    assert [f["status"] for f in match.data["files"]] == ["matched", "unmatched"]

    files = [jpeg("NCHT-2026-0101.jpg"), jpeg("NCHT20260102.jpg"), jpeg("NCHT20269999.jpg")]
    result = api.post("/api/v1/students/photos/upload/", {"files": files}, format="multipart").data["files"]
    assert [r["status"] for r in result] == ["uploaded", "uploaded", "skipped"]
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.photo and b.photo

    again = api.post("/api/v1/students/photos/upload/", {"files": [jpeg("NCHT-2026-0101.jpg")]},
                     format="multipart").data["files"]
    assert again[0]["status"] == "skipped" and "Replace existing photos" in again[0]["message"]
    replaced = api.post("/api/v1/students/photos/upload/", {"files": [jpeg("NCHT-2026-0101.jpg")], "replace": "true"},
                        format="multipart").data["files"]
    assert replaced[0] == {**replaced[0], "status": "uploaded", "message": "Replaced"}
    assert client_for(world.lecturer).post("/api/v1/students/photos/match/", {"filenames": ["a.jpg"]},
                                           format="json").status_code == 403


def test_photos_appear_on_class_lists(world, registrar, admin_user):
    register(world, world.student, world.semester, ["CS101", "MATH101"])
    client_for(registrar).put(f"/api/v1/students/{world.student.pk}/photo/", {"file": jpeg()}, format="multipart")
    offering = world.offerings["CS101"]
    rows = client_for(world.lecturer).get(f"/api/v1/class-lists/{offering.pk}/").data["students"]
    assert rows[0]["thumbnail_url"].startswith(f"/api/v1/students/{world.student.pk}/photo/")


def test_deleting_a_record_created_in_error_removes_its_photo(world, admin_user, django_capture_on_commit_callbacks):
    mistake = make_student(world.programme, world.l100)
    client_for(admin_user).put(f"/api/v1/students/{mistake.pk}/photo/", {"file": jpeg()}, format="multipart")
    mistake.refresh_from_db()
    name, storage = mistake.photo.name, mistake.photo.storage
    with django_capture_on_commit_callbacks(execute=True):
        assert client_for(admin_user).post(f"/api/v1/students/{mistake.pk}/delete-record/", {"reason": "Duplicate"},
                                           format="json").status_code == 204
    assert not storage.exists(name)


@pytest.mark.parametrize("fmt,expected_2026,expected_2005", [
    ("{SHORT}{YEAR:02}{SEQ:04}", "CN260007", "CN050007"),
    ("{SHORT}/{YEAR}/{SEQ:04}", "CN/2026/0007", "CN/2005/0007"),
    ("{YEAR:2}-{SEQ:03}", "26-007", "05-007"),
])
def test_short_year_token(fmt, expected_2026, expected_2005):
    assert render_format(fmt, short="CN", year=2026, seq=7) == expected_2026
    assert render_format(fmt, short="CN", year=2005, seq=7) == expected_2005


def test_short_year_in_settings_and_new_students(world, admin_user, registrar):
    api = client_for(admin_user)
    assert api.patch("/api/v1/institution/settings/", {"student_number_format": "{SHORT}{YEAR:02}{SEQ:04}"},
                     format="json").status_code == 200
    created = client_for(registrar).post("/api/v1/students/", {
        "first_name": "Ama", "last_name": "Short", "email": "ama.short@example.test", "programme": world.programme.pk,
        "current_level": world.l100.pk, "admission_year": 2026}, format="json")
    assert created.status_code == 201 and created.data["student_number"] == "TC260001"
