"""School fees: billing, the 60% registration rule, and paying online through Paystack."""
import datetime
import hashlib
import hmac
import itertools
import io
import json
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.academics.models import AcademicYear, PeriodStatus, Semester
from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.finance import accounts, fees, paystack
from apps.finance.models import (
    EntryKind, FeeItem, FeeSchedule, FinancialClearance, LedgerEntry, Payment, PaymentStatus,
)
from apps.institutions.models import FeeBillingPeriod, InstitutionProfile
from apps.notifications.models import Kind, Notification
from conftest import client_for, make_user

pytestmark = pytest.mark.django_db
SECRET = "sk_test_example"


@pytest.fixture
def officer(db):
    return make_user(Role.ACCOUNTS_OFFICER)


def settings_(**values):
    profile = InstitutionProfile.load()
    for key, value in values.items():
        setattr(profile, key, value)
    profile.save()
    return profile


def schedule(world, *amounts, semester="current", year=None):
    semester = world.semester if semester == "current" else semester
    made = FeeSchedule.objects.create(programme=world.programme, level=world.l100, academic_year=year or world.year,
                                      semester=semester)
    for n, amount in enumerate(amounts):
        FeeItem.objects.create(schedule=made, description=f"Item {n}", amount=Decimal(amount), order=n)
    return made


def pay(world, student, amount, officer, reference=None):
    reference = reference or f"SLIP-{next(_slips)}"
    return accounts.record_manual_payment(student, amount=amount, reference=reference, actor=officer)


_slips = itertools.count(1)


def earlier_semester(world):
    start = world.year.start_date - datetime.timedelta(days=400)
    year = AcademicYear.objects.create(name="2024/2025", start_date=start, end_date=start + datetime.timedelta(days=360),
                                       status=PeriodStatus.CLOSED)
    now = timezone.now() - datetime.timedelta(days=390)
    return Semester.objects.create(academic_year=year, name="First Semester", term_number=1, start_date=start,
                                   end_date=start + datetime.timedelta(days=120), registration_start=now,
                                   registration_end=now + datetime.timedelta(days=10), status=PeriodStatus.CLOSED)


# --- Fee and the rule ----------------------------------------------------------------------

def test_transaction_fee_is_added_so_the_school_receives_the_full_amount(world):
    profile = settings_(payment_fee_percent=Decimal("1.95"))
    assert paystack.fee_for(Decimal("1000"), profile) == Decimal("19.89")
    # After the provider takes 1.95% of 1019.89, at least 1000.00 is left.
    assert Decimal("1019.89") * (1 - Decimal("0.0195")) >= Decimal("1000")
    profile = settings_(payment_fee_cap=Decimal("10.00"))
    assert paystack.fee_for(Decimal("1000"), profile) == Decimal("10.00")
    profile = settings_(payment_fee_percent=Decimal("0"), payment_fee_cap=None)
    assert paystack.fee_for(Decimal("1000"), profile) == Decimal("0.00")


def test_sixty_percent_of_the_semester_bill_clears_the_student(world, officer):
    settings_(require_financial_clearance=True)
    fees.bill(schedule(world, "700", "300"), actor=officer)
    assert accounts.requirement(world.student, world.semester)["needed"] == Decimal("600.00")
    pay(world, world.student, "599.99", officer)
    result = accounts.requirement(world.student, world.semester)
    assert not result["meets"] and result["short_by"] == Decimal("0.01")
    assert not FinancialClearance.objects.filter(student=world.student, cleared=True).exists()
    assert "GHS 0.01 more" in client_for(world.student.user).get("/api/v1/registrations/current/").data[
        "financial_clearance"]["message"]

    pay(world, world.student, "0.01", officer)
    clearance = FinancialClearance.objects.get(student=world.student, semester=world.semester)
    assert clearance.cleared and clearance.note.startswith("Automatic: 60%") and clearance.decided_by is None
    assert client_for(world.student.user).get("/api/v1/registrations/current/").data["financial_clearance"]["message"] is None
    assert Notification.objects.filter(recipient=world.student.user, kind=Kind.FINANCIAL_CLEARANCE).exists()


