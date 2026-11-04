"""Late registration: the penalty must be paid in full before a student who missed the window registers."""
import datetime
import itertools
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.finance import accounts, fees, late
from apps.finance.models import EntryKind, FeeItem, FeeSchedule, LedgerEntry
from apps.institutions.models import InstitutionProfile
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db
_slips = itertools.count(1)


@pytest.fixture
def officer(db):
    return make_user(Role.ACCOUNTS_OFFICER)


def settings_(**values):
    profile = InstitutionProfile.load()
    for key, value in values.items():
        setattr(profile, key, value)
    profile.save()


def window_closed(world, late_days=3):
    """Normal registration closed an hour ago; late registration runs for a few more days."""
    now = timezone.now()
    world.semester.registration_end = now - datetime.timedelta(hours=1)
    world.semester.late_registration_end = now + datetime.timedelta(days=late_days) if late_days else None
    world.semester.save()


def pay(student, amount, officer):
    return accounts.record_manual_payment(student, amount=amount, reference=f"SLIP-{next(_slips)}", actor=officer)


def courses(world):
    return [world.offerings[c].pk for c in ("CS101", "MATH101")]


def test_a_late_student_adds_the_penalty_pays_it_and_then_registers(world, officer):
    settings_(late_registration_penalty=Decimal("200"))
    window_closed(world)
    api = client_for(world.student.user)
    options = api.get("/api/v1/registrations/current/").data
    assert options["window_open"] and "Late registration is open" in options["window_message"]
    state = options["late_registration"]
    assert not state["charged"] and state["amount"] == Decimal("200.00") and "late registration penalty" in state["message"]

    blocked = api.post("/api/v1/registrations/submit/", {"offering_ids": courses(world)}, format="json")
    assert blocked.status_code == 403 and blocked.data["code"] == "late_penalty_unpaid"

    charged = api.post("/api/v1/registrations/late-penalty/", {}, format="json")
    assert charged.status_code == 201 and charged.data["late_registration"]["outstanding"] == Decimal("200.00")
    api.post("/api/v1/registrations/late-penalty/", {}, format="json")  # asking twice charges once
    assert LedgerEntry.objects.filter(student=world.student, kind=EntryKind.PENALTY).count() == 1
    assert accounts.balance(world.student) == Decimal("200.00")

    pay(world.student, "199.99", officer)
    assert api.post("/api/v1/registrations/submit/", {"offering_ids": courses(world)}, format="json").status_code == 403
    pay(world.student, "0.01", officer)
    done = api.post("/api/v1/registrations/submit/", {"offering_ids": courses(world)}, format="json")
    assert done.status_code == 200, done.data
    assert AuditLog.objects.filter(action="finance.late_penalty").exists()


def test_no_penalty_inside_the_window_or_when_it_is_zero(world):
    settings_(late_registration_penalty=Decimal("200"))
    assert late.state(world.student, world.semester) is None  # the window is open
    settings_(late_registration_penalty=Decimal("0"))
    window_closed(world)
    assert late.state(world.student, world.semester) is None
    response = client_for(world.student.user).post("/api/v1/registrations/submit/", {"offering_ids": courses(world)},
                                                   format="json")
    assert response.status_code == 200, response.data


def test_add_drop_changes_to_an_existing_registration_are_not_late(world):
    settings_(late_registration_penalty=Decimal("200"))
    api = client_for(world.student.user)
    assert api.post("/api/v1/registrations/submit/", {"offering_ids": courses(world)}, format="json").status_code == 200
    window_closed(world, late_days=0)  # add/drop still runs (the world's deadline is in 20 days)
    assert late.state(world.student, world.semester) is None
    changed = api.post("/api/v1/registrations/submit/",
                       {"offering_ids": courses(world) + [world.offerings["CS105"].pk]}, format="json")
    assert changed.status_code == 200, changed.data


