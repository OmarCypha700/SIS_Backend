# School fees and online payment

Students see what they owe and pay school fees in the portal by **mobile money (all networks), card or
bank**, through Paystack. The Accounts Office sets the fees, bills each class, records payments made at
the bank, and gives waivers or refunds. Once a student has paid enough, they are **financially cleared
automatically** and can register.

## How money flows

* The student pays on **Paystack's own checkout page**. Card numbers and MoMo PINs never reach this
  system.
* Paystack settles the money into the **institution's bank account**, which is linked to its Paystack
  business account. This system never holds money.
* A payment is credited only after the server has asked Paystack about it and the reference, currency
  (GHS) and amount all match. A payment that does not match is held as **Needs attention**, not
  credited, and the Accounts Office is notified.
* The **transaction fee is paid by the student on top** of the school fees. The amount is worked out so
  that, after Paystack's charge, the institution receives the full fee:

  `fee = amount × r ÷ (1 − r)`, rounded up to the pesewa, where `r` is the rate in Institution settings
  (1.95% by default). The optional cap limits the fee for one payment.

  Example: GHS 1,000.00 of fees + GHS 19.89 transaction fee = GHS 1,019.89 charged. The student
  sees this breakdown before paying, and on the receipt. Only the GHS 1,000.00 goes to their fees
  account.

## The registration rule

A student is cleared for the current semester when:

1. **everything billed for earlier periods is paid in full**, and
2. at least **X% of the current bill** is paid. X is set under Institution settings → *Share of fees
   to register* and is 60% by default.

Payments always pay off the oldest bill first. Waivers and scholarships count as paid, and refunds
reduce what was paid.

| Bills | Paid | Needed (60%) | Cleared? |
|---|---|---|---|
| This semester GHS 1,000 | GHS 600 | GHS 600 | Yes |
| This semester GHS 1,000 | GHS 599.99 | GHS 600 | No (GHS 0.01 short) |
| Last semester GHS 500 unpaid + this semester GHS 1,000 | GHS 600 | GHS 1,100 | No: 500 goes to last semester, only 100 to this one |
| Same | GHS 1,100 | GHS 1,100 | Yes |

* **Billing period** (Institution settings): *each semester*, or *each academic year*. Per year, the
  rule uses the year's bill, and clearance covers every semester of the year that has been set up.
* **Clearing is automatic** after any payment, waiver, adjustment or billing that brings the student
  over the line. It is recorded as "Automatic: 60% of fees paid" in the clearance list.
* **Automatic clearing never revokes**, and it never overrides a clearance that a person revoked with a
  reason (for example, a bounced cheque). The Accounts Office can still clear anyone manually, for
  example for a payment plan.
* Clearance only blocks registration when *Students must be financially cleared before registering* is
  on. When it is on, the registration page tells the student exactly how much more to pay.

## Late registration penalty

Two things must be true for a student to register: they are **financially cleared**, and they register
**within the registration window**. A student who misses the window can still register during the
semester's **late registration period**, but only after paying the **late registration penalty** in full.

* **Settings.** Set the amount under Institution settings → School fees → *Late registration penalty*
  (0 turns it off). For each semester, set *Late registration closes* under Academic periods, after the
  normal closing date. Without it, registration simply closes as before.
* **Who is charged.** Only students who actually register late. The registration page tells them the
  amount; *Add the penalty to my fees* puts it on their account (once per semester), and they pay it
  online or at the bank like any other fee. Students who never register late are never charged.
* **Paid in full, by new money.** The penalty is paid by money paid after it was charged, or by credit
  the student already had. Fees paid earlier stay with the fees, so a student who had already paid the
  whole semester still pays the penalty. It is in addition to the share of the fees (60% by default).
* **Add/drop is not late.** A student who registered on time can still change courses until the add/drop
  deadline without a penalty.
* **Exceptions.** The Accounts Office can *waive* the penalty (e.g. medical grounds) with a reason: that
  counts as paid. Reversing a penalty charged in error removes it without charging it again.
* **Registry staff** registering a student late, on their behalf, can add the penalty from the *Register
  a student* page and are held to the same rule; only an administrator can register the student before
  it is paid, and the override is recorded in the audit log.

| Example | Needed to register |
|---|---|
| Fees GHS 1,000 (60% rule), penalty GHS 200, nothing paid yet | GHS 800: the penalty in full plus GHS 600 |
| Had paid GHS 1,000 before the window closed, then adds the GHS 200 penalty | GHS 200 more: the penalty |

## Accounts Office: day to day

* **Finance → Fee schedules:**
  * Set the fees for each programme and level, one line per item (tuition, examination fee, dues…).
  * *Copy* last year's fees as a starting point.
  * *Bill* charges the fees to every student of that programme and level whose status allows
    registration. You see a preview first.
  * Billing again is safe:
    * students already billed are left alone and new students are added;
    * if an item's amount changed, the difference is billed as an adjustment;
    * a charge that was reversed is not billed again.
