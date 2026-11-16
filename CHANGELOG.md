# Release notes

Copyright © 2026 Alfa Calix Enterprise. All rights reserved.

Each release lists what changed, what to do when updating, and the checks it passed. Update the
backend and the frontend to the same release together. Version numbers follow semantic versioning:
see [docs/developers/releasing.md](docs/developers/releasing.md).

## 1.0.0-rc.1 — release candidate (16 November 2026)

The first release candidate for 1.0.0, for pilot and test portals. It becomes **1.0.0** once it passes
the PostgreSQL test run and the first institution goes live on it.

First production release of SIS: a student information system for colleges, nursing and midwifery
training institutions, technical institutions and universities. One portal per institution.

### Features

* **Institution set-up:** identity, logo, colour, contacts, time zone, student-number and transcript
  serial formats, registration, results, fees and sign-in policies. The portal and installed app are
  branded from these settings.
* **Academic structure:** academic years and any number of semesters, departments, levels, programmes
  with credit limits, courses with prerequisites, curricula, course offerings with lecturers; course
  import.
* **Students:** records with automatic numbering, photos (single or bulk upload, stored privately),
  status and programme or level changes with history, imports with temporary passwords and sign-in
  slips, exports, bulk actions, deleting records created in error.
* **Registration:** a full eligibility check (window, add/drop, status, programme, level, compulsory
  courses, prerequisites, retakes, credit limits, capacity), approvals, registration on a student's
  behalf, bulk registration of a class, late registration with a penalty, class lists, slips.
* **Results:** grading schemes, assessment components, marks entry and import, submit → approve →
  publish with separation of duties, amendments with a second approver, GPA and CGPA, result slips,
  incomplete results and withdrawal mid-semester.
* **Academic regulations:** resits, retake caps, attempt limits, repeating a level, and progression
  reviews proposing probation, promotion, repeating and dismissal, with presets.
* **Fees and payments:** fee schedules and class billing, an append-only student ledger, online payment
  by mobile money, card or bank with the fee added on top, payments recorded by the Accounts Office or
  imported from a bank statement with duplicate detection, receipts and statements, automatic financial
  clearance at a set share of the bill.
* **Official transcripts:** numbered, frozen at issue, verifiable by code and QR code on a public page,
  signed by hand or with an uploaded signature and seal; reprint and revoke; interim transcripts.
* **Printed documents:** every PDF carries the institution's name across the page in a printable grey;
  interim and cancelled documents are stamped.
* **Notifications:** in-app, push (with an app-icon badge and chime) and email within the provider's
  daily allowance; the installable app with an offline page.
* **Accounts and security:** roles and appointments to offices, passkeys (fingerprint or face), a
  growing wait after wrong passwords, signed-in devices with sign-out of one or all, administrator
  sign-out everywhere, a maximum sign-in length, a complete audit log.
* **Data tables:** search, filters, sorting, rows per page (remembered), selecting every matching
  record, bulk actions in batches with progress, CSV and Excel export.
* **Help:** the user guide at `/docs` on every portal, open without signing in and branded with the
  institution's name and logo; linked from the account menu, *My account* and the sign-in page.
* **Version shown** in the account menu and on *My account*, with a warning when the server runs a
  different release.

### Updating to this release

New installation: follow [docs/operations/deployment.md](docs/operations/deployment.md). New settings
since earlier test builds: `PAYSTACK_SECRET_KEY`, `VAPID_*`, `WEBAUTHN_*` (normally unset),
`CACHE_LOCATION`, `NUM_PROXIES`. Run `migrate` and add `run_daily_tasks` as the scheduled task.

### Checks

| Check | Result |
|---|---|
| Backend tests | 333 passed (SQLite) |
| End-to-end tests | 47 passed (Chromium, desktop and mobile layouts) |
| Migrations, API schema, lint | Clean |
| PostgreSQL test run | **Not yet run: required before the first production installation** |

## Current limitations

* **PostgreSQL** is configured but has not yet been tested: run the suite on it before go-live.
* **Online payment** has been tested against a simulated gateway only. Test with the institution's
  test keys (mobile money, card and bank) before using live keys. Refunds are made from the payment
  provider's dashboard and then recorded in the student's account.
* **Push notifications and the app badge** have been tested in desktop Chromium only. Check them on
  real Android phones and iPhones before rollout.
* **Printing:** check the watermark, stamps, signature and seal on the institution's own printers.
* iPhone HEIC photos are not accepted (save as JPG first); photo cropping is centred, not face-aware.
* Resit scores are entered by hand (no spreadsheet import yet).
* Passkeys are optional; no second sign-in factor is required for administrators yet.
* Imports, exports and PDFs run in the request: fine for about 2,000 rows.
* Changing a grading scheme does not recalculate existing result sheets (raw marks are kept).
* One set of academic regulations per installation; graduation checks and award classification are not
  implemented; resit grades cannot be amended after publication.
* Registration status policies are configured by support staff; there is no screen for them yet.
* The audit log cannot stop changes made directly in the database by someone with server access; see
  [docs/security/design.md](docs/security/design.md).

## Roadmap

1. PostgreSQL rehearsal with a pilot institution's data, and automated off-site backups.
2. A required second factor (passkey or authenticator app) for administrators and result approvers.
3. Continuous integration running all tests and security checks on every change.
4. Per-programme regulations, graduation audit and award classification.
5. Resit score import; a screen for registration status policies.
6. Background jobs for very large imports and exports.
7. New modules: admissions, timetabling, attendance.
