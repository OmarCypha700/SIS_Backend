# Official transcripts

The portal produces two kinds of academic record:

| | Unofficial copy | Official transcript |
|---|---|---|
| Who | Students (own record), registry staff | Administrators; registrars only if an administrator allows it |
| Contents | Published results (staff may include unpublished, marked "internal") | Published results only |
| Identification | None, marked "Unofficial copy" | Serial number, verification code and QR code |
| Recorded | No | Issuance register and audit log |
| Signature | None | Signature line and stamp box, or the uploaded signature and seal |

## Issuing

* **From the student's record**: open the student, go to the *Academic record* tab, then
  *Official transcripts* → **Issue official transcript**.
* **From the register**: go to *Students → Transcripts* → **Issue transcript** and search for the student.

The dialog shows the CGPA, credits and number of semesters that will be printed. It also warns when
the transcript will be **interim**: the programme is not marked Completed or Graduated, or some
results are still unpublished. Unpublished results never appear on an official transcript. Interim
transcripts are titled "(interim)", say why, and carry an INTERIM watermark.

The person issuing it enters who it is for (required) and chooses how it is signed:

* **Print for hand signing and stamping** (the default). The PDF has a signature line, the
  signatory's name and title, a date line, and an empty box for the official stamp. The designated
  authority signs and stamps the printed copy.
* **Include the uploaded signature and seal**, for PDFs sent electronically. Available only after an
  administrator uploads both images.

Issuing allocates the next serial number, for example `CNMT/TR/2026/000123` (format set in
*Institution settings → Official transcripts*), and downloads the PDF.

## Reprinting and revoking

* The record printed on each transcript is frozen when it is issued, so a **reprint** is identical
  to the original. It keeps the same serial and code, and its footer says "Reprint, copy N". Every
  print is counted and logged.
* **Revoke** a transcript, with a reason, when a result is amended after issue or it was issued in
  error. A revoked transcript can no longer be printed, and its verification page says it has been
  revoked. Issue a new one if needed.
* Issuing a new transcript does not cancel earlier ones, because a student may need copies for
  several recipients. Revoke old ones yourself if they should no longer be relied on.

## Verification

Each transcript carries a QR code and a 10-character code, e.g. `BUD7V-JRKHE`. Anyone can open
`<frontend address>/verify` without signing in and enter the code, or scan the QR code. The
verification page shows:

* whether the transcript is **valid** or **revoked**;
* serial number, student name and number, programme, cumulative GPA, credits earned, the last
  semester covered, the date of issue, and whether it is interim.

Individual grades are not shown: the verifier compares the details with the paper in front of them.
Codes are random and cannot be guessed from serial numbers, and lookups are rate-limited
(`THROTTLE_TRANSCRIPT_VERIFY`, default 30 per minute).

**The QR code uses `FRONTEND_URL`.** In production it must be the public address of the frontend
(e.g. `https://portal.your-college.edu.gh`). Otherwise printed codes point to the wrong place.

## Who can issue

* Staff accounts have a **Can issue official transcripts** permission. Administrators have it by
  default. Registrars get it only when an administrator ticks it under *Staff accounts → Edit*.
  Only administrators and registrars can hold it.
* Removing the permission does not affect transcripts already issued.
* Without the permission, registry staff can view the register and unofficial copies, but cannot
  issue, print or revoke.

## Signature and seal images

Upload them under *Institution settings → Official transcripts* (PNG, JPG or WebP, up to 2 MB).

* They are stored in `PRIVATE_MEDIA_ROOT` (default `private-media/`), outside the public `/media/`
  folder, so nobody can download them from a public address.
* Only administrators can preview, replace or remove them.
* Include this folder in your backups (see deployment.md, section 7).
