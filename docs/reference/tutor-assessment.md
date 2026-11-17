# Tutor assessment

When results are published, students assess each course's tutors before they see their result for that
course, and all of them before they see the semester GPA and their CGPA. It gives quality assurance a high
response rate, and because students answer before seeing their grade, the grade cannot colour the answer.

It is **off by default**: an administrator turns it on under *Institution settings → Tutor assessment*.

## How it works

1. A course's results are published for the first time. If tutor assessment is on and the course has
   tutors, its assessment **opens** with the active questionnaire, for every tutor assigned to the course.
2. Students are told: "Assess your tutor for NUR103 to see your result." On *My results* the course shows
   **Assess tutor to see your result** instead of the score and grade. Students are never shown the date the
   hold lifts.
3. The student answers the questions for each tutor (one at a time for team-taught courses). When the last
   tutor of the course is done, the score and grade appear straight away.
4. When every course is done, the semester GPA, CGPA and credits earned appear, and result slips and the
   statement of results can be downloaded.
5. Three days before the assessment closes, students who have not finished get a reminder.
6. The assessment **closes** after the set number of days (14 by default). From then on the results show
   for everyone, assessed or not, so no one is kept from their results.
7. If the institution shares feedback with tutors, they are told it is ready and see it under *Results → My
   teaching feedback*.

Resits, deferred examinations and amendments do **not** reopen an assessment. A course with no assigned
tutor has no assessment, and its results show straight away. Staff views of results, official transcripts and
progression reviews are never held. Turning the setting off lifts every hold at once.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| Students assess their tutors before seeing their results | Off | Switches the feature on |
| Days to assess | 14 (3 to 60) | From publication until the hold lifts |
| Responses needed before averages show | 5 (3 to 20) | Below this, reports show counts only |
| Tutors can see their own feedback | On | Off: only quality assurance and administrators see results. Tutors then have no *My teaching feedback* page and are not told when feedback is ready. Some institutions prefer this where a tutor teaches a single class |

## Questionnaires (quality assurance)

*Quality assurance → Questionnaires*:
* **Add the standard questionnaire**: 10 rating questions (1 = strongly disagree, 5 = strongly agree) and two
  optional comments. Adjust it before using it.
* One questionnaire is **active**; it is used for every course published from then on. Each course keeps the
  questions it opened with.
* A questionnaire that has been used cannot be edited (its answers would no longer match): **Copy** it, edit
  the copy, and make the copy active.
* If no questionnaire exists when results are published, the standard one is added and used.

## Anonymity

Students are told their answers are anonymous, and the system is built so that they are:

* Whether a student has assessed a tutor, and **what** they answered, are stored in separate tables with
  nothing linking them. The answers have no student, a random identifier, and a date but no time.
* Nobody, not quality assurance, not administrators, not someone reading the database, can link an answer to
  a student. Submissions are not written to the audit log, which would link them.
* Averages and comments appear only when a tutor has at least the minimum number of responses for the course.
* Comments are listed in a random order, never in the order received.
* Tutors see nothing while the assessment is open, not even how many have answered.
* Students are asked not to write anything that identifies them, and comments reach tutors only after quality
  assurance approves them.

## Who sees what

| | Averages and spread | Comments | When |
|---|---|---|---|
| Quality assurance, administrators | All tutors | All, with the review queue | Any time (with enough responses) |
| The tutor (if the institution allows it) | Their own courses | Approved only | After the assessment closes |
| Students, other staff | No | No | Never |

A Quality Assurance Officer is a role, or an office a staff member is appointed to (*Administration →
Appointments*), like the Registrar.

## Reports

*Quality assurance → Tutor assessments* lists every tutor and course with the response rate and the overall
average, filtered by semester and department, and exports to Excel. Each course shows, per tutor:
* the average for each question and how many answered 1 to 5;
* how many responses gave the same answer to every question (possibly rushed; worth bearing in mind);
* comments;
* the tutor's overall average over time.