* **Finance → Student accounts:** each student's balance and statement. From here you can:
  * *Record payment*, for a bank deposit or cashier payment. The bank slip, teller or cashier receipt
    number is **required** as the reference, so the same payment cannot be recorded twice, by hand or
    from a bank statement.
  * *Waiver or scholarship*, *Refund*, or *Adjustment* (a correction to the bill; a negative amount
    lowers it). Each needs a reason.
  * *Reverse* an entry made in error. Nothing is ever edited or deleted: the reversal appears on the
    statement, and both are in the audit log.
* **Finance → Payments:**
  * Every online and recorded payment.
  * *Check status* asks Paystack again about a pending payment.
  * Download receipts, export to CSV or Excel.
  * *Import bank statement* (student_number, amount, reference, optional date, method and note). Nothing
    is recorded until you have seen the preview. Each row is shown as:
    * **New**: will be recorded.
    * **Possible duplicate**: the student already has a payment of the same amount within 3 days under a
      different reference, for example one recorded by hand from the slip, or paid online. It is left
      unticked; tick it only if it really is a second payment.
    * **Already recorded** (same reference), **Repeated in this file**, **Student not found** or
      **Needs fixing** (missing reference, bad amount or date): these are never recorded.

    Confirming checks every row again, so importing the same statement twice never records a payment
    twice. The result lists any students now **in credit** (they paid more than they owed), so you can
    refund them or leave the credit for the next bill.

## Setting it up

1. **Paystack account.**
   * Register the institution as a business at <https://paystack.com> (Ghana). Paystack needs the
     registration documents and a GHS bank account.
   * Approval takes some days. Everything below works with **test keys** in the meantime.
2. **Secret key.**
   * In Paystack, go to *Settings → API Keys & Webhooks* and copy the **secret key**.
   * Use `sk_test_…` while testing, and `sk_live_…` only when you go live.
   * Put it in the server's `.env` (never in the code or in git):
     ```
     PAYSTACK_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxxxxxx
     ```
   * Reload the web app. `api.paystack.co` is on PythonAnywhere's free-account allowlist.
   * Check it from a Bash console:
     ```bash
     cd ~/sis-backend && ~/.virtualenvs/sis/bin/python manage.py check_payment_setup --settings=config.settings.prod
     ```
     It says whether the key works and whether you are in test or live mode.
3. **Webhook URL.** On the same Paystack page, set the webhook URL to
   `https://<your-backend>/api/v1/payments/paystack/webhook/`
   (for example `https://yourname.pythonanywhere.com/api/v1/payments/paystack/webhook/`). The webhook
   confirms payments even if the student closes the page before returning.
4. **Callback.**
   * After paying, students return to `FRONTEND_URL/fees/return`, which confirms the payment straight
     away. Check that `FRONTEND_URL` in `.env` is the portal's address.
5. **Institution settings → School fees:**
   * choose the billing period;
   * set the share needed to register (default 60%);
   * check the transaction fee rate (and cap, if any) against your Paystack pricing;
   * turn on **Students can pay online**. This cannot be turned on without the key.
6. **Daily task.** Use `python manage.py run_daily_tasks` as the PythonAnywhere scheduled task (see
   `deployment.md`). It confirms payments still pending after 30 minutes, and marks any not completed
   after 24 hours as *Not completed*.
7. **Test before going live.**
   * Set up a test fee, bill it to a test class, sign in as a student and pay.
   * Paystack's test mode shows test cards and a test mobile-money number on the checkout page.
   * Check the receipt, the balance and automatic clearance.
   * Then switch to the live key.

## Refunds and disputes

Money is refunded from the Paystack dashboard, or by the bank for a manual payment. Then record a
*Refund* on the student's account with the reason, so their balance is right. If a payment was
credited to the wrong student, reverse it on that student's account and record it on the right one.

## Payments in more than one place

* **Online and at the bank.** Both are credited: they are two payments with different references. If
  together they exceed what the student owes, the account is in credit and the next bill uses it up.
* **Online payments on the bank statement.** The payment service settles to the bank in lump sums for
  many students, without student numbers, so those lines are reported as *Student not found* and
  never credited twice. Do not add student numbers to settlement lines.
* **Recorded by hand, then on the statement.** Use the bank's own slip number when recording by hand;
  the statement row is then *Already recorded*. If a different number was used, the row shows as a
  *Possible duplicate* and is not recorded unless you tick it.

## Troubleshooting

* **Server log: "Online payment is misconfigured … rejected PAYSTACK_SECRET_KEY"** (Paystack answered
  *Invalid key*). Students see "Online payment is not available at the moment". Check that:
  * the value is the **secret** key (`sk_test_…` or `sk_live_…`), not the public key (`pk_…`);
  * it was copied whole, from the same mode (Test or Live) as the dashboard you are looking at;
  * it is in the `.env` of the project folder the web app runs from;
  * you **reloaded the web app** after editing `.env`.

  Then run `manage.py check_payment_setup` (above) until it says the key works.
* **"The payment service is not responding"**: Paystack could not be reached or returned an error. Try
  again later; payments left pending are checked by the daily task.
