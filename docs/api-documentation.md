# API documentation

Interactive documentation generated from the code (OpenAPI 3):

* Swagger UI: `/api/v1/docs/`
* Schema (YAML/JSON): `/api/v1/schema/` — or offline: `python manage.py spectacular --file schema.yml`

## Authentication

Session cookies, not tokens.

1. `GET /api/v1/auth/csrf/` sets the `csrftoken` cookie.
2. `POST /api/v1/auth/login/` with `{"email", "password"}` and header `X-CSRFToken: <csrftoken>`.
   Sets the HTTP-only `sis_sessionid` cookie and returns the current user.
3. Send `X-CSRFToken` on every `POST`/`PUT`/`PATCH`/`DELETE`.
4. `POST /api/v1/auth/logout/` ends the session.

Unauthenticated requests receive `403` with `code: "not_authenticated"`. Login, password reset and
password change are rate limited (`429`).

## Conventions

* Prefix `/api/v1/`, trailing slashes required.
* Lists are paginated: `?page=2&page_size=50` (max 200) →
  `{"count", "page", "page_size", "total_pages", "results": [...]}`.
* `?search=` free text, `?ordering=field` or `-field`, plus filters listed per endpoint in Swagger.
* Errors: `{"detail": "summary", "code": "machine_code", "errors": {...}}`.
  Validation errors put field messages in `errors`; business-rule errors (for example a failed
  registration) put a list of `{offering, course_code, message}` in `errors`.
* Status codes: 200/201/204 success, 400 validation or rule failure, 403 not allowed, 404 not found
  *or outside your scope*, 405 method not allowed, 409 conflict (locked/protected record), 429 throttled.
* Exports return CSV by default; add `?file_format=xlsx` for Excel (class lists use `?export=csv|xlsx`).
  Cells beginning with `= + - @` are escaped so spreadsheets never run them as formulas.
* Imports accept CSV (UTF-8) or Excel (.xlsx) uploads as `multipart/form-data` field `file`
  (max 5 MB). `…/import/preview/` checks the file and saves nothing; `…/import/confirm/` with the
  returned `rows` creates every row or none; `…/import/template/` downloads a template.
* PDFs (`application/pdf`): `registrations/{id}/slip-pdf/`, `my-results/slip-pdf/?semester=`,
  `my-results/transcript-pdf/`, `students/{id}/transcript-pdf/?published_only=false`.

## Endpoint overview

| Area | Endpoints | Who |
|---|---|---|
| Auth | `auth/csrf/`, `auth/login/`, `auth/logout/`, `auth/me/` (GET, PATCH phone), `auth/password/change/`, `auth/password/reset/`, `auth/password/reset/confirm/` | Everyone |
| Staff accounts | `users/` (list/create/update), `users/{id}/activate/`, `deactivate/`, `set-password/`, `users/lecturers/` | Admin (lecturer lookup: registry, results officer) |
| Institution | `institution/` (public branding), `institution/settings/` (GET, PATCH multipart for logo) | Public / Admin |
| Academic periods | `academic-years/`, `semesters/`, `semesters/{id}/set-current/`, `academic/current-period/` | Staff read; admin/registrar write |
| Structure | `departments/`, `levels/`, `programmes/`, `courses/`, `programme-courses/` | Staff read; admin write |
| Offerings | `course-offerings/`, `course-offerings/{id}/lecturers/` (PUT), `course-offerings/generate/` | Registry write; lecturers see only assigned |
| Students | `students/`, `students/{id}/change-status/`, `change-placement/`, `activate/`, `deactivate/`, `history/`, `academic-record/`, `students/me/`, `students/export/`, `students/import/preview/`, `students/import/confirm/`, `students/import/template/` | Registry full; results officer/lecturer limited view; student `me` only |
| Registration | `registrations/` (list/detail), `registrations/current/` + `registrations/submit/` (student), `registrations/options/?student=`, `registrations/on-behalf/`, `registrations/{id}/review/`, `registrations/export/`, `registrations/not-registered/`, `registrations/statistics/`, `class-lists/{offering}/` (`?export=csv`) | As named |
| Imports | `students/import/…`, `courses/import/…` (admin), `users/import/…` (admin, emails set-up links), `result-sheets/{id}/marks-template/`, `result-sheets/{id}/marks-import/` (check only; apply with `PUT marks/` and `"source": "import"`) | As named |
| Grading | `grading-schemes/`, `grading-schemes/{id}/set-default/` | Staff read; admin write |
| Results | `result-sheets/`, `result-sheets/for-offering/{offering}/`, `result-sheets/{id}/components/` (PUT), `marks/` (PUT), `submit/`, `return/`, `approve/`, `publish/`, `export/` | Assigned lecturer / users with capabilities |
| Amendments | `result-amendments/` (list/create), `result-amendments/{id}/review/` | Capability holders |
| Student results | `my-results/`, `my-results/slip/?semester=` | Student (published only) |
| Regulations | `academic-regulations/` (GET, PATCH) | Staff read; admin write |
| Resits | `resit-sheets/`, `resit-sheets/open/`, `resit-sheets/{id}/register/`, `scores/` (PUT), `submit/`, `return/`, `approve/`, `publish/`; `my-resits/`, `my-resits/{id}/register/` | Results staff / assigned lecturer / student |
| Progression | `progression-reviews/`, `progression-reviews/generate/`, `progression-reviews/{id}/apply/`, `progression-decisions/?review=`, `progression-decisions/{id}/override/`, `exclude/` | Registry write; results officer read |
| Audit | `audit-logs/` (read-only) | Admin all; registrar and results officer scoped |
| Dashboard | `dashboard/` | Everyone (role-specific payload) |

## Examples

Submit a registration (student):

```http
POST /api/v1/registrations/submit/
X-CSRFToken: …
{"offering_ids": [12, 13, 15]}
```

Failure (nothing is saved):

```json
{
  "detail": "Registration could not be completed.",
  "code": "registration_invalid",
  "errors": [
    {"offering": 15, "course_code": "CS201", "message": "Prerequisite not met: CS101."},
    {"offering": 13, "course_code": "MATH101", "message": "This is a compulsory course for your level and must be registered."}
  ]
}
```

Save draft marks (assigned lecturer):

```http
PUT /api/v1/result-sheets/7/marks/
{"marks": [{"registration": 41, "component": 3, "score": "35.5"}, {"registration": 41, "component": 4, "score": null}]}
```

Result workflow: `submit` (lecturer) → `approve` or `return` (needs `can_approve_results`) →
`publish` (needs `can_publish_results`). Published sheets reject edits with `409`.
