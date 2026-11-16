# Configuration reference

Every server setting is an environment variable, read from the `.env` file in the backend's project
folder (next to `manage.py`). Start from `.env.example`. After any change, **reload the web app**.

* Never commit `.env`, and never paste its values into email, chat or tickets.
* Each institution has its own `.env`, with its own secret key. Never copy one institution's keys
  to another.
* Values in `<angle brackets>` are yours to fill in.

## Core

| Variable | Production value | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` | Set in the WSGI file too. Production settings refuse to start with a weak secret key, debugging on or no allowed hosts. |
| `DJANGO_SECRET_KEY` | Long random value | Generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Changing it signs everyone out and invalidates password-reset links. |
| `DJANGO_DEBUG` | `False` | Must be `False` in production. |
| `DJANGO_ALLOWED_HOSTS` | `<backend-host>` | The backend's host name, e.g. `api.your-college.edu.gh`. Comma-separated. |
| `DATABASE_URL` | `postgres://<user>:<password>@<host>:<port>/<db>` | **PostgreSQL is required in production.** Empty means SQLite, for development and demos only. |
| `DB_CONN_MAX_AGE` | `60` | Seconds a database connection is reused. |
| `TIME_ZONE` | `Africa/Accra` | The institution's time zone. |
| `FRONTEND_URL` | `https://<portal-address>` | The public address of the portal. Used in emails, the transcript QR code, payment return and passkeys. **Printed transcripts point here: set it before issuing any.** |
| `CSRF_TRUSTED_ORIGINS` | `https://<portal-address>` | Exactly the portal's origin: `https://`, no trailing slash. |
| `CORS_ALLOWED_ORIGINS` | empty | Only if something calls the API from another origin. The portal does not. |
| `NUM_PROXIES` | `2` with the frontend proxy in front of a hosted backend | Proxies that add to `X-Forwarded-For` before Django. Rate limits and audit IP addresses depend on it. |
| `CACHE_LOCATION` | `/home/<user>/sis-cache` | A writable folder, so rate limits and prepared PDF images are shared by all web workers. |
| `DJANGO_ADMIN_ENABLED` | `False` | Django's built-in admin, for superusers only. Enable temporarily if support needs it. |

## Files

| Variable | Production value | Notes |
|---|---|---|
| `MEDIA_ROOT` | `/home/<user>/sis-media` | Public files: the logo. Served at `/media/`. |
| `PRIVATE_MEDIA_ROOT` | `/home/<user>/sis-private` | Student photos, transcript signature and seal. **Never** inside `MEDIA_ROOT` and never mapped as static files. Back it up. |

## Sign-in and security

| Variable | Default | Notes |
|---|---|---|
| `SESSION_COOKIE_AGE` | `28800` (8 hours) | Sign-out after this long without activity. The maximum sign-in length (7 days students, 24 hours staff) is set in *Institution settings*. |
| `SECURE_SSL_REDIRECT` | `True` | Redirect HTTP to HTTPS. |
| `SECURE_HSTS_SECONDS` | `31536000` | Browsers use HTTPS only, for a year. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | Applies HTTPS-only to subdomains too. Set `False` if the domain has subdomains that are not HTTPS. |
| `SECURE_HSTS_PRELOAD` | `False` | Opt-in. Turn on only when the whole domain is permanently HTTPS-only. |
| `THROTTLE_LOGIN`, `THROTTLE_LOGIN_EMAIL` | `10/min`, `20/hour` | Sign-in attempts per client and per email address. |
| `THROTTLE_PASSWORD_RESET`, `THROTTLE_PASSWORD_RESET_EMAIL` | `5/hour`, `3/hour` | Password-reset requests per client and per email address. |
| `THROTTLE_TRANSCRIPT_VERIFY` | `30/min` | Public transcript verification lookups per client. |
| `WEBAUTHN_RP_ID`, `WEBAUTHN_ORIGINS` | From `FRONTEND_URL` | Leave unset. Passkeys belong to the portal's address; changing the address later means everyone sets passkeys up again. |

## Email

| Variable | Production value | Notes |
|---|---|---|
| `EMAIL_BACKEND` | `anymail.backends.brevo.EmailBackend` | Development prints email to the console. |
| `BREVO_API_KEY` | From Brevo → *SMTP & API → API keys* | Secret. |
| `DEFAULT_FROM_EMAIL` | `SIS <no-reply@your-college.edu.gh>` | A sender verified in Brevo. |
| `EMAIL_DAILY_LIMIT` | `300` | The plan's daily allowance; lower it if the same account sends campaigns. |
| `EMAIL_SECURITY_RESERVE` | `60` | Kept back for password resets and account set-up links. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | empty | Only for an SMTP server instead of Brevo. |

## Push notifications

| Variable | Production value | Notes |
|---|---|---|
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | From `python manage.py generate_vapid_keys` | Generate **once**. New keys disconnect every device. Empty switches push off. |
| `VAPID_SUBJECT` | `mailto:<it-contact>` | A contact address for the push services. |
| `PUSH_ALLOWED_HOSTS` | (built-in list) | Browser push services accepted. Change only to add a new browser's service. |

## Online payment

| Variable | Production value | Notes |
|---|---|---|
| `PAYSTACK_SECRET_KEY` | `sk_test_…` while testing, then `sk_live_…` | Secret. Empty keeps online payment off. Check it with `python manage.py check_payment_setup`. |
| `PAYSTACK_BASE_URL` | (unset) | Testing only: points at a simulated gateway. Never set it in production. |

## Development and testing only

`SQLITE_PATH` (the SQLite file), `DEMO_PASSWORD` (password for `seed_demo` accounts). Never set them
on a production server.

## Frontend (Vercel or other Node.js host)

| Variable | Value | Notes |
|---|---|---|
| `BACKEND_URL` | `https://<backend-host>` | No trailing slash. Read at **build** time: redeploy after changing it. |