def test_earlier_balances_must_be_paid_in_full(world, officer):
    earlier = earlier_semester(world)
    fees.bill(schedule(world, "500", semester=earlier, year=earlier.academic_year), actor=officer)
    fees.bill(schedule(world, "1000"), actor=officer)
    pay(world, world.student, "600", officer)  # pays off the 500 first, only 100 towards this semester
    result = accounts.requirement(world.student, world.semester)
    assert result["needed"] == Decimal("1100.00") and not result["meets"]  # 500 earlier + 60% of 1000
    assert result["earlier_balance"] == Decimal("0.00") and result["percent_paid"] == 10
    pay(world, world.student, "500", officer)
    assert accounts.requirement(world.student, world.semester)["meets"]


@pytest.mark.parametrize("percent,paid,meets", [(0, None, True), (100, "999.99", False), (100, "1000", True)])
def test_percentage_is_a_setting(world, officer, percent, paid, meets):
    settings_(registration_payment_percent=percent)
    fees.bill(schedule(world, "1000"), actor=officer)
    if paid:
        pay(world, world.student, paid, officer)
    assert accounts.requirement(world.student, world.semester)["meets"] is meets


def test_yearly_billing_clears_every_semester_of_the_year(world, officer):
    settings_(fee_billing_period=FeeBillingPeriod.ACADEMIC_YEAR, require_financial_clearance=True)
    second = Semester.objects.create(academic_year=world.year, name="Second Semester", term_number=2,
                                     start_date=world.semester.end_date + datetime.timedelta(days=1),
                                     end_date=world.semester.end_date + datetime.timedelta(days=120),
                                     registration_start=timezone.now() + datetime.timedelta(days=90),
                                     registration_end=timezone.now() + datetime.timedelta(days=100))
    fees.bill(schedule(world, "2000", semester=None), actor=officer)
    pay(world, world.student, "1200", officer)
    cleared = set(FinancialClearance.objects.filter(student=world.student, cleared=True).values_list("semester", flat=True))
    assert cleared == {world.semester.pk, second.pk}


def test_a_person_revoking_clearance_is_not_overridden(world, officer):
    from apps.finance import services

    settings_(require_financial_clearance=True)
    fees.bill(schedule(world, "1000"), actor=officer)
    services.set_clearance([world.student], world.semester, cleared=False, note="Cheque bounced", actor=officer)
    pay(world, world.student, "1000", officer)
    assert not FinancialClearance.objects.get(student=world.student, semester=world.semester).cleared


# --- Billing -------------------------------------------------------------------------------

def test_fee_schedules_and_billing_through_the_api(world, officer):
    api = client_for(officer)
    response = api.post("/api/v1/fee-schedules/", {
        "programme": world.programme.pk, "level": world.l100.pk, "academic_year": world.year.pk,
        "semester": world.semester.pk, "items": [{"description": "Tuition", "amount": "1500.00"},
                                                 {"description": "SRC dues", "amount": "50.00"}]}, format="json")
    assert response.status_code == 201, response.data
    schedule_id = response.data["id"]
    assert response.data["total"] == "1550.00"
    duplicate = api.post("/api/v1/fee-schedules/", {
        "programme": world.programme.pk, "level": world.l100.pk, "academic_year": world.year.pk,
        "semester": world.semester.pk, "items": [{"description": "Tuition", "amount": "1"}]}, format="json")
    assert duplicate.status_code == 400

    preview = api.post(f"/api/v1/fee-schedules/{schedule_id}/bill/", {"dry_run": True}, format="json").data
    assert preview["students"] == 2 and preview["total"] == Decimal("3100.00") and LedgerEntry.objects.count() == 0
    done = api.post(f"/api/v1/fee-schedules/{schedule_id}/bill/", {"dry_run": False}, format="json").data
    assert done["changed"] == 2 and accounts.balance(world.student) == Decimal("1550.00")
    again = api.post(f"/api/v1/fee-schedules/{schedule_id}/bill/", {"dry_run": False}, format="json").data
    assert again["changed"] == 0 and accounts.balance(world.student) == Decimal("1550.00")

    # Tuition goes up: billing again charges only the difference.
    items = api.get(f"/api/v1/fee-schedules/{schedule_id}/").data["items"]
    items[0]["amount"] = "1600.00"
    assert api.patch(f"/api/v1/fee-schedules/{schedule_id}/", {"items": items}, format="json").status_code == 200
    api.post(f"/api/v1/fee-schedules/{schedule_id}/bill/", {"dry_run": False}, format="json")
    assert accounts.balance(world.student) == Decimal("1650.00")
    assert LedgerEntry.objects.filter(student=world.student, kind=EntryKind.ADJUSTMENT, amount=Decimal("100")).exists()
    # Billed fees cannot be deleted.
    assert api.delete(f"/api/v1/fee-schedules/{schedule_id}/").status_code == 409


