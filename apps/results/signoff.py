"""
Is anyone able to sign off a result sheet?

Approval (and, if the institution turns it on, publication) must be done by someone other than
the person who submitted the results. In small institutions the submitter may be the only person
holding that permission, for example a results officer who also teaches. These checks find sheets
that nobody can move forward, so the submitter and administrators are told straight away instead
of the sheet waiting unnoticed.

Works for ordinary result sheets and resit sheets.
"""
from apps.accounts.models import Role, User
from apps.institutions.models import InstitutionProfile

from .models import ResitSheet, ResultSheet, SheetStatus

STEPS = {
    "approve": ("can_approve_results", "approver_must_differ_from_submitter", "Can approve results"),
    "publish": ("can_publish_results", "publisher_must_differ_from_submitter", "Can publish results"),
}


def _holders():
    # Includes permissions that come with an office someone currently holds.
    return {step: set(User.objects.with_capability(flag).values_list("pk", flat=True))
            for step, (flag, _, _) in STEPS.items()}


def _steps_ahead(status):
    if status == SheetStatus.SUBMITTED:
        return ["approve", "publish"]
    if status == SheetStatus.APPROVED:
        return ["publish"]
    return []


def gaps(sheet, *, profile=None, holders=None):
    """Warnings for the steps no eligible person can take. Empty when sign-off can proceed."""
    profile = profile or InstitutionProfile.load()
    holders = holders if holders is not None else _holders()
    submitter = sheet.submitted_by
    warnings = []
    for step in _steps_ahead(sheet.status):
        flag, rule, label = STEPS[step]
        eligible = set(holders[step])
        must_differ = getattr(profile, rule)
        if must_differ and submitter is not None:
            eligible.discard(submitter.pk)
        if eligible:
            continue
        if must_differ and submitter is not None and submitter.pk in holders[step]:
            message = (f"No one other than {submitter.get_full_name()}, who submitted these results, can {step} them. "
                       f"An administrator needs to give another person “{label}” under Staff accounts.")
        else:
            message = (f"No active staff member can {step} results. An administrator needs to give someone "
                       f"“{label}” under Staff accounts.")
        warnings.append({"step": step, "message": message})
    return warnings


def blocked_sheets():
    """Result and resit sheets that are waiting for a step nobody can take, for dashboards."""
    profile, holders = InstitutionProfile.load(), _holders()
    waiting = (SheetStatus.SUBMITTED, SheetStatus.APPROVED)
    found = []
    for kind, model, link in (("results", ResultSheet, "/results/sheets/{}"), ("resit", ResitSheet, "/results/resits/{}")):
        for sheet in model.objects.filter(status__in=waiting).select_related("offering__course", "submitted_by"):
            for warning in gaps(sheet, profile=profile, holders=holders):
                found.append({"kind": kind, "id": sheet.pk, "course_code": sheet.offering.course.code,
                              "status": sheet.status, "link": link.format(sheet.pk), **warning})
    return found
