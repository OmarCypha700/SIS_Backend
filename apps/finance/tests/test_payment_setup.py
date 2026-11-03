"""A misconfigured payment key: a clear message for students, a clear log line and check for IT."""
import logging
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import Role
from apps.finance import fees, paystack
from apps.finance.models import FeeItem, FeeSchedule, Payment, PaymentStatus
from apps.institutions.models import InstitutionProfile
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db


class _Response:
    def __init__(self, body, status):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


@pytest.fixture
def rejected(settings, monkeypatch):
    """The provider answers every call with 401 Invalid key, as it does for a wrong or public key."""
    settings.PAYSTACK_SECRET_KEY = "sk_test_wrong"
    profile = InstitutionProfile.load()
    profile.online_payments_enabled = True
    profile.save()
    monkeypatch.setattr("apps.finance.paystack.requests.request",
                        lambda *a, **k: _Response({"status": False, "message": "Invalid key"}, 401))


def test_a_rejected_key_gives_students_a_plain_message_and_it_a_clear_log(world, rejected, caplog):
    officer = make_user(Role.ACCOUNTS_OFFICER)
    schedule = FeeSchedule.objects.create(programme=world.programme, level=world.l100, academic_year=world.year,
                                          semester=world.semester)
    FeeItem.objects.create(schedule=schedule, description="Tuition", amount=Decimal("1000"))
    fees.bill(schedule, actor=officer)

    with caplog.at_level(logging.ERROR, logger="apps.finance.paystack"):
        response = client_for(world.student.user).post("/api/v1/my-fees/pay/", {"amount": "100"}, format="json")
    assert response.status_code == 503 and response.data["code"] == "payments_unavailable"
    assert "pay at the bank" in response.data["detail"] and "key" not in response.data["detail"].lower()
    assert "PAYSTACK_SECRET_KEY" in caplog.text and "pk_" in caplog.text
    assert Payment.objects.get().status == PaymentStatus.FAILED


def test_setup_check_command(settings, rejected):
    with pytest.raises(CommandError, match="rejected PAYSTACK_SECRET_KEY"):
        call_command("check_payment_setup")
    settings.PAYSTACK_SECRET_KEY = "pk_test_public"
    with pytest.raises(CommandError, match="public key"):
        call_command("check_payment_setup")
    settings.PAYSTACK_SECRET_KEY = ""
    with pytest.raises(CommandError, match="not set"):
        call_command("check_payment_setup")


def test_setup_check_reports_the_mode(settings, monkeypatch):
    settings.PAYSTACK_SECRET_KEY = "sk_test_good"
    monkeypatch.setattr("apps.finance.paystack.requests.request",
                        lambda *a, **k: _Response({"status": True, "data": []}, 200))
    ok, message = paystack.check_setup()
    assert ok and "TEST mode" in message


def test_fee_schedules_page_in_a_fixed_order(world):
    officer = make_user(Role.ACCOUNTS_OFFICER)
    for level in (world.l200, world.l100):
        FeeSchedule.objects.create(programme=world.programme, level=level, academic_year=world.year,
                                   semester=world.semester)
    response = client_for(officer).get("/api/v1/fee-schedules/?page_size=1")
    assert response.status_code == 200 and response.data["results"][0]["level_name"] == "Level 100"