def test_semester_is_required_when_billing_per_semester(world, officer):
    response = client_for(officer).post("/api/v1/fee-schedules/", {
        "programme": world.programme.pk, "level": world.l100.pk, "academic_year": world.year.pk, "semester": None,
        "items": [{"description": "Tuition", "amount": "1000"}]}, format="json")
    assert response.status_code == 400 and "semester" in str(response.data)


def test_reversed_charge_is_not_billed_again(world, officer):
    plan = schedule(world, "1000")
    fees.bill(plan, actor=officer)
    charge = LedgerEntry.objects.get(student=world.student, kind=EntryKind.CHARGE)
    accounts.reverse_entry(charge, reason="Not in this programme", actor=officer)
    fees.bill(plan, actor=officer)
    assert accounts.balance(world.student) == Decimal("0.00")


# --- Accounts Office entries ---------------------------------------------------------------

def test_waivers_refunds_and_reversals_need_a_reason_and_are_audited(world, officer):
    fees.bill(schedule(world, "1000"), actor=officer)
    api = client_for(officer)
    url = f"/api/v1/student-accounts/{world.student.pk}/"
    assert api.post(url + "entry/", {"kind": "WAIVER", "amount": "200", "reason": ""}, format="json").status_code == 400
    assert api.post(url + "entry/", {"kind": "WAIVER", "amount": "200", "reason": "Merit scholarship"},
                    format="json").status_code == 201
    assert api.post(url + "entry/", {"kind": "REFUND", "amount": "500", "reason": "Too much"},
                    format="json").status_code == 400  # more than was paid or waived
    paid = api.post(url + "record-payment/", {"amount": "300", "reference": "GCB-1", "channel": "bank"}, format="json")
    assert paid.status_code == 201 and paid.data["receipt_number"].startswith("TC/RCP/")
    assert api.post(url + "record-payment/", {"amount": "300", "reference": "GCB-1"}, format="json").status_code == 409
    detail = api.get(url).data
    assert Decimal(str(detail["summary"]["balance"])) == Decimal("500.00")
    payment_line = next(line for line in detail["statement"] if line["kind"] == "PAYMENT")
    assert api.post(url + "reverse/", {"entry": payment_line["id"], "reason": "Wrong student"},
                    format="json").status_code == 200
    assert api.post(url + "reverse/", {"entry": payment_line["id"], "reason": "Again"}, format="json").status_code == 409
    assert accounts.balance(world.student) == Decimal("800.00")
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"finance.waiver", "finance.payment_recorded", "finance.reversed", "finance.billed"} <= actions
    assert api.get(url + "statement-pdf/").status_code == 200


def lines(*rows):
    return "\n".join(rows) + "\n"


def upload(text):
    file = io.BytesIO(text.encode())
    file.name = "bank.csv"
    return file


def test_bank_statement_is_previewed_then_recorded(world, officer):
    fees.bill(schedule(world, "1000"), actor=officer)
    pay(world, world.student, "300", officer, reference="GCB-1")  # already recorded from the slip
    today = timezone.localdate().isoformat()
    number, other = world.student.student_number, world.other_student.student_number
    csv = lines("student_number,amount,reference,date,method,note",
                f"{number},400.00,GCB-9,{today},bank,Tanoso",
                f"{number},400.00,GCB-9,{today},bank,same slip twice",
                f"{number},300.00,GCB-1,{today},bank,already recorded",
                "NOPE/1,100,X-1,,,",
                f"{other},abc,GCB-10,,,",
                f"{other},200,,,,no reference")
    api = client_for(officer)
    preview = api.post("/api/v1/payments/import/preview/", {"file": upload(csv)}, format="multipart").data
    statuses = [row["status"] for row in preview["rows"]]
    assert statuses == ["new", "repeated", "recorded", "unknown", "invalid", "invalid"]
    assert accounts.balance(world.student) == Decimal("700.00")  # the preview saved nothing

    result = api.post("/api/v1/payments/import/confirm/", {"rows": preview["rows"]}, format="json").data
    assert result["recorded"] == 1 and len(result["skipped"]) == 5
    assert accounts.balance(world.student) == Decimal("300.00")
    # Confirming the same rows again records nothing: every row is checked again.
    again = api.post("/api/v1/payments/import/confirm/", {"rows": preview["rows"]}, format="json").data
    assert again["recorded"] == 0 and accounts.balance(world.student) == Decimal("300.00")


