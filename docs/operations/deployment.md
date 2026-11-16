# Deploying a production portal

One portal serves exactly one institution, with its own backend, database, files and keys. This guide
installs one. Settings are explained in [configuration.md](configuration.md); routine care is in
[maintenance.md](maintenance.md). For a demonstration on free hosting, see
[demo-hosting.md](demo-hosting.md).

## 1. What you need

| Item | Recommendation |
|---|---|
| Backend host | A Python 3.12 host with HTTPS and a scheduled task: PythonAnywhere **paid** plan (Hacker or above), or a cloud VM / platform owned by the institution |
| Database | **PostgreSQL 14 or later**, with daily backups. SQLite is not supported in production. |
| Frontend host | Vercel (Pro plan for an institution), or any Node.js 20+ host behind HTTPS |
| Domain | The institution's own, e.g. `portal.your-college.edu.gh` for the portal |
| Email | A Brevo account with a verified sender and an authenticated domain (DKIM and SPF) |
| Online payment (optional) | A Paystack business account for the institution, settling to its bank account |
| Access | Repository access for the person deploying (a read-only deploy key or token on the server) |

Keep the institution's accounts (hosting, domain, email, payment) in the institution's name, or
record in the contract who holds them. See [../security/data-protection.md](../security/data-protection.md).

## 2. Database

Create an empty PostgreSQL database and a user that owns only this database. Note its connection URL
for `DATABASE_URL`. Before the first live installation, run the test suite against PostgreSQL
([../developers/testing.md](../developers/testing.md)).

## 3. Backend

The steps below use PythonAnywhere. On another host, the commands are the same; serve
`config.wsgi` with your WSGI server, map `/static/` and `/media/`, and force HTTPS.

1. **Code and packages.** In a Bash console:
   ```bash
   git clone <backend-repository-url> ~/sis-backend     # private repository: use a deploy key or token
   cd ~/sis-backend
   python3.12 -m venv ~/.virtualenvs/sis
   source ~/.virtualenvs/sis/bin/activate
   pip install -r requirements.txt
   cp .env.example .env && nano .env                    # production values: configuration.md
   ```
2. **Database, files and the first administrator.**
   ```bash
   export DJANGO_SETTINGS_MODULE=config.settings.prod
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py create_institution_admin --email <admin@institution.edu.gh> --first-name <First> --last-name <Last>
   ```
   The command asks for the password twice (or reads `INITIAL_ADMIN_PASSWORD` for an automated
   install; remove that variable afterwards). If you set it on the administrator's behalf, give it
   to them in person and ask them to change it under *My account* at first sign-in.
3. **Web app.** Web tab → *Add a new web app* → *Manual configuration* → Python 3.12.
   * *Virtualenv*: `/home/<user>/.virtualenvs/sis`; *Source code*: `/home/<user>/sis-backend`.
   * *Static files*: `/static/` → `/home/<user>/sis-backend/staticfiles`; `/media/` → your `MEDIA_ROOT`.
     Do **not** map `PRIVATE_MEDIA_ROOT`.
   * *Security*: turn on **Force HTTPS**.
4. **WSGI file** (linked on the Web tab):
   ```python
   import os, sys
   path = "/home/<user>/sis-backend"
   if path not in sys.path:
       sys.path.insert(0, path)
   os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"
   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```
5. **Reload**, then open `https://<backend-host>/api/v1/institution/`. You should see JSON. If not,
   check the error log on the Web tab.
6. **Security check.**
   ```bash
   python manage.py check --deploy --settings=config.settings.prod
   ```
   The only acceptable warning is HSTS preload, which is opt-in.

## 4. Email

1. In Brevo, verify the sender address and authenticate the domain with the DKIM and SPF records it
   shows, so mail is not marked as spam.
2. Create an API key (*SMTP & API → API keys*).
3. Set `EMAIL_BACKEND`, `BREVO_API_KEY` and `DEFAULT_FROM_EMAIL`, then reload.
4. Test with *Forgot password* on the sign-in page. Brevo's *Transactional → Logs* lists each email.

Students never receive alert emails, so the free daily allowance is normally enough. See
[../reference/notifications.md](../reference/notifications.md).

## 5. Push notifications

```bash
python manage.py generate_vapid_keys
```
Copy the three lines into `.env` and reload. Generate the keys once and back them up with `.env`:
new keys disconnect every device.

## 6. Scheduled task (required)

