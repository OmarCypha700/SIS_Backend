# Implementation status

## Completed

* **Foundation** – Django 5.2 + DRF modular monolith, environment-based settings (SQLite default,
  PostgreSQL via `DATABASE_URL`), OpenAPI docs, consistent errors and pagination, request ids.
* **Authentication** – session cookies + CSRF through a same-origin proxy, login/logout, password
  change, email password reset, temporary passwords for new staff, rate limiting, account status checks.
* **Roles and permissions** – Administrator, Registrar, Lecturer, Results Officer, Student; separate
  approve/publish/amend capabilities; deny-by-default views; object-level scoping.
* **Institution configuration** – identity, logo, colour, contacts, time zone, numbering formats,
  registration and results policies.
* **Academic structure** – academic years, any number of terms, departments, levels, programmes with
  level structure and credit limits, courses with prerequisites (cycle-checked), curriculum, course
  offerings with eligibility, capacity and lecturer assignment, generation from curriculum,
  protection of closed periods and referenced records.
* **Student records** – create (auto-numbering), edit, search/filter/sort/paginate, status and
  programme/level changes with history, deactivation instead of deletion, CSV import with preview
  and all-or-nothing confirm, duplicate detection, CSV export, academic record and transcript.
* **Course registration** – eligibility engine (window, add/drop, status policy, programme, level,
  compulsory courses, prerequisites, passed/failed/retake, credit limits and overload approval,
  capacity, cross-programme policy), transactional submission with row locking, history of every
  change, approvals, registration on behalf, class lists, exports, not-registered report, slips.
* **Results** – grading schemes with validated contiguous bands and rounding, assessment
  components (weights = 100%), keyboard-friendly marks entry with drafts, entry deadline,
  submit → return/approve → publish workflow, immutability after publication, amendment requests
  with second-person approval, GPA/CGPA with configurable retake policy, result slips, transcripts,
  grade distribution, CSV export.
* **Academic regulations** – configurable resits (eligibility, limit, grade cap) with their own
  approval workflow, retake grade caps, attempt limits, mandatory retake of failed compulsory courses,
  and end-of-semester progression reviews proposing probation, promotion, repeating and dismissal,
  which the registry reviews, overrides and applies (see `academic-regulations.md`).
* **Imports and exports** – every export offers CSV or Excel (.xlsx); imports accept either. Students,
  courses (with prerequisites and curriculum placement) and staff accounts are imported with a
  check-then-confirm step, all-or-nothing. Lecturers download a marks template for their class and
  upload completed marks, which are checked and previewed before being saved. Imported staff receive
  an email link to set their own password.
* **Documents** – registration slips, result slips and transcripts are available as server-generated
  PDFs and as printable pages.
* **Navigation** – the sidebar scrolls independently, keeps the current page in view, and its groups
  can be collapsed (remembered per browser).
* **Audit** – append-only log of sensitive actions with before/after values, viewer for authorised roles.
* **Frontend** – responsive app shell (sidebar, mobile drawer, breadcrumbs, current period,
  account menu, light/dark), role-specific dashboards from live data, all screens listed in the
  brief, loading/empty/error states, confirmation dialogs with reasons for sensitive actions.
* **Student photos** – shown on the student's record, dashboard and profile, in the student list and
  on class lists. Uploaded one at a time or in bulk (files, a folder or a ZIP), with each file matched
  to a student by its number while ignoring punctuation and case. Photos are converted to WebP,
  cropped to passport shape and stored privately.
* **Numbering** – `{YEAR:02}` gives the last two digits of the year (26 for 2026) in student number
  and transcript serial formats.
* **Official transcripts** – numbered transcripts with a QR verification code and a public
  verification page, printed for hand signing and stamping or with an uploaded signature and seal.
  Interim labelling, an issuance register with reprint and revoke, and a per-registrar permission
  controlled by administrators. See [transcripts.md](transcripts.md).
* **Bulk actions and deleting records created in error** – tick students or staff accounts to change
  status, deactivate, reactivate or (administrators only) delete them in one go. Deletion is limited to
  records that have never been used (no registrations, results, status changes, sign-in or audit
  activity). Anything else is skipped with the reason and should be deactivated instead. Every deletion
  is recorded in the audit log with its reason.
* **Notifications and installable app** – in-app notifications (bell, page), web push with an
  app-icon badge and an in-app sound, and Brevo email for staff alerts and password resets, kept
  within the free plan's daily limit by a reserve for security mail and daily digests. The app is
  installable, branded from Institution settings, and has an offline page. See
  [notifications.md](notifications.md).
* **Quality** – 193 backend tests and 26 Playwright end-to-end journeys passing; ESLint clean;
  `check --deploy` clean apart from opt-in HSTS preload; `pip-audit` and `npm audit` clean.

## Known limitations

* iPhone HEIC photos are not accepted; save them as JPG first. The photo crop is centred and
  does not detect faces.

* Push notifications, the app-icon badge and Brevo delivery have been tested with mocks and in desktop
  Chromium only. Check them on real Android and iPhone devices, and with your Brevo account, before
  rollout.
* Push messages that are not sent straight away go out while people are using the app, or by the daily
  task. On a free PythonAnywhere account, a message created late at night may wait until morning.

* PostgreSQL support is configured and documented but **has not been tested** (no PostgreSQL server
  was available during development). Run the suite against PostgreSQL before production.
* PDF slips and transcripts are marked "unofficial": there is no signature, seal or verification code yet.
* Resit scores are entered by hand (no spreadsheet import for resits yet).
* No multi-factor authentication yet.
* Imports, exports and transcript generation run synchronously (fine for ~2,000 rows); there are no
  background workers.
* Email delivery for password reset requires SMTP configuration.
* Student photos are stored but not yet displayed or uploadable from the UI.
* A grading-scheme change does not automatically recalculate existing sheets; raw marks are kept so
  an authorised recalculation feature can be added.
* Academic regulations are one set per installation (no per-programme variations), graduation and
  award classification are not implemented, and resit grades cannot be amended after publication.
* The audit log cannot prevent changes by someone with direct database access (see `security.md`).
* Lists of registration statuses, course types and award types are fixed in code; status
  *policies* are configurable in the database (Django admin) but have no UI yet.

## Recommended next steps

1. Run the full test suite and a pilot rehearsal on PostgreSQL; set up automated daily backups.
2. Add MFA (TOTP) for staff, starting with administrators and result approvers.
3. Verification codes (QR) on PDF transcripts and slips so third parties can check authenticity.
4. UI for status policies and student photo upload.
5. Per-programme regulations, graduation audit against `required_credits` and award classification.
6. Background jobs (RQ or Celery) when imports or exports exceed a few thousand rows.
7. CI pipeline running pytest, ESLint, Playwright, `pip-audit` and `npm audit` on every change.
8. Future modules (admissions, fees, timetabling, attendance, notifications) as separate apps.
