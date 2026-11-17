# Data protection

How SIS supports an institution's obligations under Ghana's **Data Protection Act, 2012 (Act 843)**, and
what the institution and Alfa Calix Enterprise each need to do. This is guidance, not legal advice:
confirm your obligations with your legal adviser and the Data Protection Commission.

## Roles

* The **institution** is the *data controller*: it decides why and how student and staff data is used.
  It should be registered with the Data Protection Commission.
* **Alfa Calix Enterprise** (and any hosting company it uses) is a *data processor*: it handles the data
  only on the institution's instructions, under a written contract.

## Responsibilities

| Topic | How SIS supports it | Institution / Alfa Calix Enterprise |
|---|---|---|
| Minimisation | Only name, contact, programme and academic data are required; date of birth, gender, address and emergency contact are optional | Institution: decide which optional fields are justified, and leave the rest empty |
| Authorised access | Roles, appointments, deny-by-default access, reduced views for lecturers | Institution: give roles carefully; review staff accounts and appointments each semester |
| Accountability | Audit log of sensitive actions, including exports, with the capacity each person acted in | Institution: review the log, especially exports; investigate anything unusual |
| Tutor assessment | Answers are stored with no link to the student; averages only above a minimum number of responses; comments reviewed before tutors see them | Institution: tell students it is anonymous (privacy notice); never try to identify who wrote a comment |
| Accuracy | Students see their own records; changes are recorded with reasons | Institution: correct records on request |
| Retention | Records are deactivated, not deleted, to keep academic history | Institution: set how long records are kept after graduation or withdrawal; Alfa Calix Enterprise purges on instruction with a controlled, audited procedure |
| Access requests | Exports of student records; students see their own data, results and fees; transcripts | Institution: answer requests within the legal time limits |
| Security | See [overview.md](overview.md) | Alfa Calix Enterprise: maintain it; both: keep accounts personal (no shared logins) |
| Backups | Daily, encrypted, off-site | Alfa Calix Enterprise: run and test them; record restore tests |
| Incidents | Audit log and server logs support investigation | Both: follow the procedure below |
| Vendor access | Named accounts only; every action audited | Contract: who at Alfa Calix Enterprise may access data, and why |
| Hosting location | Chosen per installation | Contract: name the hosting providers and countries (sub-processors) |
| End of service | Full export of the database and files | Alfa Calix Enterprise: hand over the data, then delete every copy and confirm in writing |

Never use real student records in development, training or demonstrations without the institution's
written authorisation. Demonstrations use fictitious data only.

## Contract annex: data processing (checklist)

Agree these in writing before go-live:

- [ ] The data processed: student and staff identity and contact details, photos, academic records,
      registrations, results, fees and payments, sign-in and audit records.
- [ ] Purpose: running the institution's student information system, and nothing else.
- [ ] Alfa Calix Enterprise acts only on the institution's documented instructions.
- [ ] Named staff at Alfa Calix Enterprise who may access the data, and for what (support, updates,
      backups); confidentiality obligations on them.
- [ ] Sub-processors (hosting, email, payment provider) and the countries where data is stored; notice
      before any change.
- [ ] Security measures (see [overview.md](overview.md)), backups and restore testing.
- [ ] Incident notification: Alfa Calix Enterprise tells the institution **within 24 hours** of
      discovering an incident, with what is known.
- [ ] Help with access requests and corrections.
- [ ] Retention periods, and deletion or return of data at the end of the contract, with written
      confirmation.
- [ ] Ownership of accounts (domain, hosting, email, payment provider) and who pays for them.
- [ ] The institution's right to audit, or to receive evidence of, these measures.

## Privacy notice for students and staff (template)

Adapt and publish this, for example on the institution's website and in the student handbook.

> **How [Institution] uses your information in the student portal**
>
> [Institution] uses the student portal to manage admissions records, course registration, results,
> fees and official documents. We hold your name, student or staff number, contact details, photo,
> programme and level, registrations, marks and results, fee and payment records, and records of your
> sign-ins and actions in the portal.
>
> We use this information to run your studies or your work, to issue results and transcripts, to collect
> fees and to keep the portal secure. When results are published you may be asked to assess your tutors
> first; your answers are stored without your name or student number and are reported only as averages
> from several students, so no one, including your tutor, can tell what you said. Staff see only what their role needs. Online payments are processed
> by our payment provider; we never receive your card number or mobile-money PIN.
>
> The portal is provided and hosted for us by Alfa Calix Enterprise and its hosting providers, under a
> contract that requires them to protect your information and use it only for us.
>
> We keep academic records for [period] after you leave, as required to issue transcripts and verify
> qualifications. You may ask to see the information we hold about you, and to correct anything that is
> wrong, by contacting [office, email].
>
> If you believe your information has been misused, contact [office, email]. You may also contact the
> Data Protection Commission.

## Incident response

An incident is any event that may have exposed, changed or lost personal data: a stolen or shared
password, a lost device signed in to the portal, data sent to the wrong person, unauthorised access,
or loss of data.

1. **Contain** (immediately):
   * a person's account: sign it out everywhere (*Staff accounts* or the student's record), set a
     temporary password, or deactivate it;
   * a suspected server compromise: Alfa Calix Enterprise takes the portal offline if needed, and
     rotates the server keys.
2. **Report:** staff report to the institution's designated contact; Alfa Calix Enterprise reports to
   the institution within 24 hours of discovering an incident it handles.
3. **Assess:** what data, whose, how many people, and the likely harm. Use the audit log (filter by
   person, action and date) and server logs. Preserve evidence: do not delete logs or accounts.
4. **Notify:** the Act requires the data controller to notify the **Data Protection Commission and the
   people affected** as soon as reasonably practicable after discovering unauthorised access to personal
   data. The institution decides and sends the notifications; Alfa Calix Enterprise provides the facts.
5. **Recover:** restore from backup if data was lost or altered; correct affected records through the
   portal so the changes are audited.
6. **Learn:** within two weeks, record what happened, why, and what changes prevent it from recurring.

Keep an incident register (date, what happened, data affected, people notified, actions taken), even
for incidents that needed no notification.
