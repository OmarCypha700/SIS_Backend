# How SIS protects your institution's data

This overview is for institution management, IT staff and auditors. It describes how the portal
protects accounts and records. Responsibilities under the Data Protection Act are in
[data-protection.md](data-protection.md).

## Your own installation

Each institution has its **own portal, database, files and keys**. No other institution's portal can
reach your data, and your records are never mixed with anyone else's.

## Signing in

* **Passwords** must be at least 10 characters and are checked against common and easily guessed
  passwords. They are stored only in a one-way, salted form that cannot be turned back into the password.
* **Fingerprint or face sign-in (passkeys).** Staff and students can sign in with their device's
  fingerprint, face or screen lock instead of a password. The fingerprint or face never leaves the
  device, and passkeys only work on your portal's own address, so they cannot be stolen by a fake site.
* **Wrong passwords** make the next attempt wait, a little longer each time. Accounts are never locked
  permanently, so nobody can lock someone else out on purpose. Repeated failures alert the account
  holder.
* **Temporary passwords** given to new or imported users must be changed before anything else works.
* **Sessions** end after a period without activity, and in any case after a set time (by default 7 days
  for students and 24 hours for staff, adjustable in Institution settings).
* **Signed-in devices.** Everyone can see where they are signed in and sign out any other device. A
  password change signs out the other devices. Administrators and the registry can sign an account out
  everywhere, for example when a phone is lost.

## Who can see and do what

* Every person has a **role** (Administrator, Registrar, Results Officer, Accounts Officer, Lecturer,
  Student). Offices such as Registrar can be held for a period through **appointments**, and access ends
  when the appointment ends.
* Access is **denied unless allowed**. Students see only their own records. Lecturers see only the
  students on their own courses, without personal contact details. The Accounts Office sees fees and
  clearance, not results.
* **Results need more than one person.** Approving and publishing results are separate permissions, and
  by default the approver must be someone other than the lecturer who submitted them. Amendments to
  published results need a second person's approval.

## Records cannot be quietly changed

* **Audit log.** Sign-ins, changes to students, registrations, marks, results, fees, payments and
  settings are recorded with who did it, in which capacity, when, from where, and the before and after
  values. The log cannot be edited or deleted through the portal.
* **Published results** change only through an approved amendment. **Fee accounts** are never edited:
  a mistake is corrected with a visible reversing entry.
* **Students are not deleted** once their record has been used; they are deactivated, so academic history
  is kept.

## Payments

Students pay on the payment provider's secure page. **Card numbers and mobile-money PINs never reach
the portal**, and the portal never holds money: it settles directly to the institution's bank account. A
payment is credited only after the portal has confirmed it with the provider and the amount matches.

## Documents

* Official transcripts carry a serial number and a **verification code and QR code**. Anyone can check
  a transcript on the portal's verification page, which shows whether it is valid or has been revoked.
* Every printed document carries the institution's name across the page, which makes altered copies
  easier to spot.

## Data in transit and on devices

* The portal works **only over HTTPS**. Sign-in cookies cannot be read by scripts on the page.
* The installable app **does not store records, results or uploads on the device**, so a lost or shared
  phone does not expose them. Notifications never contain marks, grades or personal details.
* Student photos and transcript signature images are kept in a private area and are never available at
  a public address.

## Operations

* Daily, encrypted, off-site backups, with restores tested each semester.
* Software updates are tested before release, and security checks of the software's components are run
  before each release.
* The software's source code is proprietary and kept in private repositories.
