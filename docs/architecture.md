# Architecture

## Overview

```
Browser ──HTTPS──▶ Next.js (frontend host, e.g. Vercel)
                     │  pages, React UI
                     │  /api/* and /media/* rewritten (proxied) ──▶ Django REST API (PythonAnywhere)
                     │                                                 │
                     └─ session + CSRF cookies are first-party         ├─ SQLite (dev / demo)
                        on the frontend domain                         └─ PostgreSQL (production)
```

* **Frontend** – Next.js App Router, JavaScript only, Tailwind CSS, shadcn/ui (Radix),
  React Hook Form + Zod, Axios, Sonner, Recharts. Pages are thin; data comes from the API.
* **Backend** – a modular monolith: one Django project, one app per domain, business rules in
  `services.py` modules, thin DRF views.
* **One installation per institution.** Each college or university gets its own deployment and
  database. There is no tenant column in the data model (a deliberate decision); isolation comes
  from separate infrastructure.

## Backend modules

| App | Responsibility | Key services |
|---|---|---|
| `institutions` | Single-row `InstitutionProfile` (identity, branding, policy flags), student-number sequences | `next_student_number` |
| `accounts` | Custom `User` (email login), roles, result capabilities, auth endpoints, staff management | — |
| `academics` | Academic years, semesters (any number per year), departments, levels, programmes, courses, prerequisites, curriculum (`ProgrammeCourse`), course offerings, lecturer assignment | `generate` action creates offerings from curriculum |
| `students` | Student records, status policies, placement/status history, CSV import/export | `create_student`, `change_status`, `change_placement`, `validate_import_rows`, `import_students` |
| `registration` | Semester registrations, course registrations, registration events, class lists | `build_options` (eligibility), `submit_registration`, `review_registration` |
| `results` | Grading schemes and bands, assessment components, marks, result sheets and workflow, computed course results, amendments, GPA/CGPA | `grading.py` (pure maths), `services.py` (workflow) |
| `progression` | End-of-semester progression reviews: proposals from the academic regulations, registry overrides, application | `evaluate`, `generate_review`, `override_decision`, `apply_review` |
| `audit` | Append-only audit log, auth event signals, request correlation | `log_action`, `AuditedModelMixin` |
| `dashboard` | Read-only aggregates per role | — |

Rules live in exactly one place. For example, `registration.services.build_options` decides
eligibility both for displaying choices and when validating a submission, so the screen and the
server cannot disagree.

## Request flow

1. The browser calls `/api/v1/...` on the frontend origin; Next.js forwards it to Django.
2. `RequestContextMiddleware` assigns a request id (returned as `X-Request-ID`) used by the audit log.
3. DRF authenticates via the Django session cookie and enforces CSRF on unsafe methods.
4. `RoleBasedPermission` checks the view's declared `read_roles` / `write_roles` / `action_roles`
   (deny by default). Object scope is applied in `get_queryset`, so records outside a user's scope
   return 404.
5. Views validate input with serializers and call service functions inside transactions.
6. Errors are returned as `{"detail", "code", "errors"}` by `common.exceptions.api_exception_handler`.

## Configuration and environments

`config/settings/base.py` holds shared settings; `dev.py`, `test.py` and `prod.py` override them.
All deployment-specific values come from environment variables (`.env.example`). `prod.py`
refuses to start without a strong secret key, `DEBUG=False` and explicit allowed hosts.

## Institution-specific behaviour

Configurable without code changes: name, logo, colour, contact details, time zone, student-number
format, academic-year naming, term structure, levels, programmes, credit limits, grading schemes
(boundaries, points, rounding), status policies (who may register / see results), registration
policies (higher-level courses, retakes, cross-programme electives, credit overload, approval) and
result policies (separate approver, second approver for amendments, CGPA retake rule), and academic
regulations (resits, retake caps, attempt limits, probation, promotion, dismissal — see
`academic-regulations.md`).

## Deployment models

* **Vendor-managed dedicated hosting** – the vendor runs one backend + database + storage per
  institution from the same codebase (see `deployment.md`, "A new institution").
* **Institution-controlled hosting** – the institution owns the cloud account; the vendor is
  granted scoped access to deploy updates. The application is identical; only who holds the
  credentials changes.

Infrastructure separation alone is not compliance: contracts, access policies, retention and
incident procedures are also required (see `security.md`).

### If a shared multi-tenant model is ever needed

Add an `institution` foreign key to institution-owned models, scope every queryset through a
manager keyed on the authenticated user's institution, make uniqueness constraints
`(institution, code)`, and add a control plane for provisioning. The service-layer design keeps
this change contained, but it is a significant migration and should be justified first.

## Future modules

New domains (admissions, fees, timetabling, attendance, HR, library, hostels, LMS integration,
notifications, alumni) should be new Django apps that reference `students.Student`,
`academics.*` and `accounts.User`. Examples of intended hooks:

* Admissions creates `Student` records with status `APPLICANT` through `students.services.create_student`.
* Fees can block registration by adding a check in `registration.services.build_options`.
* Bulk imports, transcript PDFs and notifications are candidates for a background worker
  (Celery or RQ with Redis) once volumes justify it; today they run synchronously.

No microservices, message brokers or search clusters are used; none are needed at this scale.