Add a scheduled task (hourly on a paid plan; at least daily):
```bash
cd ~/sis-backend && ~/.virtualenvs/sis/bin/python manage.py run_daily_tasks --settings=config.settings.prod
```
It confirms online payments still pending, sends queued email and push, builds email digests and
removes expired sign-in sessions. Details: [maintenance.md](maintenance.md).

## 7. Frontend

1. Import the frontend repository in Vercel (framework: Next.js). Connect it with the GitHub
   integration, which works with private repositories.
2. Set `BACKEND_URL = https://<backend-host>` (no trailing slash). It is read at **build** time, so
   redeploy after changing it.
3. Add the institution's domain to the Vercel project and point DNS at it.
4. On the backend, set `FRONTEND_URL` and `CSRF_TRUSTED_ORIGINS` to `https://<portal-address>`, set
   `NUM_PROXIES=2`, and reload.

On another Node.js host: `npm ci && BACKEND_URL=https://<backend-host> npm run build && npm start`, with
HTTPS in front.

The browser talks only to the portal's address; the portal forwards `/api/*` and `/media/*` to the
backend. Sign-in cookies are therefore first-party and HTTP-only.

## 8. Online payment (optional)

1. **Paystack account.** Register the institution as a business at Paystack (Ghana), with its
   registration documents and a GHS bank account. Everything below can be tested with test keys while
   approval is pending.
2. **Secret key.** Paystack → *Settings → API Keys & Webhooks*. Put the **secret** key (`sk_test_…`, later
   `sk_live_…`) in `.env` as `PAYSTACK_SECRET_KEY` and reload. Never use the public key (`pk_…`).
3. **Check it:**
   ```bash
   python manage.py check_payment_setup --settings=config.settings.prod
   ```
   It reports whether the key works and whether it is a test or live key.
4. **Webhook.** On the same Paystack page, set the webhook URL to
   `https://<backend-host>/api/v1/payments/paystack/webhook/`. It confirms payments even when a
   student closes the page before returning.
5. **Institution settings → School fees:** billing period, share needed to register, transaction fee
   rate (and cap) matching your Paystack pricing, then **Students can pay online**.
6. **Test** with a test fee and a test student, using the test card and mobile-money number Paystack's
   test checkout shows. Check the receipt, the balance and automatic clearance. Then switch to the live
   key.

How fees and payments work: [../reference/fees-and-payments.md](../reference/fees-and-payments.md).

## 9. Setting up the institution

The administrator signs in and works through *Administration*, in this order:

1. **Institution settings:** name, short name (the installed app is called "<short name> Portal"),
   logo (square, at least 512 × 512 pixels), colour, contacts, student-number format, registration,
   results, fees and sign-in policies.
2. **Grading schemes**, then **Academic regulations** (or a preset).
3. **Levels, departments, programmes, courses** and each programme's curriculum (courses can be
   imported).
4. **Years & semesters**: the current academic year and semester, registration and late-registration
   dates.
5. **Staff accounts** (or *Import staff*) and **Appointments** for the Registrar and Results Officer.
6. **Students** (*Import students*), photos (*Import photos*), then course offerings for the semester.
7. **Official transcripts:** signatory name and title, serial format, and the signature and seal images
   if transcripts will be sent electronically.

The SIS User Guide covers each of these screens.

## 10. Go-live checklist

- [ ] PostgreSQL in use; test suite run against it; first backup taken **and restored** once.
- [ ] `check --deploy` clean apart from HSTS preload; `DJANGO_DEBUG=False`; Django admin off.
- [ ] Portal on the institution's domain over HTTPS; `FRONTEND_URL` is that address.
- [ ] Opening `https://<portal-address>/manifest.webmanifest` shows `"name": "<short name> Portal"`.
- [ ] Password-reset email arrives and is not marked as spam.
- [ ] Push tested on an Android phone and an iPhone (installed app).
- [ ] A transcript, result slip and receipt printed on the institution's own black-and-white printer:
      the watermark, INTERIM stamp, signature and seal are all visible.
- [ ] If payments are used: a live payment of a small amount made, credited, receipted and refunded.
- [ ] Scheduled task runs (check its log the next day).
- [ ] Repositories private; `.env` backed up somewhere safe and separate from the database backups.
- [ ] Contract, data-processing annex and incident contacts agreed
      ([../security/data-protection.md](../security/data-protection.md)).

## A second institution

Repeat this guide from the start: new database, new backend web app (or the institution's own hosting
account), new `.env` with a new secret key and new push keys, new media folders, new frontend project.
Never share a database, key or media folder between institutions.
