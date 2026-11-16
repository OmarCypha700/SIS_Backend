# Running the portal: maintenance

Routine care for a production portal installed with [deployment.md](deployment.md).

## Scheduled task

`python manage.py run_daily_tasks` runs, in order (each can also be run on its own):

| Command | What it does |
|---|---|
| `reconcile_payments` | Confirms online payments still pending after 30 minutes; marks those not completed after 24 hours as *Not completed* |
| `send_pending_messages` | Sends queued email and push, builds each person's email digest, retries failures (up to 5 attempts) and prunes old data: read notifications after 180 days, sent messages after 90 days, devices that keep failing |
| `clear_old_sessions` | Removes expired sign-in sessions and their device records |

One failing step does not stop the others. Run it hourly where the host allows; daily at minimum.

## Backups

Back up three things, every day, and keep copies **off the server**, encrypted, with restricted access:

1. **The database**:
   ```bash
   pg_dump --format=custom --file=sis-$(date +%F).dump "$DATABASE_URL"
   ```
2. **`MEDIA_ROOT`** (the logo) and **`PRIVATE_MEDIA_ROOT`** (student photos, transcript signature and
   seal). Photos are about 30 KB each: 1,000 students need roughly 35 MB.
3. **`.env`**, kept separately from the database backups (it holds the keys).

**Restore** (stop the web app first):
```bash
pg_restore --clean --if-exists --dbname="$DATABASE_URL" sis-YYYY-MM-DD.dump
```
then copy the media folders back and reload. **Test a restore at least once a semester**, on a separate
database, and record that you did.

Retention: keep daily backups for 30 days and one per month for as long as the institution's records
policy requires.

## Updating

1. Read the release notes ([CHANGELOG.md](../../CHANGELOG.md)) for migrations and new settings.
2. Back up the database and media.
3. Backend:
   ```bash
   cd ~/sis-backend && source ~/.virtualenvs/sis/bin/activate
   git fetch --tags
   git checkout vX.Y.Z                  # the release tag, never just the latest code
   pip install -r requirements.txt
   python manage.py migrate --settings=config.settings.prod
   python manage.py collectstatic --noinput --settings=config.settings.prod
   ```
   Reload the web app.
4. Frontend: deploy the **same** release tag (on Vercel, deploy that tag's commit, or point the
   production branch at it).
5. Check the version in the account menu: it should show `X.Y.Z` with no "(server …)" note.
6. Smoke test: sign in as an administrator and a student; open the dashboard, the student list, a
   result sheet, and download one PDF.

Update outside registration and results-publication periods. Test migrations on a copy of production
data first when the release notes mention data changes.

**Rolling back:** redeploy the previous version of both projects. If a migration had already run,
restore the backup taken in step 2.

## Monitoring

| Watch | Where | Act when |
|---|---|---|
| Errors | Backend error log (Web tab) | Any repeated error |
| PDF speed | Server log lines `PDF <reference>: … in N s` | Warnings (over 3 s): check the host's CPU and the logo size |
| CPU allowance | Host dashboard | Close to the plan limit: slowness and timeouts follow |
| Email | Administrator dashboard → *Notification delivery*; Brevo logs | Failures, or a queue that does not empty |
| Payments | *Finance → Payments*, status *Needs attention* | Any: compare with the Paystack dashboard |
| Disk | Host dashboard | Over 80% of the quota |
| Backups | Backup job output | Any failed or missing day |

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ImproperlyConfigured: Set a strong DJANGO_SECRET_KEY` | `.env` missing from the project folder, or the key is too short |
| `DisallowedHost` / 400 Bad Request | Add the backend host to `DJANGO_ALLOWED_HOSTS` and reload |
| Sign-in works but saving gives 403 "CSRF" or "Origin checking failed" | `CSRF_TRUSTED_ORIGINS` must be exactly the portal's origin |
| Portal says "Cannot reach the server" | `BACKEND_URL` wrong or the backend is down; rebuild the frontend after fixing |
| "The server took too long to respond" on downloads | The backend took over 30 s. Check the `PDF … in N s` log lines and the CPU allowance; a very large logo slows the first PDF after it is uploaded |
| Installed app is called "SIS Portal" | The portal could not read the institution's name when the app was installed. Check `https://<portal>/manifest.webmanifest` shows the short name; if not, check `BACKEND_URL`. Then remove and reinstall the app on the device |
| Watermark or INTERIM stamp missing on paper | The printer is in toner-saving or draft mode; print at normal quality |
| Static admin files missing | Run `collectstatic`; check the `/static/` mapping |
| Logo not shown | Check the `/media/` mapping and `MEDIA_ROOT` permissions |
| Password-reset email not received | Check Brevo logs and the sender's verification. Each person gets at most one reset email per 15 minutes and three a day |
| Push not arriving | `VAPID_*` set? On iPhone the app must be installed on the Home Screen. The person may have blocked notifications in the browser |
| Students see "Online payment is not available" | `PAYSTACK_SECRET_KEY` missing or rejected: run `check_payment_setup`. Use the secret key (`sk_…`), not the public key, from the same mode as the dashboard, then reload |
| "The payment service is not responding" | Paystack unreachable for a moment; pending payments are checked by the scheduled task |
| "No default grading scheme is configured" | *Administration → Grading schemes*: create one and make it the default |
| "database is locked" | The portal is on SQLite: move to PostgreSQL |
| Occasional 502 in the browser console | The host stopped a slow request; check the error log for slow requests |

## Ending an institution's service

1. Export the full database (`pg_dump`) and both media folders, and hand them to the institution.
2. Get written confirmation that they received and can read the data.
3. Delete the portal, database, media, backups and keys held by Alfa Calix Enterprise, and confirm the
   deletion in writing. See [../security/data-protection.md](../security/data-protection.md).
