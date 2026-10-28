# Deployment guide

Replace everything in `<angle brackets>` with your own values. Paths below are examples.

## 1. Prerequisites

* Python 3.12+ and Git on the server (PythonAnywhere provides both).
* Node.js 20+ for building the frontend (Vercel provides it).
* A domain for the frontend (optional; Vercel gives you `*.vercel.app`).
* For production: a PostgreSQL database (PythonAnywhere paid plans include one, or any managed PostgreSQL).

## 2. Environment variables (backend)

Copy `.env.example` to `.env` next to `manage.py` and set:

| Variable | Development | Production |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` | `config.settings.prod` (set in the WSGI file) |
| `DJANGO_SECRET_KEY` | any | long random value: `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `True` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | `<username>.pythonanywhere.com` |
| `DATABASE_URL` | empty (SQLite) | `postgres://<user>:<password>@<host>:<port>/<db>` |
| `FRONTEND_URL` | `http://localhost:3000` | `https://<frontend-domain>` (used in reset emails) |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:3000` | `https://<frontend-domain>` |
| `CORS_ALLOWED_ORIGINS` | unused with the proxy | leave empty unless something calls the API cross-origin |
| `EMAIL_BACKEND` | console backend | `anymail.backends.brevo.EmailBackend` |
| `BREVO_API_KEY` | empty | Brevo → *SMTP & API* → *API keys* (keep secret) |
| `DEFAULT_FROM_EMAIL` | any | a sender verified in Brevo, e.g. `SIS <no-reply@your-college.edu.gh>` |
| `EMAIL_DAILY_LIMIT`, `EMAIL_SECURITY_RESERVE` | 300, 60 | Brevo free plan: 300 (lower it if you also send campaigns) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | empty (push off) | from `python manage.py generate_vapid_keys`; subject `mailto:<it contact>` |
| `DJANGO_ADMIN_ENABLED` | `True` | `False` (enable temporarily if needed) |
| `MEDIA_ROOT` | `./media` | e.g. `/home/<username>/sis-media` |

Never commit `.env`.

## 3. Backend on PythonAnywhere

1. **Upload the code.** Open a Bash console:
   ```bash
   git clone <your-backend-repo-url> ~/sis-backend
   cd ~/sis-backend
   python3.12 -m venv ~/.virtualenvs/sis
   source ~/.virtualenvs/sis/bin/activate
   pip install -r requirements.txt
   cp .env.example .env && nano .env      # fill in production values (section 2)
   ```
   `requirements.txt` includes `openpyxl` (Excel) and `reportlab` (PDF); both install on
   PythonAnywhere without extra system packages.
2. **Database and static files.**
   ```bash
   export DJANGO_SETTINGS_MODULE=config.settings.prod
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py create_institution_admin --email <you@institution.edu> --first-name <First> --last-name <Last>
   ```
3. **Web app.** Web tab → *Add a new web app* → *Manual configuration* → Python 3.12.
   * *Virtualenv*: `/home/<username>/.virtualenvs/sis`
   * *Source code*: `/home/<username>/sis-backend`
   * *Static files*: URL `/static/` → `/home/<username>/sis-backend/staticfiles`;
     URL `/media/` → your `MEDIA_ROOT`.
   * *Security*: enable **Force HTTPS**.
4. **WSGI file** (link on the Web tab) — replace its contents with:
   ```python
   import os, sys
   path = "/home/<username>/sis-backend"
   if path not in sys.path:
       sys.path.insert(0, path)
   os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"
   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```
   The settings read `.env` from the project folder automatically.
5. **Reload** the web app and open `https://<username>.pythonanywhere.com/api/v1/institution/`.
   You should see JSON. Errors are in the *error log* linked on the Web tab.
6. **Email (Brevo).** Create a free Brevo account and set it up:
   * Under *Senders, Domains & Dedicated IPs*, verify the sender address.
   * Authenticate your domain with the DKIM and SPF records Brevo shows, so mail is not marked as spam.
   * Create an API key under *SMTP & API → API keys*.
   * Put the backend, key and verified sender in `.env` (section 2) and reload.
   * Test it with *Forgot password*. Brevo's *Transactional → Logs* shows each email.
7. **Push notifications.** Run `python manage.py generate_vapid_keys`, copy the three lines into
   `.env`, and reload. Generate the keys once: changing them later means everyone has to turn push on again.
8. **Scheduled task (required).** On the *Tasks* tab, add a daily task at 06:00 UTC:
   ```bash
   cd ~/sis-backend && ~/.virtualenvs/sis/bin/python manage.py send_pending_messages --settings=config.settings.prod
   ```
   It sends whatever did not go out straight away and merges waiting alert emails into digests.
   A free account allows one daily task. On a paid account, run it hourly.
   See [notifications.md](notifications.md).

SQLite on PythonAnywhere is acceptable only for demonstrations. Use PostgreSQL (section 6) for pilots.

## 4. Frontend on Vercel (or any Node.js host)