def test_the_penalty_is_paid_before_the_share_of_the_fees(world, officer):
    settings_(late_registration_penalty=Decimal("200"), require_financial_clearance=True)
    schedule = FeeSchedule.objects.create(programme=world.programme, level=world.l100, academic_year=world.year,
                                          semester=world.semester)
    FeeItem.objects.create(schedule=schedule, description="Tuition", amount=Decimal("1000"))
    fees.bill(schedule, actor=officer)
    window_closed(world)
    late.charge(world.student, world.semester, actor=world.student.user)
    pay(world.student, "600", officer)  # 200 penalty first, so only 400 towards the fees
    needed = accounts.requirement(world.student, world.semester)
    assert needed["needed"] == Decimal("800.00") and needed["short_by"] == Decimal("200.00")
    assert needed["penalty_outstanding"] == Decimal("0.00") and needed["percent_paid"] == 40 and not needed["meets"]


def test_fees_paid_before_the_penalty_do_not_pay_it(world, officer):
    """A student who had paid the whole semester before missing the window still pays the penalty itself."""
    settings_(late_registration_penalty=Decimal("150"))
    schedule = FeeSchedule.objects.create(programme=world.programme, level=world.l100, academic_year=world.year,
                                          semester=world.semester)
    FeeItem.objects.create(schedule=schedule, description="Tuition", amount=Decimal("1000"))
    fees.bill(schedule, actor=officer)
    pay(world.student, "1000", officer)
    window_closed(world)
    late.charge(world.student, world.semester, actor=world.student.user)
    assert late.state(world.student, world.semester)["outstanding"] == Decimal("150.00")
    assert accounts.requirement(world.student, world.semester)["short_by"] == Decimal("150.00")
    pay(world.student, "150", officer)
    assert late.block(world.student, world.semester) is None


def test_existing_credit_pays_the_penalty(world, officer):
    settings_(late_registration_penalty=Decimal("150"))
    pay(world.student, "500", officer)  # paid ahead: in credit
    window_closed(world)
    late.charge(world.student, world.semester, actor=world.student.user)
    assert late.block(world.student, world.semester) is None


def test_a_waiver_counts_as_paid_and_a_reversal_removes_it(world, officer):
    settings_(late_registration_penalty=Decimal("200"))
    window_closed(world)
    entry = late.charge(world.student, world.semester, actor=world.student.user)
    accounts.add_entry(world.student, kind=EntryKind.WAIVER, amount="200", reason="Hospitalised in September",
                       actor=officer)
    assert late.block(world.student, world.semester) is None

    other = world.other_student
    penalty = late.charge(other, world.semester, actor=other.user)
    accounts.reverse_entry(penalty, reason="Charged in error", actor=officer)
    state = late.state(other, world.semester)
    assert state["charged"] and state["outstanding"] == Decimal("0.00")  # not asked to add it again
    assert late.block(other, world.semester) is None
    assert entry.amount == Decimal("200.00")


def test_registry_is_held_to_the_rule_and_only_an_administrator_can_override(world, admin_user):
    settings_(late_registration_penalty=Decimal("200"))
    window_closed(world, late_days=0)  # late registration has closed too
    student_api = client_for(world.student.user)
    assert not student_api.get("/api/v1/registrations/current/").data["window_open"]

    registrar = make_user(Role.REGISTRAR)
    body = {"student": world.student.pk, "semester": world.semester.pk, "offering_ids": courses(world),
            "reason": "Late arrival"}
    refused = client_for(registrar).post("/api/v1/registrations/on-behalf/", body, format="json")
    assert refused.status_code == 403 and refused.data["code"] == "late_penalty_unpaid"
    added = client_for(registrar).post("/api/v1/registrations/on-behalf/late-penalty/", {"student": world.student.pk},
                                       format="json")
    assert added.status_code == 201 and added.data["late_registration"]["charged"]

    allowed = client_for(admin_user).post("/api/v1/registrations/on-behalf/", body, format="json")
    assert allowed.status_code == 200, allowed.data
    assert AuditLog.objects.filter(action="registration.late_penalty_override").exists()


def test_late_registration_must_close_after_normal_registration(world, admin_user):
    response = client_for(admin_user).patch(f"/api/v1/semesters/{world.semester.pk}/", {
        "late_registration_end": (world.semester.registration_end - datetime.timedelta(days=1)).isoformat()},
        format="json")
    assert response.status_code == 400 and "late_registration_end" in str(response.data)
