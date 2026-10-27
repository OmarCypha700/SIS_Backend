# Academic regulations: resits, retakes, progression, probation and dismissal

The system does **not** contain any national or regulator rules. Each institution enters its
own regulations under **Administration → Academic regulations** (`GET/PATCH /api/v1/academic-regulations/`,
administrators only). Every rule is optional; a blank value switches it off, and with all rules off
the system behaves as before. Record where the rules come from in the *Source* field.

## Settings

| Setting | Effect |
|---|---|
| Offer resits | Allows resits to be opened for published courses |
| Minimum course total to qualify | Only failing students at or above this total are eligible for a resit |
| Resits allowed per course | Completed resits per student per course, across all attempts |
| Highest grade point a resit can earn | Grade cap for resit results (e.g. 2.00 = C on a 4-point scale) |
| Highest grade point a retaken course can earn | Grade cap when a failed course is re-registered in a later semester |
| Attempts allowed per course | Registrations with a published result; when used up, the course can no longer be registered |
| Failed compulsory course must be retaken | A failed compulsory course becomes compulsory whenever it is offered again |
| Probation when CGPA is below | Proposes probation |
| Dismissal when CGPA is below | Proposes dismissal (must not be above the probation threshold) |
| Dismissal after N consecutive probations | The Nth probation in a row becomes a dismissal proposal |
| Dismissal when attempts are exhausted | Proposes dismissal when a compulsory course is failed with no attempts left |
| Credits needed in the year to be promoted | Minimum passed credits in the academic year |
| Most failed credits that may be carried forward | Maximum outstanding (failed, not since passed) credits for promotion |
| Minimum CGPA to proceed | Below it, the student is not promoted at the end of the year |
| Most referred courses a student may carry forward | Counts courses failed and not yet passed ("referred"); above it, no promotion |
| Repeat the level when not promoted | Otherwise the student stays without a "repeat" proposal |
| Dismissal when referred courses are more than | Proposes dismissal/withdrawal above this number of referred courses |
| Apply the dismissal CGPA as "at or below" | Treats the dismissal threshold as inclusive (e.g. "1.0 or less") |
| Dismissal on a second repeat | Proposes dismissal when a student who already repeated a level would repeat it again |
| Record the student as | Status set when a dismissal is applied: Dismissed or Withdrawn |

Grade caps limit the **grade and grade points** only; the underlying total is kept and the result
is marked "Grade capped" on records and transcripts.

## Presets

*Academic regulations → Start from a preset* fills the form with a published policy; nothing is
saved until an administrator reviews the values and chooses **Save regulations**. From the command
line: `python manage.py apply_regulation_preset --list` and
`python manage.py apply_regulation_preset health_training_institutions`.

**Health Training Institutions (Ghana) – examination policy**, student obligations 1(d)–(g):

| Policy | Setting |
|---|---|
| (d) Proceed with CGPA ≥ 1.5 and at most 2 referred courses | Minimum CGPA to proceed 1.50; referred courses to proceed 2 |
| (e) Write and pass referred courses | Resits on (one per course); failed compulsory courses must be retaken when offered |
| (f) Repeat the class with CGPA < 1.5 or 3 referred courses | Repeat the level when not promoted |
| (g) Withdrawal with CGPA of 1.0 or less | Dismissal CGPA threshold 1.00, "at or below", recorded as Withdrawn |
| (g) Withdrawal on a second repeat | Dismissal on a second repeat |
| (g) Withdrawal with more than 3 referred courses | Dismissal when referred courses are more than 3 |

Not applied by the system: (b)–(c) eligibility to sit examinations based on 21 days of absence
(there is no attendance module), (h) the graduation requirement, and (i)–(p) examination-hall
conduct. No probation stage, resit grade cap or attempt limit is set because the policy does not
state one; add them if your institution's full regulations do.

## Retakes

A failed course can be re-registered in a later semester when it is offered (and when
*Institution settings → Students may retake failed courses* is on). It is marked **Retake** in
registration, counts against the attempt limit, and is graded with the retake cap. The CGPA uses
the institution's retake policy (latest, best or all attempts).

## Resits (supplementary examinations)

1. **Open** – after a course's results are published, an examinations officer, registrar or
   administrator opens a resit from the result sheet, optionally with a registration deadline.
   Eligible failing students are listed automatically.
2. **Register** – students register under *My results → Resits* before the deadline. Registry staff
   can register students at any time before scores are submitted (a reason is required).
3. **Score** – the assigned lecturer enters a resit score out of 100 for each registered student.
4. **Approve and publish** – same capabilities and separation of duties as ordinary results.
5. **Calculation** – coursework is kept and the resit score replaces the components marked
   *Exam* in the assessment set-up (scaled to their combined weight). If no component is marked,
   the resit score becomes the course total. The resit cap is then applied. Students who did not
   register keep their original result.

Published resit results are recorded in the audit log with the old and new grade, and the course
appears as "Resit" on the student's record. Resit grades cannot be changed through the ordinary
amendment process.

## Progression reviews (probation, promotion, repeating, dismissal)

1. **Prepare** – after a semester's results (and resits) are published, the registry prepares a
   review for that semester (*Results → Progression reviews*). For each active student registered in
   the semester the system computes semester GPA, CGPA (using the retake policy), credits earned in
   the academic year and outstanding failed credits, and proposes:
   * **standing** – good standing, probation or dismissal;
   * **placement** – promote, repeat level, no change, or final level completed.
   Promotion is only considered in the last term of the academic year. Each proposal lists the
   reasons in plain language.
2. **Review** – registry staff may change any outcome (a reason is required and recorded) or exclude
   a student. Recalculating keeps these changes. Students whose results are not all published are
   marked *Provisional*.
3. **Apply** – applies every included, non-provisional decision: promotion moves the student to the
   next level of their programme; probation and good standing update the student's academic
   standing; dismissal sets the status to *Dismissed* (which, by default, blocks registration but keeps
   access to results). Every change goes into the student's history and the audit log. An applied
   review cannot be changed or re-run.

Nothing in a review changes a student record until it is applied.

## Not covered yet

* Different regulations per programme (one set per installation today).
* Graduation and classification of awards.
* Automatic generation of resit timetables or notifications to students.
* Reinstatement workflows after dismissal (use *Change status* with a reason).