def test_same_amount_under_another_reference_is_flagged_as_a_possible_duplicate(world, officer):
    fees.bill(schedule(world, "1000"), actor=officer)
    # Recorded by hand from the cashier's receipt; the bank statement later shows the bank's own number.
    pay(world, world.student, "500", officer, reference="CASHIER-0042")
    today = timezone.localdate().isoformat()
    csv = lines("student_number,amount,reference,date", f"{world.student.student_number},500,GCB-77,{today}")
    api = client_for(officer)
    row = api.post("/api/v1/payments/import/preview/", {"file": upload(csv)}, format="multipart").data["rows"][0]
    assert row["status"] == "possible_duplicate" and "CASHIER-0042" in row["message"]
    # A different amount, or a payment weeks apart, is not flagged.
    far = (timezone.localdate() + datetime.timedelta(days=20)).isoformat()
    csv = lines("student_number,amount,reference,date", f"{world.student.student_number},499,GCB-78,{today}",
                f"{world.student.student_number},500,GCB-79,{far}")
    rows = api.post("/api/v1/payments/import/preview/", {"file": upload(csv)}, format="multipart").data["rows"]
    assert [r["status"] for r in rows] == ["new", "new"]
    # Ticked on purpose (a genuine second payment): it is recorded.
    result = api.post("/api/v1/payments/import/confirm/", {"rows": [row]}, format="json").data
    assert result["recorded"] == 1


def test_import_reports_students_left_in_credit(world, officer):
    fees.bill(schedule(world, "1000"), actor=officer)
    csv = lines("student_number,amount,reference", f"{world.student.student_number},1200,GCB-500")
    api = client_for(officer)
    rows = api.post("/api/v1/payments/import/preview/", {"file": upload(csv)}, format="multipart").data["rows"]
    result = api.post("/api/v1/payments/import/confirm/", {"rows": rows}, format="json").data
    assert result["in_credit"] == [{"student": world.student.pk, "student_number": world.student.student_number,
                                    "full_name": world.student.full_name, "credit": Decimal("200.00")}]


def test_a_recorded_payment_needs_the_bank_reference(world, officer):
    response = client_for(officer).post(f"/api/v1/student-accounts/{world.student.pk}/record-payment/",
                                        {"amount": "300"}, format="json")
    assert response.status_code == 400 and "reference" in str(response.data)


def test_only_finance_staff_see_accounts_and_payments(world, officer):
    registrar = make_user(Role.REGISTRAR)
    for user in (registrar, world.student.user):
        api = client_for(user)
        assert api.get("/api/v1/student-accounts/").status_code == 403
        assert api.get("/api/v1/payments/").status_code == 403
        assert api.get("/api/v1/fee-schedules/").status_code == 403
    assert client_for(officer).get("/api/v1/payments/summary/").status_code == 200


# --- Paying online -------------------------------------------------------------------------

class FakeGateway:
    """Stands in for api.paystack.co: records calls and answers verify with `self.outcome`."""

    def __init__(self):
        self.calls, self.outcome, self.amount_offset, self.currency = [], "success", 0, "GHS"

    def __call__(self, method, url, headers=None, timeout=None, json=None, **kwargs):  # noqa: A002 - requests' name
        self.calls.append((method, url, json))
        assert headers["Authorization"] == f"Bearer {SECRET}" and timeout
        if url.endswith("/transaction/initialize"):
            return _Response({"status": True, "data": {"authorization_url": "https://checkout.paystack.com/abc",
                                                       "reference": json["reference"]}})
        reference = url.rsplit("/", 1)[-1]
        payment = Payment.objects.get(reference=reference)
        return _Response({"status": True, "data": {
            "status": self.outcome, "reference": reference, "currency": self.currency,
            "amount": paystack.pesewas(payment.total) + self.amount_offset, "channel": "mobile_money",
            "paid_at": "2026-11-02T10:00:00Z", "gateway_response": "Approved",
            "authorization": {"bank": "MTN", "brand": "mtn", "last4": "1234"},
        }})


class _Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


@pytest.fixture
def gateway(settings, monkeypatch, world):
    settings.PAYSTACK_SECRET_KEY = SECRET
    settings_(online_payments_enabled=True, payment_fee_percent=Decimal("1.95"))
    fake = FakeGateway()
    monkeypatch.setattr("apps.finance.paystack.requests.request", fake)
    return fake


def webhook(client, body, secret=SECRET):
    raw = json.dumps(body).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return client.post("/api/v1/payments/paystack/webhook/", raw, content_type="application/json",
                       HTTP_X_PAYSTACK_SIGNATURE=signature)


