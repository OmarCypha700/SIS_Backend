"""
Workflow events that need someone's attention. Called by the workflow services right after the
state change, inside the same transaction: if the action rolls back, nothing is sent.

Wording rule: course codes and actions only, never marks, grades or personal details.
"""
from apps.academics.models import CourseOffering
from apps.accounts.models import User
from apps.results.models import ResitEntryStatus

from .models import Kind
from .services import lecturers_of, notify, registry_staff, users_with


def _course(offering):
    return f"{offering.course.code} {offering.course.title}"


def _offering(obj):
    return CourseOffering.objects.select_related("course").get(pk=obj.offering_id)


def _students_of_sheet(sheet):
    return list(User.objects.filter(student_profile__course_results__sheet=sheet).distinct())


# --- Result sheets ----------------------------------------------------------------

def sheet_submitted(sheet, actor):
    offering = _offering(sheet)
    notify(users_with("can_approve_results"), kind=Kind.RESULTS_SUBMITTED, actor=actor,
           title=f"Results submitted for {offering.course.code}",
           body=f"{_course(offering)} is waiting for your review.", link=f"/results/sheets/{sheet.pk}")
    signoff_check(sheet, offering, f"/results/sheets/{sheet.pk}")


def signoff_check(sheet, offering, link):
    """Tell administrators when nobody but the submitter can approve or publish this sheet."""
    from apps.results import signoff

    warnings = signoff.gaps(sheet)
    if not warnings:
        return
    admins = list(User.objects.filter(is_active=True, role="ADMIN"))
    notify(admins, kind=Kind.SIGNOFF_BLOCKED, title=f"Sign-off blocked: {offering.course.code}",
           body=" ".join(w["message"] for w in warnings), link=link)


def sheet_returned(sheet, actor):
    offering = _offering(sheet)
    recipients = lecturers_of(offering) + ([sheet.submitted_by] if sheet.submitted_by_id else [])
    notify(recipients, kind=Kind.RESULTS_RETURNED, actor=actor,
           title=f"Results returned for {offering.course.code}",
           body="The reviewer returned the results for correction. Open the course to see the reason.",
           link=f"/results/courses/{offering.pk}")


def sheet_approved(sheet, actor):
    offering = _offering(sheet)
    notify(users_with("can_publish_results"), kind=Kind.RESULTS_APPROVED, actor=actor,
           title=f"Results ready to publish: {offering.course.code}",
           body=f"{_course(offering)} has been approved and can be published.", link=f"/results/sheets/{sheet.pk}")
    signoff_check(sheet, offering, f"/results/sheets/{sheet.pk}")
    notify(lecturers_of(offering), kind=Kind.RESULTS_APPROVED, actor=actor,
           title=f"Results approved for {offering.course.code}", link=f"/results/courses/{offering.pk}")


def sheet_published(sheet, actor):
    offering = _offering(sheet)
    notify(lecturers_of(offering), kind=Kind.RESULTS_PUBLISHED, actor=actor,
           title=f"Results published for {offering.course.code}", link=f"/results/courses/{offering.pk}")
    notify(_students_of_sheet(sheet), kind=Kind.RESULTS_PUBLISHED, actor=actor,
           title=f"New result: {offering.course.code}",
           body=f"Your result for {_course(offering)} is now available.", link="/my-results")


# --- Amendments ---------------------------------------------------------------------

def amendment_requested(amendment, actor):
    offering = _offering(amendment.course_result)
    notify(users_with("can_approve_amendments"), kind=Kind.AMENDMENT_REQUESTED, actor=actor,
           title=f"Amendment requested for {offering.course.code}",
           body="A change to a published result is waiting for your decision.", link="/results/amendments")


def amendment_reviewed(amendment, actor):
    offering = _offering(amendment.course_result)
    outcome = "approved and applied" if amendment.status == "APPLIED" else "rejected"
    notify([amendment.requested_by], kind=Kind.AMENDMENT_REVIEWED, actor=actor,
           title=f"Amendment {outcome}: {offering.course.code}", link="/results/amendments")


# --- Resits ---------------------------------------------------------------------------

