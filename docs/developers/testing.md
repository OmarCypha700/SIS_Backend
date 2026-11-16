# Testing

Internal: for the development team. Test results are recorded per release in
[CHANGELOG.md](../../CHANGELOG.md), not here.

## Backend (pytest)

```bash
pip install -r requirements-dev.txt
pytest                         # all tests
pytest apps/registration -q    # one module
pytest -k amendment            # by name
```

Uses `config.settings.test` (in-memory SQLite, fast password hashing, in-memory email).
Shared fixtures in `conftest.py` build a small institution (`world` fixture) and users for each role.

Coverage by area:

| Area | Examples |
|---|---|
| Authentication | login/logout, generic failure messages, inactive accounts, rate limiting, CSRF on login and unsafe requests, password change/reset (token reuse rejected) |
| Permissions | unauthenticated access to every module, role escalation via `/auth/me/`, staff management limited to admins, superuser flags not exposed, student/lecturer object scope (404 outside scope), results officer read-only student view |
| Students | auto-generated numbers, duplicate email/number, required fields, invalid references, identity fields not patchable, audited status/placement changes, pagination/filter/search/sort, constant query count, deactivation blocks login, CSV import all-or-nothing, CSV formula escaping |
| Academics | date validation, single current semester, closed periods protected, unique codes, prerequisite cycles, protected deletes (409), programme-level consistency, lecturer assignment, curriculum-based generation |
| Registration | options and compulsory courses, success + events + audit, add/drop history, DB constraint on duplicates, compulsory enforcement, full rollback on invalid selection and on a failure after writes, registration window and add/drop deadline, prerequisites, passed/failed/retake policy, credit limits and overload approval, capacity, status policy, cross-programme policy, approval workflow, on-behalf with reason, student isolation, marks block dropping, reports, class-list access |
| Results | band validation (gaps/overlaps/ends/precision), exact boundaries and half-up rounding, weighted totals, GPA, CGPA retake policies, weights = 100, mark ranges, assigned-lecturer-only entry, missing marks block submission, full submit→approve→publish flow, students see only published results, immutability after publication, return/resubmit, approver ≠ submitter, entry deadline, amendment approval by a second person, amendment authorisation, status-based result access, scheme locking, dashboards |
| Audit | immutability, redaction, read-only API with role scoping, request id |
| Regulations | grade cap maths, retake cap, attempt limit, failed compulsory becomes compulsory, resit eligibility and permissions, full resit flow (coursework kept, cap applied, not-taken entries, student isolation, deadline, reason for on-behalf), progression proposals, override with reason preserved on recalculation, apply (promotion, probation, dismissal), consecutive-probation dismissal, provisional decisions skipped, applied reviews locked, permissions |
| Imports, exports, PDFs | CSV and Excel reading (BOM, blank rows, numeric cells, .xls rejected, size limit), Excel exports with formula escaping, student import from Excel, marks template, marks import preview (header matching, out-of-range, unknown students, nothing saved until applied, audit source), marks import permissions and locking, course import (prerequisites in the same file, cycles, duplicates, curriculum placement, all-or-nothing, admin only), staff import (role aliases, no passwords, set-up email links that work), forgot-password for accounts without a password, PDF slips and transcripts with access control |
| Sign-in security | growing wait after wrong passwords, alerts, passkey registration and sign-in, signed-in devices, signing out one or all other devices, password change keeping or ending other sign-ins, administrator sign-out everywhere, maximum sign-in length |
| Fees and payments | billing preview and idempotence, the registration rule (arrears, exact thresholds, per semester and per year), transaction fee gross-up and cap, payment confirmation (signature, replay, mismatch), reconciliation, bank-statement import (duplicates, re-import), reversals and waivers, late penalty, automatic clearance, student isolation |
| Transcripts and documents | issuing, frozen reprints, revoking, public verification, issuing permission; PDF watermark and stamps, signature and seal preparation, images embedded at print size |
| Notifications | who is told for each event, no personal details in previews, email allowance and digests, push subscriptions linked to devices |
| Seed | demo builds consistently; refuses without DEBUG or on a non-empty database |

Release checks (record the results in the release notes):

```bash
python manage.py makemigrations --check --dry-run
python manage.py spectacular --file schema.yml     # must produce no warnings
DJANGO_SECRET_KEY=... DJANGO_ALLOWED_HOSTS=... python manage.py check --deploy --settings=config.settings.prod
pip-audit -r requirements.txt
cd "../SIS Frontend" && npm run lint && npm audit --omit=dev
```

## End-to-end (Playwright, frontend project)

```bash
cd "../SIS Frontend"
npx playwright install chromium   # first time only
npx playwright test
```

The config starts a throw-away backend on port 8100 (`tests/e2e/start-backend.mjs` deletes
`e2e.sqlite3`, migrates and runs `seed_demo`) and a production build of the frontend on port 3100.
Set `SIS_BACKEND_DIR` / `SIS_PYTHON` if the backend lives elsewhere.

Journeys covered include: signing in and out, wrong-password waits and passkeys; signed-in devices
and signing a device out; students, imports and bulk actions (rows per page, selecting every matching
record); registration by students, on their behalf, in bulk and late with a penalty; marks entry,
import and the results workflow through publication; resits and progression reviews; financial
clearance, fees, online payment against a simulated gateway, and bank-statement import; transcripts and
verification; notifications and the installable app; and mobile layouts.

## PostgreSQL

The suite is database-agnostic. To run it on PostgreSQL (required before production):

```bash
# e.g. docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16
DATABASE_URL=postgres://postgres:dev@localhost:5432/sis_test pytest
```

The test database is created and destroyed automatically. **Required before the first production
installation and after any release that changes migrations**; record the result in the release notes.