def test_student_pays_online_and_is_credited_once(world, officer, gateway, client, django_capture_on_commit_callbacks):
    settings_(require_financial_clearance=True)
    fees.bill(schedule(world, "1000"), actor=officer)
    api = client_for(world.student.user)
    overview = api.get("/api/v1/my-fees/").data
    assert overview["online_enabled"] and Decimal(str(overview["summary"]["short_by"])) == Decimal("600.00")
    quote = api.get("/api/v1/my-fees/quote/?amount=600").data
    assert quote["fee"] == Decimal("11.94") and quote["total"] == Decimal("611.94")

    started = api.post("/api/v1/my-fees/pay/", {"amount": "600"}, format="json")
    assert started.status_code == 201 and started.data["authorization_url"].startswith("https://checkout.paystack.com")
    reference = started.data["reference"]
    sent = gateway.calls[0][2]
    assert sent["amount"] == 61194 and sent["currency"] == "GHS" and sent["callback_url"].endswith("/fees/return")

    with django_capture_on_commit_callbacks(execute=True):
        assert webhook(client, {"event": "charge.success", "data": {"reference": reference}}).status_code == 200
    # The student's return to the portal confirms again: still one credit.
    returned = api.post(f"/api/v1/my-fees/payments/{reference}/verify/").data
    assert returned["payment"]["status"] == "SUCCESS" and returned["cleared"] is True
    payment = Payment.objects.get(reference=reference)
    assert payment.receipt_number and payment.channel == "mobile_money"
    assert "last4" not in payment.gateway_response  # no card details kept
    assert LedgerEntry.objects.filter(payment=payment).count() == 1
    assert accounts.balance(world.student) == Decimal("400.00")  # the fee is not credited to school fees
    assert Notification.objects.filter(recipient=world.student.user, kind=Kind.PAYMENT_RECEIVED).exists()
    assert api.get(f"/api/v1/my-fees/payments/{reference}/receipt/").status_code == 200

    # Another student cannot see or check this payment.
    other = client_for(world.other_student.user)
    assert other.post(f"/api/v1/my-fees/payments/{reference}/verify/").status_code == 404
    assert other.get(f"/api/v1/my-fees/payments/{reference}/receipt/").status_code == 404


def test_webhook_with_a_bad_signature_is_refused(world, officer, gateway, client):
    fees.bill(schedule(world, "1000"), actor=officer)
    reference = client_for(world.student.user).post("/api/v1/my-fees/pay/", {"amount": "100"}, format="json").data["reference"]
    assert webhook(client, {"event": "charge.success", "data": {"reference": reference}},
                   secret="sk_test_wrong").status_code == 401
    assert Payment.objects.get(reference=reference).status == PaymentStatus.PENDING


@pytest.mark.parametrize("change", ["amount", "currency"])
def test_mismatched_amount_or_currency_is_not_credited(world, officer, gateway, change,
                                                       django_capture_on_commit_callbacks):
    fees.bill(schedule(world, "1000"), actor=officer)
    reference = client_for(world.student.user).post("/api/v1/my-fees/pay/", {"amount": "100"}, format="json").data["reference"]
    if change == "amount":
        gateway.amount_offset = -1000
    else:
        gateway.currency = "NGN"
    with django_capture_on_commit_callbacks(execute=True):
        payment = paystack.confirm(reference)
    assert payment.status == PaymentStatus.MISMATCH and not LedgerEntry.objects.filter(payment=payment).exists()
    assert accounts.balance(world.student) == Decimal("1000.00")
    assert Notification.objects.filter(recipient=officer, kind=Kind.PAYMENT_ATTENTION).exists()


def test_limits_on_what_a_student_can_pay(world, officer, gateway):
    api = client_for(world.student.user)
    assert api.post("/api/v1/my-fees/pay/", {"amount": "100"}, format="json").data["code"] == "nothing_owed"
    fees.bill(schedule(world, "1000"), actor=officer)
    assert api.post("/api/v1/my-fees/pay/", {"amount": "1000.01"}, format="json").data["code"] == "amount_too_large"
    settings_(online_payments_enabled=False)
    assert api.post("/api/v1/my-fees/pay/", {"amount": "100"}, format="json").status_code == 403


