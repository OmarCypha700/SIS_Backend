# Testing

## Backend (pytest)

```bash
pip install -r requirements-dev.txt
pytest                         # all tests
pytest apps/registration -q    # one module
pytest -k amendment            # by name
```

Uses `config.settings.test` (in-memory SQLite, fast password hashing, in-memory email).
Shared fixtures in `conftest.py` build a small institution (`world` fixture) and users for each role.

Last full run: **128 passed**. Coverage by area:

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
| Seed | demo builds consistently; refuses without DEBUG or on a non-empty database |

Also run before releases:

```bash
python manage.py makemigrations --check --dry-run
python manage.py spectacular --file schema.yml     # must produce no warnings
DJANGO_SECRET_KEY=... DJANGO_ALLOWED_HOSTS=... python manage.py check --deploy --settings=config.settings.prod
pip-audit -r requirements.txt
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

Journeys covered (last run: **14 passed**): redirect when signed out; generic login error; admin
creates a student (with validation errors) and finds it; student registers and opens the slip,
and is refused staff pages; lecturer configures assessments, enters and submits marks, API refuses
lecturer publication; results officer approves (no publish button); registrar publishes; student
sees the published grade; lecturer cannot open another lecturer's course; mobile navigation
without horizontal overflow; administrator sets academic regulations (with validation); a resit is
opened, a student registered by the registry, scored, approved, published with the grade cap; a
progression review is prepared, an outcome overridden with a reason, and the review applied; the admin
sidebar scrolls to its last item at a small window height and remembers collapsed groups; Excel export
and PDF slip downloads; a lecturer imports marks from a file (errors shown, valid rows applied);
an administrator imports courses (with curriculum placement) and staff accounts.

## PostgreSQL

The suite is database-agnostic. To run it on PostgreSQL (required before production):

```bash
# e.g. docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16
DATABASE_URL=postgres://postgres:dev@localhost:5432/sis_test pytest
```

The test database is created and destroyed automatically. **This has not yet been executed**:
the development machine used to build this release had no PostgreSQL server.
