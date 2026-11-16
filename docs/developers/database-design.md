# Database design

Internal: for the development team.

All schema changes are Django migrations (`apps/*/migrations`). The ORM is used for all data
access; nothing depends on SQLite-specific SQL.

## Entities

```
InstitutionProfile (1 row)       StudentNumberSequence(key, last_value)

User ──1:1── Student ──▶ Programme ──▶ Department
  │              │ └──▶ Level (current)
  │              └── StudentProgrammeHistory (status/programme/level changes)
  │
  └── LecturerAssignment ◀── CourseOffering ──▶ Course ──▶ Department
                                  │  │               └── prerequisites (Course ↔ Course)
                                  │  └──▶ Semester ──▶ AcademicYear
                                  └── eligible Programmes / Levels (M2M)

Programme ── ProgrammeCourse (course, level, term, COMPULSORY/ELECTIVE) ──▶ Course

SemesterRegistration (student, semester, programme & level snapshot, status, credits)
  ├── CourseRegistration (offering, ACTIVE/DROPPED, retake, category)
  └── RegistrationEvent (history of every add/drop/submit/approve)

GradingScheme ── GradeBand (letter, min, max, points, pass)
CourseOffering ── AssessmentComponent (name, weight, max score, required)
ResultSheet (1 per offering, scheme, status, who/when) ── ResultSheetEvent
StudentMark (course registration × component → score)
CourseResult (per course registration: total, grade, points, credits, published flag)
ResultAmendment (original/new marks & grade, reason, requester, reviewer, status)

AcademicRegulation (1 row: resit, retake, attempt and progression rules)
ResitSheet (1 per offering, workflow status, deadline) ── ResitEntry (course result, ELIGIBLE/REGISTERED/NOT_TAKEN/COMPLETED, score, original & new grade)
ProgressionReview (1 per semester, DRAFT/APPLIED, regulation snapshot)
  └── ProgressionDecision (student, GPA/CGPA/credits, proposed & final standing/placement, reasons, override reason, excluded, applied)
Student.academic_standing (GOOD / PROBATION); CourseResult.is_resit, grade_capped; AssessmentComponent.is_examination

Appointment (user, office, acting, starts_on, ends_on, reference, ended_at, end_reason)
SignInDelay (email, failures, wait until)   Passkey (user, credential, public key, counter)
SignedInDevice (user, session_key, method, user agent, IP, signed_in_at, last_seen_at)

FinancialClearance (student, semester, cleared, note, decided_by/at)
FeeSchedule (programme, level, academic_year, semester?) ── FeeItem (description, amount)
LedgerEntry (student, kind CHARGE/PENALTY/PAYMENT/WAIVER/REFUND/ADJUSTMENT/REVERSAL, side BILL/SETTLEMENT,
             amount, period, fee_item?, payment?, reverses?)  — append-only
Payment (student, reference, source ONLINE/MANUAL, status, amount, fee, total, channel, receipt_number)

IssuedTranscript (student, serial, verification_code, frozen record, signing, interim, print count, revoked)

Notification (recipient, kind, title, body, link, read_at)
PushSubscription (user, endpoint, keys, device?)   OutgoingMessage (channel, recipient, status, attempts)

AuditLog (append-only)
StatusPolicy (status → can_register, can_view_results)
```

## Important constraints

| Constraint | Purpose |
|---|---|
| `Student.student_number`, `Student.email`, `User.email` unique | Duplicate identity prevention |
| `Course.code`, `Programme.code`, `Department.code`, `Level.name/order` unique | Codes unique per installation |
| `Semester (academic_year, term_number)` unique; partial unique on `is_current=True` | Exactly one current semester |
| `CourseOffering (course, semester)` unique | A course is offered once per semester |
| `SemesterRegistration (student, semester)` unique | One registration per semester |
| Partial unique `CourseRegistration (student, offering) WHERE status='ACTIVE'` | No duplicate active registrations; dropped history is kept |
| `StudentMark (registration, component)` unique | One mark per component |
| `ResultSheet.offering` one-to-one; `CourseResult.registration` one-to-one | One result per registration |
| `GradingScheme` partial unique on `is_default=True` | One default scheme |
| `on_delete=PROTECT` on academic references | Historical records cannot be orphaned; the API returns 409 |
| `Payment.reference`, `Payment.receipt_number` unique | A payment is credited once; receipts are unique |
| One charge per student per fee item; an entry can be reversed once | Billing again is safe; no double reversal |
| `IssuedTranscript.serial`, `verification_code` unique | Each transcript is identifiable and verifiable |
| `SignedInDevice.session_key` unique | One record per sign-in |

Validation that spans fields (date ordering, semester inside its year, credit min ≤ max, grade
bands contiguous and non-overlapping, weights totalling 100) lives in model `clean()` methods or
service functions and is reused by the serializers.

## History and deletion

* Students are never hard-deleted through the API; they are deactivated (`is_archived`, login disabled).
* Dropped courses keep their row with `status=DROPPED` and a `RegistrationEvent`.
* Raw marks are retained after computation, so results can be recalculated or amended.
* Published results change only via `ResultAmendment`, recorded in the audit log.
* Each result sheet stores the grading scheme it used; schemes used by submitted results are locked.
* Fee ledger entries are never edited or deleted: mistakes are corrected with a `REVERSAL` entry.
* Issued transcripts freeze the record they print, so reprints are identical; they are revoked, not deleted.

## Indexes

Beyond primary/foreign keys and unique constraints: `Student(status)`, `Student(last_name, first_name)`,
`Student(programme, current_level)`, `SemesterRegistration(semester, status)`,
`CourseRegistration(offering, status)`, `CourseResult(student, is_published)`,
`AuditLog(entity_type, entity_id)`, `AuditLog(created_at)`, `AuditLog(action)`.
List endpoints use `select_related`/`prefetch_related`; a test asserts the student list stays
within a fixed number of queries regardless of page size.

## SQLite vs PostgreSQL

| Topic | SQLite (development, demos) | PostgreSQL (production) |
|---|---|---|
| Concurrency | One writer at a time. Transactions use `BEGIN IMMEDIATE` (`transaction_mode`), so concurrent registrations queue rather than fail. | Row-level locks. `select_for_update()` on course offerings serialises capacity checks per course. |
| `select_for_update()` | No-op (whole-database lock already held) | Real row locks |
| Partial unique indexes | Supported | Supported |
| Case-insensitive search (`icontains`) | ASCII-only case folding | Full Unicode |
| Suitable for | One developer, a demo, a small pilot with few simultaneous users | Any real deployment |

Do not rely on SQLite's locking as a production strategy. Run the test suite and the critical
workflows against PostgreSQL before go-live ([testing.md](testing.md), "PostgreSQL").