1. Push the frontend repository to GitHub/GitLab and import it in Vercel (framework: Next.js; defaults are fine).
2. Environment variable: `BACKEND_URL = https://<username>.pythonanywhere.com` (no trailing slash).
   It is read at **build** time for the `/api` and `/media` rewrites — redeploy after changing it.
3. Deploy, then set the backend's `CSRF_TRUSTED_ORIGINS` and `FRONTEND_URL` to the Vercel URL
   (or your custom domain) and reload the backend.

Other hosts: `npm ci && BACKEND_URL=... npm run build && npm start` (port 3000 by default; put
HTTPS in front). PythonAnywhere is not used for the frontend: Next.js needs a Node.js server.

## 5. Connecting the two

The browser only talks to the frontend; the frontend proxies `/api/*` and `/media/*` to Django.
Checklist when sign-in fails:

* `CSRF_TRUSTED_ORIGINS` contains the exact frontend origin, including `https://` and no trailing slash.
* `DJANGO_ALLOWED_HOSTS` contains the backend host name.
* `BACKEND_URL` was set before the last frontend build.
* Both sites use HTTPS in production (secure cookies are not sent over HTTP).

## 6. Moving from SQLite to PostgreSQL

1. Create an empty PostgreSQL database and user (the user should own only this database).
2. Export from SQLite (backend folder, old settings):
   ```bash
   python manage.py dumpdata --natural-foreign --natural-primary \
     --exclude contenttypes --exclude auth.permission --exclude sessions \
     --indent 2 -o sis-export.json
   ```
3. Point `DATABASE_URL` at PostgreSQL, then:
   ```bash
   python manage.py migrate
   python manage.py loaddata sis-export.json
   ```
4. Verify: sign in as each role, compare record counts (students, registrations, published
   results), and run `pytest` against PostgreSQL (`docs/testing.md`).
5. Keep the SQLite file as a read-only backup until the PostgreSQL deployment is accepted.

`AuditLog` rows load normally (inserts are allowed; updates are not).

## 7. Backups

SQLite (development/demo):
```bash
mkdir -p backups
python -c "import sqlite3,datetime; s=sqlite3.connect('db.sqlite3'); d=sqlite3.connect(f'backups/sis-{datetime.date.today()}.sqlite3'); s.backup(d)"
# restore: stop the app, replace db.sqlite3 with the backup file, start the app
```
PostgreSQL:
```bash
pg_dump --format=custom --file=sis-$(date +%F).dump "$DATABASE_URL"
pg_restore --clean --if-exists --dbname="$DATABASE_URL" sis-YYYY-MM-DD.dump
```
Also back up `MEDIA_ROOT` (logos, photos). Encrypt backups, store them off the server, restrict
access, and test a restore at least once per semester. PythonAnywhere scheduled tasks can run the
backup command daily.

## 8. A new institution (dedicated installation)

For each institution, from the same code:

1. New backend web app (or new PythonAnywhere account / cloud project owned by the institution),
   new database, new `MEDIA_ROOT`, new `.env` with its own secret key and hosts.
2. `migrate`, `collectstatic`, `create_institution_admin`.
3. New frontend deployment with `BACKEND_URL` pointing to that backend.
4. The institution's administrator signs in and configures *Institution settings* (name, logo,
   colour, student-number format, policies), grading scheme(s), levels, departments, programmes,
   courses, curriculum, academic year and semesters, then staff accounts.
5. Import students with the CSV template.

Never share databases, secret keys or media folders between institutions.

## 9. Updating safely

1. Back up the database and media (section 7).
2. Backend: `git pull`, `pip install -r requirements.txt`, `python manage.py migrate`,
   `python manage.py collectstatic --noinput`, reload the web app.
3. Frontend: push to the deployment branch (Vercel builds automatically).
4. Smoke test: sign in, open dashboard, students list, a result sheet.
5. Roll back by redeploying the previous commit and restoring the backup if a migration failed.

Read migration files before applying them to production; test on a copy of production data first.

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ImproperlyConfigured: Set a strong DJANGO_SECRET_KEY` | `.env` missing or not in the project folder; key shorter than 40 characters |
| `DisallowedHost` / 400 Bad Request | Add the host to `DJANGO_ALLOWED_HOSTS` and reload |
| Sign-in works but every save returns 403 "CSRF" / "Origin checking failed" | Add the frontend origin to `CSRF_TRUSTED_ORIGINS` |
| Frontend shows "Cannot reach the server" | `BACKEND_URL` wrong or backend down; rebuild frontend after fixing |
| Redirect loop or 404 on API calls | Remove trailing-slash rewrites in front of Next.js; keep `skipTrailingSlashRedirect` |
| Static admin files missing | Run `collectstatic`; check the `/static/` mapping |
| Logo not shown | Check the `/media/` mapping and `MEDIA_ROOT` permissions |
| "database is locked" on SQLite | Too many simultaneous writers: move to PostgreSQL |
| Password reset email not received | Configure `EMAIL_*`; in development, emails print to the console |
| "No default grading scheme is configured" | Create a grading scheme and mark it default (Administration → Grading schemes) |
