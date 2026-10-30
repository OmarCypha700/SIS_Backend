# Security and data protection

## Authentication design

* Django sessions with secure, **HTTP-only** cookies (`sis_sessionid`) and CSRF protection.
  No tokens are stored in `localStorage`.
* The Next.js frontend proxies `/api/*` to Django (`rewrites` in `next.config.mjs`). The browser
  therefore only talks to the frontend origin and every cookie is first-party with
  `SameSite=Lax`. This avoids third-party-cookie problems between Vercel and PythonAnywhere
  without weakening cookie settings.
* `CSRF_TRUSTED_ORIGINS` must contain the frontend origin (e.g. `https://sis.example.edu`).
  Login itself is CSRF-protected (prevents login CSRF).
* Passwords use Django's PBKDF2 hasher and validators (minimum 10 characters, not common,
  not numeric-only, not similar to the user's details).
* Sessions expire after 8 hours of inactivity (`SESSION_COOKIE_AGE`, sliding).
* Rate limits: login 10/min, password reset 5/hour, password change 10/hour per client, plus
  login 20/hour and password reset 3/hour per email address. The client address comes from
  `X-Forwarded-For` only as far as `NUM_PROXIES` trusted proxies, so clients cannot choose it.
* Password reset email is sent at most once per 15 minutes and 3 times a day per person, so repeated
  requests cannot use up the shared daily email allowance.
* Login failures return one generic message and are audited (attempted email only).
* Password reset tokens are single-use and expire after 2 hours; the reset endpoint always
  returns the same response so it cannot be used to discover accounts.
* Imports can generate a random temporary password per person (12 characters from an unambiguous
  set, about 70 bits), shown once for printing as sign-in slips or downloading as CSV, and never
  stored or logged. An optional `password` column is checked against the same validators.
* Anyone with a temporary password (import, or set by an administrator) must choose their own
  before using the API: `PasswordChangeRequiredMiddleware` answers every other API request with
  403 `password_change_required`, and the app shows only the change-password screen.
* After sign-in the app only follows `?next=` to a path on its own origin.
* Push subscriptions are accepted only for the browsers' push services (`PUSH_ALLOWED_HOSTS`),
  at most 10 devices per person; delivery never follows redirects.
* The password-reset email is sent after the response has gone out, so response time does not
  reveal whether an account exists.
* The API documentation (`/api/v1/docs/`, `/api/v1/schema/`) is for administrators only, and its
  Swagger UI files come from a fixed release (`swagger-ui-dist@5.17.14`), never `@latest`.
* Logos, transcript signatures and seals must be under 12 megapixels; app icons are drawn once per
  branding change and cached, so anonymous icon requests never decode the logo.
* Spreadsheet uploads are read row by row with hard limits on rows and columns, so a small file
  that declares a huge sheet cannot exhaust memory. Export headers are escaped like cells.
* Multi-factor authentication is not implemented yet (recommended next step for staff).

## Authorisation

* Deny by default: `DEFAULT_PERMISSION_CLASSES = IsAuthenticated`, and every view declares the
  roles allowed per action (`common/permissions.py`).
* Object scope is enforced in querysets: students only see their own records; lecturers only see
  offerings they are assigned to and students registered on them; out-of-scope ids return 404.
* Lecturers and results officers receive a reduced student serializer without personal details.
* Privileged result actions are separate per-user capabilities: approve results, publish results,
  request amendments, approve amendments. Approval and publication can be held by different people.
  By default the approver must differ from the submitter, and an amendment's approver must differ
  from its requester. *Institution settings* can also require that the publisher differs from the
  submitter. This is off by default: it suits institutions where registrars also teach.
* When staff who approve or publish also teach, the submitter may be the only person holding the
  next permission (for example, the only results officer submits their own course). The system
  then shows a "Sign-off cannot continue yet" warning on the sheet, notifies administrators, and
  lists the sheet under *Sign-off needs attention* on the registry and results-officer
  dashboards. Keep at least two people able to approve and two able to publish, for example the
  Results Officer and the Vice-Principal (Academic) for approval.