def test_daily_task_confirms_or_gives_up_on_pending_payments(world, officer, gateway, monkeypatch):
    fees.bill(schedule(world, "1000"), actor=officer)
    api = client_for(world.student.user)
    paid = api.post("/api/v1/my-fees/pay/", {"amount": "100"}, format="json").data["reference"]
    left = api.post("/api/v1/my-fees/pay/", {"amount": "100"}, format="json").data["reference"]
    Payment.objects.filter(reference=paid).update(created_at=timezone.now() - datetime.timedelta(hours=1))
    Payment.objects.filter(reference=left).update(created_at=timezone.now() - datetime.timedelta(hours=30))
    gateway.outcome = "abandoned"
    original = gateway.__call__

    def answer(method, url, **kwargs):
        gateway.outcome = "success" if url.endswith(paid) else "abandoned"
        return original(method, url, **kwargs)

    monkeypatch.setattr("apps.finance.paystack.requests.request", answer)
    call_command("reconcile_payments")
    assert Payment.objects.get(reference=paid).status == PaymentStatus.SUCCESS
    assert Payment.objects.get(reference=left).status == PaymentStatus.ABANDONED
    assert accounts.balance(world.student) == Decimal("900.00")


def test_online_payment_cannot_be_turned_on_without_the_key(admin_user, settings):
    settings.PAYSTACK_SECRET_KEY = ""
    response = client_for(admin_user).patch("/api/v1/institution/settings/", {"online_payments_enabled": True},
                                            format="json")
    assert response.status_code == 400


def test_fee_settings_from_the_settings_form(admin_user):
    api = client_for(admin_user)
    response = api.patch("/api/v1/institution/settings/", {"payment_fee_cap": "10.00", "registration_payment_percent": "70",
                                                           "fee_billing_period": "ACADEMIC_YEAR"}, format="multipart")
    assert response.status_code == 200, response.data
    profile = InstitutionProfile.load()
    assert profile.payment_fee_cap == Decimal("10.00") and profile.registration_payment_percent == 70
    assert api.patch("/api/v1/institution/settings/", {"payment_fee_cap": ""}, format="multipart").status_code == 200
    assert InstitutionProfile.load().payment_fee_cap is None
    assert api.patch("/api/v1/institution/settings/", {"registration_payment_percent": "101"},
                     format="multipart").status_code == 400


def test_reversing_a_payment_cancels_it_and_warns_about_clearance(world, officer):
    from apps.finance import services

    settings_(require_financial_clearance=True)
    fees.bill(schedule(world, "1000"), actor=officer)
    payment = pay(world, world.student, "600", officer, reference="GCB-WRONG")
    assert services.is_cleared(world.student, world.semester)  # cleared automatically at 60%
    api = client_for(officer)
    url = f"/api/v1/student-accounts/{world.student.pk}/"
    line = next(x for x in api.get(url).data["statement"] if x["kind"] == "PAYMENT")
    response = api.post(url + "reverse/", {"entry": line["id"], "reason": "Recorded on the wrong student"}, format="json")
    assert response.status_code == 200
    assert "still cleared" in response.data["warning"] and "600.00" in response.data["warning"]

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REVERSED and payment.reversal_reason == "Recorded on the wrong student"
    assert services.is_cleared(world.student, world.semester)  # never revoked automatically
    receipt = api.get(f"/api/v1/payments/{payment.pk}/receipt/")
    assert receipt.status_code == 200 and receipt["Content-Type"] == "application/pdf"
    # A reversed payment no longer counts as collected.
    assert api.get("/api/v1/payments/summary/").data["collected"] == Decimal("0.00")


def test_payments_filtered_by_date_paid_and_exported_with_the_filters(world, officer):
    fees.bill(schedule(world, "1000"), actor=officer)
    early = pay(world, world.student, "100", officer, reference="GCB-EARLY")
    late = pay(world, world.student, "200", officer, reference="GCB-LATE")
    today = timezone.localdate()
    Payment.objects.filter(pk=early.pk).update(paid_at=timezone.now() - datetime.timedelta(days=10))
    api = client_for(officer)
    since = (today - datetime.timedelta(days=2)).isoformat()
    listed = api.get(f"/api/v1/payments/?date_from={since}&date_to={today.isoformat()}").data["results"]
    assert [p["reference"] for p in listed] == ["GCB-LATE"]
    exported = api.get(f"/api/v1/payments/export/?date_from={since}&file_format=csv")
    body = b"".join(exported.streaming_content if exported.streaming else [exported.content]).decode("utf-8-sig")
    assert "GCB-LATE" in body and "GCB-EARLY" not in body
    assert late.reference in body