def resit_opened(resit, actor):
    offering = _offering(resit)
    notify(lecturers_of(offering), kind=Kind.RESIT_OPENED, actor=actor,
           title=f"Resit opened for {offering.course.code}", link=f"/results/resits/{resit.pk}")
    students = User.objects.filter(student_profile__course_results__resit_entries__sheet=resit,
                                   student_profile__course_results__resit_entries__status=ResitEntryStatus.ELIGIBLE)
    deadline = f" Register before {resit.registration_deadline:%d %b %Y}." if resit.registration_deadline else ""
    notify(list(students.distinct()), kind=Kind.RESIT_OPENED, actor=actor,
           title=f"Resit available: {offering.course.code}",
           body=f"You are eligible to resit {_course(offering)}.{deadline}", link="/my-results")


def resit_changed(resit, actor, status):
    offering = _offering(resit)
    link = f"/results/resits/{resit.pk}"
    code = offering.course.code
    if status == "SUBMITTED":
        notify(users_with("can_approve_results"), kind=Kind.RESIT_UPDATED, actor=actor,
               title=f"Resit scores submitted for {code}", body="Waiting for your review.", link=link)
        signoff_check(resit, offering, link)
    elif status == "RETURNED":
        recipients = lecturers_of(offering) + ([resit.submitted_by] if resit.submitted_by_id else [])
        notify(recipients, kind=Kind.RESIT_UPDATED, actor=actor, title=f"Resit scores returned for {code}",
               body="Open the resit to see the reason.", link=link)
    elif status == "APPROVED":
        notify(users_with("can_publish_results"), kind=Kind.RESIT_UPDATED, actor=actor,
               title=f"Resit ready to publish: {code}", link=link)
        signoff_check(resit, offering, link)
    elif status == "PUBLISHED":
        notify(lecturers_of(offering), kind=Kind.RESIT_UPDATED, actor=actor,
               title=f"Resit results published for {code}", link=link)
        students = User.objects.filter(student_profile__course_results__resit_entries__sheet=resit,
                                       student_profile__course_results__resit_entries__status=ResitEntryStatus.COMPLETED)
        notify(list(students.distinct()), kind=Kind.RESULTS_PUBLISHED, actor=actor,
               title=f"Resit result: {code}", body=f"Your resit result for {_course(offering)} is available.",
               link="/my-results")


# --- Appointments --------------------------------------------------------------------------

def appointment_changed(appointment, actor, *, started):
    if started:
        title = f"You have been appointed {appointment.title}"
        body = f"From {appointment.starts_on:%d %b %Y}" + (
            f" to {appointment.ends_on:%d %b %Y}." if appointment.ends_on else ".")
    else:
        title = f"Your appointment as {appointment.title} has ended"
        body = "The office's access has been removed from your account. Your own role is unchanged."
    notify([appointment.user], kind=Kind.APPOINTMENT, actor=actor, title=title, body=body, link="/profile")


# --- Registration, teaching, students ----------------------------------------------------

def registration_pending(registration, actor):
    notify(registry_staff(), kind=Kind.REGISTRATION_PENDING, actor=actor,
           title=f"Registration awaiting approval: {registration.student.student_number}",
           body=f"A course registration for {registration.semester} needs approval.",
           link=f"/registrations/{registration.pk}")


def registration_reviewed(registration, actor):
    outcome = "approved" if registration.status == "REGISTERED" else "not approved"
    notify([registration.student.user], kind=Kind.REGISTRATION_REVIEWED, actor=actor,
           title=f"Your course registration was {outcome}",
           body=f"Semester: {registration.semester}.", link="/registration")


def lecturers_assigned(offering, lecturer_ids, actor):
    offering = CourseOffering.objects.select_related("course", "semester").get(pk=offering.pk)
    notify(list(User.objects.filter(pk__in=lecturer_ids)), kind=Kind.TEACHING_ASSIGNED, actor=actor,
           title=f"You have been assigned to {offering.course.code}",
           body=f"{_course(offering)}, {offering.semester}.", link=f"/results/courses/{offering.pk}")


def progression_applied(decisions, actor):
    users = [d.student.user for d in decisions]
    notify(users, kind=Kind.PROGRESSION_APPLIED, actor=actor, email=False,
           title="Your end-of-period academic review has been recorded",
           body="Check your dashboard for your current level and standing.", link="/dashboard")


def student_status_changed(student, actor):
    notify([student.user], kind=Kind.STATUS_CHANGED, actor=actor, email=False,
           title=f"Your student status is now {student.get_status_display()}",
           body="Contact the registry if you have questions.", link="/dashboard")