* Student photos are personal data. They are stored in `PRIVATE_MEDIA_ROOT`, never at a public
  address, and served only through `/api/v1/students/<id>/photo/` to the student themself, registry
  staff, results officers, and lecturers of courses the student is registered on. Uploads are
  re-encoded as WebP, which removes hidden data such as GPS location and camera details. Only the
  registry can add, replace or remove photos, and every change is audited.
* Role and capability fields are never writable through self-service endpoints; changing your own
  role or deactivating yourself is refused. `is_staff` / `is_superuser` are not exposed by the API.
* Django admin (`/django-admin/`) is for superusers only and disabled in production unless
  `DJANGO_ADMIN_ENABLED=True`.

## Audit logging

`apps/audit` records actor, action, entity, before/after values (changed fields only), reason,
IP address, request id and timestamp for: sign-in/out and failures, password events, staff
account and role changes, student creation/changes/status/placement/deactivation/import/export,
registration submissions, on-behalf registrations, approvals and exports, assessment configuration,
mark saves (with old and new values), result submission/return/approval/publication/export,
amendment requests, approvals and rejections, institution and academic structure changes.

Keys containing `password`, `token`, `secret`, `session`, `csrf` or `otp` are redacted.

**Immutability limits.** The ORM refuses to update or delete audit rows and the API is read-only,
but anyone with direct database access can still alter them. In production: give the application
database user only the privileges it needs, restrict who holds owner credentials, back up the audit
table with the rest of the database, and, for stronger guarantees, revoke `UPDATE`/`DELETE` on
`audit_auditlog` for the application role in PostgreSQL or ship logs to write-once storage.

## Web security settings (production)

`config/settings/prod.py`: `DEBUG=False` enforced, strong secret key required, explicit
`ALLOWED_HOSTS`, HTTPS redirect, HSTS (preload opt-in via `SECURE_HSTS_PRELOAD`), secure cookies,
`X-Frame-Options: DENY`, `nosniff`, `same-origin` referrer policy. CORS is restricted to configured
origins (only needed if the API is called cross-origin). The frontend sends the same security
headers. `python manage.py check --deploy --settings=config.settings.prod` passes apart from the
deliberate HSTS-preload opt-in.

Other protections: ORM parameterised queries (no raw SQL), React output encoding, uploads limited
to PNG/JPG/WebP images ≤ 2 MB and UTF-8 CSV ≤ 5 MB / 2,000 rows, CSV exports escape formula
characters, generic error messages for server errors, request correlation ids.

Dependency checks: `pip-audit -r requirements.txt` and `npm audit --omit=dev` (both clean when
last run). Run them before each release.

## Data protection (Ghana Data Protection Act, 2012 — Act 843)

This section describes how the software supports compliance; it is not legal advice, and
infrastructure separation alone does not make an institution compliant. Each institution (the
data controller) should register with the Data Protection Commission and put the policies below
in writing with the vendor (the data processor).

| Topic | How the system supports it | Institution / vendor responsibility |
|---|---|---|
| Minimisation | Only name, contact, programme and academic data are required; date of birth, gender, address and emergency contact are optional | Decide which optional fields are justified and leave the rest empty |
| Authorised access | Role-based access, scoped querysets, reduced views for lecturers | Assign roles carefully; review staff accounts each semester |
| Accountability | Audit log of sensitive actions and exports | Review the log; investigate unusual exports |
| Retention | Records are deactivated, not deleted, preserving academic history | Define how long records are kept after graduation/withdrawal; purge per policy using a controlled script |
| Portability / access requests | CSV export of student records; students can view their own data and results; transcripts are printable | Respond to subject access requests within statutory time limits |
| Backups | See `deployment.md` | Encrypt backups, restrict access, test restores |
| Incidents | Audit log + server logs support investigation | Agree an incident response plan and notification duties between institution and vendor |
| Vendor access | Separate installation per institution; vendor uses named accounts | Contract defines vendor access, sub-processors, hosting location and termination |
| Termination | Full database export (`dumpdata` or `pg_dump`) and media folder | On contract end: hand over data, then delete vendor copies and confirm in writing |

Never use real student records in development or demos without authorisation; use `seed_demo`.
