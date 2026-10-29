# Core SIS — Backend (Django REST API)

School Management System for colleges, nursing and midwifery training institutions,
technical institutions and universities. This repository is the API: student records,
academic structure, course registration, assessment and results, and audit logging.

The web interface lives in the sibling **SIS Frontend** project (Next.js). One deployment
serves exactly one institution, with its own database.

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Modules, request flow, deployment model, future modules |
| [docs/database-design.md](docs/database-design.md) | Entities, constraints, SQLite vs PostgreSQL |
| [docs/api-documentation.md](docs/api-documentation.md) | Endpoint overview, auth, errors, pagination |
| [docs/security.md](docs/security.md) | Auth design, permissions, audit, data protection (Ghana) |
| [docs/testing.md](docs/testing.md) | Running backend and end-to-end tests |
| [docs/deployment.md](docs/deployment.md) | PythonAnywhere, Vercel, PostgreSQL, new institutions, backups, updates, troubleshooting |
| [docs/academic-regulations.md](docs/academic-regulations.md) | Resits, retakes, progression, probation and dismissal rules |
| [docs/transcripts.md](docs/transcripts.md) | Official transcripts: issuing, signing, verification, permissions |
| [docs/notifications.md](docs/notifications.md) | In-app, push and email notifications, Brevo limits, the installable app |
| [docs/status.md](docs/status.md) | What is done, known limitations, next steps |

## Quick start (local development)

Prerequisites: Python 3.12+, Git. (Node.js 20+ for the frontend.)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

# 2. Install dependencies (requirements-dev.txt also installs the test tools)
pip install -r requirements-dev.txt

# 3. Configure the environment
cp .env.example .env            # Windows: copy .env.example .env
#    The defaults use SQLite and are fine for development.

# 4. Create the database
python manage.py migrate

# 5a. EITHER load fictitious demo data (recommended for a first look)...
python manage.py seed_demo
# 5b. ...OR create your first administrator for a clean installation
python manage.py create_institution_admin --email you@example.edu --first-name Ama --last-name Mensah

# 6. Run the API on http://127.0.0.1:8000
python manage.py runserver
```

Then start the frontend (see its README) and open http://localhost:3000.

### Demo accounts (`seed_demo`)

`seed_demo` creates a fictitious institution ("Northridge College … (Demo)") and these
accounts, all with the password `Demo-Password-2026` (override with the `DEMO_PASSWORD`
environment variable before seeding). They exist only in your local database.

| Role | Email |
|---|---|
| Administrator | admin@northridge.example |
| Registrar | registrar@northridge.example |
| Results officer | exams@northridge.example |
| Lecturer | lecturer@northridge.example |
| Student | student@northridge.example |

The command refuses to run when `DEBUG` is off or when the database already contains
academic data. Never run it against a database holding real records.

### Useful URLs

- API root: `http://127.0.0.1:8000/api/v1/`
- Interactive API docs (OpenAPI/Swagger): `http://127.0.0.1:8000/api/v1/docs/`
- OpenAPI schema: `http://127.0.0.1:8000/api/v1/schema/`
- Django admin (superusers only, enabled in development): `http://127.0.0.1:8000/django-admin/`

## Running tests

```bash
pytest                    # 128 tests: permissions, workflows, calculations, regulations, imports, PDFs, audit
python manage.py makemigrations --check --dry-run
```

End-to-end browser tests live in the frontend project (`npx playwright test`).

## Project layout

```
config/            settings (base/dev/prod/test), URLs, WSGI
common/            shared helpers: permissions, pagination, errors, CSV, middleware
apps/
  institutions/    institution profile, academic regulations, student-number generation, seed_demo
  accounts/        custom user, roles, result capabilities, auth endpoints, staff accounts
  academics/       academic years, semesters, departments, levels, programmes, courses, offerings
  students/        student records, status policies, history, CSV import/export
  registration/    semester registration rules and workflow, class lists
  results/         grading schemes, assessments, marks, approval workflow, amendments, resits, GPA/CGPA
  progression/     end-of-semester progression reviews (standing, promotion, dismissal)
  audit/           append-only audit log
  dashboard/       role-specific dashboard figures
docs/              documentation
```
