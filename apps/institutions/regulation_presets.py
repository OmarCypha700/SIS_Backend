"""
Ready-made academic regulation settings that an administrator can load, review and save.

A preset only fills in the settings form; nothing is applied until it is saved, and every
value can still be changed. Rules that the policy does not state are left switched off.
"""

PRESETS = {
    "health_training_institutions": {
        "name": "Health Training Institutions (Ghana) - examination policy",
        "summary": (
            "Proceed to the next level with CGPA of at least 1.5 and at most 2 referred courses; "
            "repeat the class with CGPA below 1.5 or 3 referred courses; withdrawal with CGPA of 1.0 or less, "
            "a second repeat, or more than 3 referred courses. Referred courses are written as resits."
        ),
        "values": {
            # (e) referred courses are written and must be passed
            "resits_enabled": True,
            "resit_min_total": None,
            "max_resits_per_course": 1,
            "resit_max_grade_point": None,
            "retake_max_grade_point": None,
            "max_attempts_per_course": None,
            "failed_compulsory_must_be_retaken": True,
            # (d) proceed: CGPA >= 1.5 and at most two referred courses
            "promotion_min_cgpa": "1.50",
            "max_referred_courses_to_proceed": 2,
            "promotion_min_credits_per_year": None,
            "promotion_max_outstanding_failed_credits": None,
            # (f) otherwise repeat the class
            "repeat_level_when_not_promoted": True,
            # (g) withdrawal
            "dismissal_cgpa_below": "1.00",
            "dismissal_cgpa_inclusive": True,
            "dismiss_when_referred_courses_over": 3,
            "dismiss_on_second_repeat": True,
            "dismissal_status": "WITHDRAWN",
            # not part of this policy
            "probation_cgpa_below": None,
            "dismiss_after_consecutive_probations": None,
            "dismiss_when_attempts_exhausted": False,
            "reference": (
                "Health Training Institutions examination policy, student obligations 1(d)-(g): proceed to the next "
                "level with a minimum CGPA of 1.5 and a maximum of two (2) referred courses; write and pass referred "
                "courses; repeat the class with a CGPA below 1.5 or three (3) referred courses; withdrawal with a CGPA "
                "of 1.0 or less, a second repeat of a class, or more than three (3) referred courses. Item 1(b)-(c) "
                "(attendance) and 1(h) (graduation) are not applied by this system."
            ),
        },
    },
}


def preset_list():
    return [{"key": key, "name": p["name"], "summary": p["summary"], "values": p["values"]} for key, p in PRESETS.items()]
