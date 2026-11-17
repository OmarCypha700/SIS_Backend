"""The built-in questionnaire, a starting point that quality assurance reviews and may change."""

PRESET_NAME = "Student assessment of tutors (standard)"

# (label for reports, question, kind, required)
PRESET_QUESTIONS = [
    ("Objectives", "The tutor explained the course objectives and outline at the start.", "RATING", True),
    ("Punctuality", "The tutor attended classes regularly and on time.", "RATING", True),
    ("Clarity", "The tutor explained topics clearly.", "RATING", True),
    ("Coverage", "The tutor covered the course outline.", "RATING", True),
    ("Participation", "The tutor encouraged questions and participation.", "RATING", True),
    ("Assessment", "Assessments reflected what was taught.", "RATING", True),
    ("Feedback", "The tutor gave helpful feedback on assignments and tests.", "RATING", True),
    ("Availability", "The tutor was available for consultation.", "RATING", True),
    ("Fairness", "The tutor treated all students fairly and with respect.", "RATING", True),
    ("Overall", "Overall, I would rate this tutor's teaching highly.", "RATING", True),
    ("Strengths", "What did the tutor do well?", "COMMENT", False),
    ("Improvements", "What could the tutor improve?", "COMMENT", False),
]

# The 1 to 5 scale, as students see it.
SCALE = {1: "Strongly disagree", 2: "Disagree", 3: "Neutral", 4: "Agree", 5: "Strongly agree"}
