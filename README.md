# SIS — Backend (Django REST API)

**Proprietary and confidential.** Copyright © 2026 Alfa Calix Enterprise. All rights reserved. Use,
copying or distribution without written permission is prohibited: see [LICENSE](LICENSE).

SIS is a student information system for colleges, nursing and midwifery training institutions,
technical institutions and universities. This repository is the API: institution set-up, academic
structure, students, registration, results and regulations, fees and payments, official transcripts,
notifications and the audit log. The web portal is the **SIS Frontend** repository (Next.js). Each
institution has its own installation and database.

* **Documentation:** [docs/README.md](docs/README.md), grouped by audience (operations, security,
  reference, developers).
* **Release notes, limitations and roadmap:** [CHANGELOG.md](CHANGELOG.md).

## Local development

Prerequisites: Python 3.12+, Git; Node.js 20+ for the frontend.

```bash
python -m venv .venv
.venv\Scripts\activate              # Windows
source .venv/bin/activate           # macOS / Linux
pip install -r requirements-dev.txt # includes the test tools
cp .env.example .env                # Windows: copy .env.example .env  (defaults: SQLite, console email)
python manage.py migrate
python manage.py seed_demo          # fictitious demo data (or create_institution_admin for a clean start)
python manage.py runserver          # http://127.0.0.1:8000
```

Then start the frontend (see its README) and open http://localhost:3000.

### Demo accounts (development only)

`seed_demo` creates a fictitious institution with these accounts, all with the password
`Demo-Password-2026` unless you set `DEMO_PASSWORD` before seeding:

| Role | Email |
|---|---|
| Administrator | admin@northridge.example |
| Registrar | registrar@northridge.example |
| Results officer | exams@northridge.example |
| Accounts officer | accounts@northridge.example |
| Lecturer | lecturer@northridge.example |
| Student | student@northridge.example |

The command refuses to run when `DEBUG` is off or the database already holds academic data. **Any
demo reachable by others must be seeded with its own `DEMO_PASSWORD`**
([docs/operations/demo-hosting.md](docs/operations/demo-hosting.md)). Never run it against real records.

### Useful URLs (development)

* API root: `http://127.0.0.1:8000/api/v1/`
* API documentation (administrators only): `http://127.0.0.1:8000/api/v1/docs/`
* Django admin (superusers; development only by default): `http://127.0.0.1:8000/django-admin/`

## Tests

```bash
pytest
python manage.py makemigrations --check --dry-run
```

End-to-end tests run from the frontend repository. See [docs/developers/testing.md](docs/developers/testing.md).

## Project layout

```
config/            settings (base, dev, prod, test), URLs, WSGI
common/            shared helpers: permissions, pagination, errors, bulk actions, spreadsheets, PDFs, middleware
apps/
  institutions/    institution profile and branding, app icons, academic regulations, seed_demo
  accounts/        users, roles, appointments, sign-in security, passkeys, signed-in devices
  academics/       years, semesters, departments, levels, programmes, courses, offerings
  students/        student records, photos, status policies, history, imports and exports
  registration/    registration rules and workflow, bulk registration, class lists
  results/         grading, assessments, marks, workflow, amendments, resits, GPA/CGPA
  progression/     progression reviews (standing, promotion, repeating, dismissal)
  finance/         clearance, fee schedules, student ledger, payments, bank-statement import, late penalty
  transcripts/     official transcripts and verification
  notifications/   in-app, push and email notifications
  audit/           append-only audit log
  dashboard/       role-specific dashboard figures
docs/              documentation
```
