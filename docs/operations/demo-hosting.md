# Demonstration installs

A demo portal shows the product to prospective institutions with fictitious data. It may run on free
hosting and SQLite. **A demo must never hold real student records.**

## Free PythonAnywhere and Vercel

Follow [deployment.md](deployment.md), with these differences:

| Topic | Demo | Production |
|---|---|---|
| Database | SQLite (leave `DATABASE_URL` empty) | PostgreSQL |
| Scheduled task | One a day (free plan limit) | Hourly |
| Outbound internet | Free plan allows only listed sites; `api.brevo.com` and `api.paystack.co` are on the list | Unrestricted |
| CPU | A daily allowance; once used, everything slows until it resets, and PDF downloads can time out | Plan-sized |
| Domain | `<user>.pythonanywhere.com` and `*.vercel.app` | The institution's own |
| Payments | Paystack **test** key only | Live key |

What the free plan's limits mean in practice:

* Push messages and emails that were not sent straight away go out while people use the portal, or
  with the daily task. A message created late at night may wait until morning.
* The first PDF after a new logo is uploaded is slower; later ones use the prepared copy.

## Demo data

`seed_demo` creates a fictitious institution ("Northridge College … (Demo)") with programmes, courses,
results, fees and these accounts:

| Role | Email |
|---|---|
| Administrator | admin@northridge.example |
| Registrar | registrar@northridge.example |
| Results officer | exams@northridge.example |
| Accounts officer | accounts@northridge.example |
| Lecturer | lecturer@northridge.example |
| Student | student@northridge.example |

* **Always set your own `DEMO_PASSWORD`** before seeding a demo that others can reach. The built-in
  default is published in the developer README, so a demo seeded with it can be signed into by anyone.
* `seed_demo` refuses to run unless `DJANGO_DEBUG=True` and the database holds no academic data. Turn
  debugging on for the seeding command only, then set `DJANGO_DEBUG=False` and reload:
  ```bash
  DJANGO_DEBUG=True DEMO_PASSWORD='<a strong password>' python manage.py seed_demo
  ```
* To reset a demo, delete the SQLite file, run `migrate`, then seed again.

## From a pilot on SQLite to PostgreSQL

If a pilot ran on SQLite with real data and moves to production:

1. Create the PostgreSQL database (see [deployment.md](deployment.md), section 2).
2. Export from SQLite, with the old settings:
   ```bash
   python manage.py dumpdata --natural-foreign --natural-primary \
     --exclude contenttypes --exclude auth.permission --exclude sessions \
     --indent 2 -o sis-export.json
   ```
3. Set `DATABASE_URL` to PostgreSQL, then:
   ```bash
   python manage.py migrate
   python manage.py loaddata sis-export.json
   ```
4. Check: sign in as each role and compare counts of students, registrations, published results and
   payments with the old portal.
5. Keep the SQLite file, read-only and secured, until the institution accepts the new portal; then
   delete it.

The export holds personal data: keep it encrypted and delete it when the move is accepted.
