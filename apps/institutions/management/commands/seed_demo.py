"""
Populate an empty development database with clearly fictitious demonstration data.

Every person and institution created here is synthetic. Never run this against a
database that holds real student records.
"""
import datetime
import os
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.academics.models import (
    AcademicYear, AwardType, Course, CourseCategory, CourseOffering, CourseType, Department, LecturerAssignment,
    Level, OfferingStatus, PeriodStatus, Programme, ProgrammeCourse, Semester,
)
from apps.accounts.models import Role, User
from apps.institutions.models import InstitutionProfile
from apps.registration.services import submit_registration
from apps.results import services as results_services
from apps.results.defaults import create_standard_scheme
from apps.students.models import Student
from apps.students.services import create_student

DEFAULT_DEMO_PASSWORD = "Demo-Password-2026"

FIRST_NAMES = ["Ama", "Kofi", "Akosua", "Kwame", "Abena", "Yaw", "Efua", "Kojo", "Adwoa", "Kwabena", "Esi",
               "Kwaku", "Afia", "Kweku", "Akua", "Fiifi", "Nana", "Selorm", "Dzifa", "Elikem", "Naa", "Nii",
               "Fatima", "Ibrahim", "Zainab", "Abdul", "Mariam", "Yusif"]
LAST_NAMES = ["Mensah", "Owusu", "Boateng", "Asante", "Osei", "Agyeman", "Appiah", "Darko", "Ofori", "Amoah",
              "Quaye", "Tetteh", "Addo", "Lamptey", "Adjei", "Annan", "Danso", "Sarpong", "Mahama", "Alhassan"]

PROGRAMMES = [
    ("NUR", "Nursing", "Diploma in General Nursing", AwardType.DIPLOMA, 3, [
        ("NUR101", "Foundations of Nursing", 3, 1, 1, CourseCategory.COMPULSORY, CourseType.THEORY, []),
        ("NUR103", "Anatomy and Physiology I", 3, 1, 1, CourseCategory.COMPULSORY, CourseType.THEORY, []),
        ("NUR105", "Communication Skills", 2, 1, 1, CourseCategory.COMPULSORY, CourseType.THEORY, []),
        ("NUR107", "Introduction to Psychology", 2, 1, 1, CourseCategory.ELECTIVE, CourseType.THEORY, []),
        ("NUR109", "Health Informatics Basics", 2, 1, 1, CourseCategory.ELECTIVE, CourseType.PRACTICAL, []),
        ("NUR201", "Medical-Surgical Nursing I", 3, 2, 1, CourseCategory.COMPULSORY, CourseType.THEORY, ["NUR101"]),
        ("NUR203", "Pharmacology", 3, 2, 1, CourseCategory.COMPULSORY, CourseType.THEORY, ["NUR103"]),
        ("NUR205", "Clinical Practicum I", 3, 2, 1, CourseCategory.COMPULSORY, CourseType.CLINICAL, ["NUR101"]),
        ("NUR207", "Community Health Nursing", 2, 2, 1, CourseCategory.ELECTIVE, CourseType.THEORY, []),
        ("NUR102", "Anatomy and Physiology II", 3, 1, 2, CourseCategory.COMPULSORY, CourseType.THEORY, ["NUR103"]),
    ]),
    ("CS", "Computing", "BSc Information Technology", AwardType.BACHELOR, 4, [
        ("ITC101", "Introduction to Programming", 3, 1, 1, CourseCategory.COMPULSORY, CourseType.THEORY, []),
        ("ITC103", "Discrete Mathematics", 3, 1, 1, CourseCategory.COMPULSORY, CourseType.THEORY, []),
        ("ITC105", "Computer Hardware Essentials", 2, 1, 1, CourseCategory.COMPULSORY, CourseType.PRACTICAL, []),
        ("ITC107", "Academic Writing", 2, 1, 1, CourseCategory.ELECTIVE, CourseType.THEORY, []),
        ("ITC109", "Digital Literacy", 2, 1, 1, CourseCategory.ELECTIVE, CourseType.PRACTICAL, []),
        ("ITC201", "Data Structures", 3, 2, 1, CourseCategory.COMPULSORY, CourseType.THEORY, ["ITC101"]),
        ("ITC203", "Database Systems", 3, 2, 1, CourseCategory.COMPULSORY, CourseType.THEORY, ["ITC101"]),
        ("ITC205", "Web Development", 3, 2, 1, CourseCategory.ELECTIVE, CourseType.PRACTICAL, ["ITC101"]),
        ("ITC102", "Object-Oriented Programming", 3, 1, 2, CourseCategory.COMPULSORY, CourseType.THEORY, ["ITC101"]),
    ]),
]


class Command(BaseCommand):
    help = "Create fictitious demonstration data (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=40)
        parser.add_argument("--force", action="store_true", help="Allow running when DEBUG is off.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("seed_demo only runs with DEBUG on. Use --force for a disposable demo server.")
        if Student.objects.exists() or AcademicYear.objects.exists():
            raise CommandError("The database already has academic data. seed_demo only runs on an empty database.")
        password = os.environ.get("DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD)
        random.seed(2026)
        with transaction.atomic():
            self._seed(password, options["students"])
        self.stdout.write(self.style.SUCCESS("Demo data created. Sign-in details are listed in the README."))

    def _user(self, email, role, first, last, password, **extra):
        return User.objects.create_user(email=email, password=password, role=role, first_name=first, last_name=last,
                                        **extra)

    def _seed(self, password, student_count):
        profile = InstitutionProfile.load()
        profile.name = "Northridge College of Health and Technology (Demo)"
        profile.short_name = "NCHT"
        profile.motto = "Knowledge in service"
        profile.email = "registry@northridge.example"
        profile.phone = "+233 30 000 0000"
        profile.physical_address = "1 Demo Avenue, Accra"
        profile.result_slip_footer = "This is a demonstration document and has no official standing."
        profile.save()
        create_standard_scheme()

        admin = self._user("admin@northridge.example", Role.ADMIN, "Adwoa", "Admin", password)
        self._user("registrar@northridge.example", Role.REGISTRAR, "Kwesi", "Registrar", password)
        officer = self._user("exams@northridge.example", Role.RESULTS_OFFICER, "Efua", "Examiner", password)
        publisher = User.objects.get(email="registrar@northridge.example")
        lecturers = [
            self._user("lecturer@northridge.example", Role.LECTURER, "Yaw", "Lecturer", password, title="Dr."),
            self._user("lecturer2@northridge.example", Role.LECTURER, "Akua", "Teacher", password, title="Mrs."),
            self._user("lecturer3@northridge.example", Role.LECTURER, "Nii", "Tutor", password, title="Mr."),
        ]

        levels = [Level.objects.create(name=f"Level {n}00", order=n) for n in range(1, 5)]
        now = timezone.now()
        today = now.date()
        start_year = today.year if today.month >= 8 else today.year - 1
        previous_year = AcademicYear.objects.create(
            name=f"{start_year - 1}/{start_year}", start_date=datetime.date(start_year - 1, 8, 1),
            end_date=datetime.date(start_year, 7, 31), status=PeriodStatus.CLOSED)
        year = AcademicYear.objects.create(
            name=f"{start_year}/{start_year + 1}", start_date=datetime.date(start_year, 8, 1),
            end_date=datetime.date(start_year + 1, 7, 31), status=PeriodStatus.ACTIVE)
        past = Semester.objects.create(
            academic_year=previous_year, name="First Semester", term_number=1,
            start_date=datetime.date(start_year - 1, 9, 1), end_date=datetime.date(start_year - 1, 12, 20),
            registration_start=timezone.make_aware(datetime.datetime(start_year - 1, 8, 20)),
            registration_end=timezone.make_aware(datetime.datetime(start_year - 1, 9, 20)),
            status=PeriodStatus.ACTIVE)
        current = Semester.objects.create(
            academic_year=year, name="First Semester", term_number=1,
            start_date=max(year.start_date, today - datetime.timedelta(days=30)),
            end_date=min(year.end_date, today + datetime.timedelta(days=90)),
            registration_start=now - datetime.timedelta(days=7), registration_end=now + datetime.timedelta(days=21),
            add_drop_deadline=now + datetime.timedelta(days=28),
            result_entry_deadline=now + datetime.timedelta(days=100), status=PeriodStatus.ACTIVE, is_current=True)
        Semester.objects.create(
            academic_year=year, name="Second Semester", term_number=2,
            start_date=min(year.end_date - datetime.timedelta(days=120), today + datetime.timedelta(days=120)),
            end_date=year.end_date, registration_start=now + datetime.timedelta(days=110),
            registration_end=now + datetime.timedelta(days=130), status=PeriodStatus.PLANNED)

        programmes = []
        for dept_code, dept_name, prog_name, award, duration, courses in PROGRAMMES:
            department = Department.objects.create(code=dept_code, name=f"Department of {dept_name}")
            programme = Programme.objects.create(
                code=f"{dept_code}-{award[:3]}", name=prog_name, department=department, award_type=award,
                duration_years=duration, required_credits=duration * 36, min_credits_per_semester=6,
                max_credits_per_semester=18)
            programme.levels.set(levels[:duration])
            created = {}
            for code, title, credits, level, term, category, ctype, prereqs in courses:
                course = Course.objects.create(code=code, title=title, credits=credits, department=department,
                                               course_type=ctype)
                course.prerequisites.set([created[p] for p in prereqs])
                created[code] = course
                ProgrammeCourse.objects.create(programme=programme, course=course, level=levels[level - 1],
                                               term_number=term, category=category)
            programmes.append(programme)

        for semester in (past, current):
            for link in ProgrammeCourse.objects.filter(term_number=1).select_related("programme", "level"):
                offering, _ = CourseOffering.objects.get_or_create(course=link.course, semester=semester,
                                                                   defaults={"status": OfferingStatus.OPEN})
                offering.programmes.add(link.programme)
                offering.levels.add(link.level)
        for index, offering in enumerate(CourseOffering.objects.order_by("semester_id", "course__code")):
            LecturerAssignment.objects.get_or_create(offering=offering, lecturer=lecturers[index % len(lecturers)],
                                                     defaults={"is_coordinator": True})
        # Make sure the main demo lecturer teaches at least one Level 100 course this semester.
        for code in ("ITC101", "NUR101"):
            offering = CourseOffering.objects.get(course__code=code, semester=current)
            LecturerAssignment.objects.get_or_create(offering=offering, lecturer=lecturers[0])

        students = []
        for i in range(student_count):
            programme = programmes[i % len(programmes)]
            returning = i % 2 == 0
            first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
            email = "student@northridge.example" if i == 0 else f"{first}.{last}.{i}@students.northridge.example".lower()
            data = {
                "first_name": first, "last_name": last, "email": email, "programme": programme,
                "current_level": levels[0], "admission_year": start_year - 1 if returning else start_year,
                "gender": random.choice(["F", "M"]),
                "date_of_birth": datetime.date(random.randint(1998, 2006), random.randint(1, 12), random.randint(1, 28)),
                "phone": f"024{random.randint(1000000, 9999999)}",
            }
            student = create_student(data, actor=admin, password=password)
            student.user.must_change_password = False
            student.user.save(update_fields=["must_change_password"])
            students.append((student, returning))

        # Last year's first semester: returning students registered, results published.
        Semester.objects.filter(pk=past.pk).update(
            registration_start=now - datetime.timedelta(days=1), registration_end=now + datetime.timedelta(days=1))
        past.refresh_from_db()
        returning_students = [s for s, returning in students if returning]
        for student in returning_students:
            options = [o for o in self._options(student, past) if o.eligible]
            chosen = [o.offering.pk for o in options if o.is_compulsory or random.random() < 0.5]
            submit_registration(student, past, chosen, actor=admin)
        for offering in CourseOffering.objects.filter(semester=past, registrations__isnull=False).distinct():
            self._grade_offering(offering, officer, publisher)
        Semester.objects.filter(pk=past.pk).update(
            registration_start=timezone.make_aware(datetime.datetime(start_year - 1, 8, 20)),
            registration_end=timezone.make_aware(datetime.datetime(start_year - 1, 9, 20)), status=PeriodStatus.CLOSED)

        # Returning students move up a level; about half of all students register now.
        for student, returning in students:
            if returning:
                student.current_level = levels[1]
                student.save(update_fields=["current_level"])
        for student, _ in students[1::2]:
            options = [o for o in self._options(student, current) if o.eligible]
            chosen = [o.offering.pk for o in options if o.is_compulsory]
            chosen += [o.offering.pk for o in options if not o.is_compulsory][:1]
            if chosen:
                submit_registration(student, current, chosen, actor=student.user)

    @staticmethod
    def _options(student, semester):
        from apps.registration.services import build_options
        return build_options(student, semester).options.values()

    @staticmethod
    def _grade_offering(offering, officer, publisher):
        lecturer = offering.assignments.first().lecturer
        sheet = results_services.get_or_create_sheet(offering)
        results_services.replace_components(sheet, [
            {"name": "Continuous assessment", "weight": 40, "max_score": 40, "is_required": True},
            {"name": "End of semester examination", "weight": 60, "max_score": 100, "is_required": True},
        ], actor=lecturer)
        components = list(offering.assessment_components.all())
        entries = []
        for registration in offering.registrations.filter(status="ACTIVE"):
            ability = random.uniform(0.35, 0.95)
            if registration.student.email == "student@northridge.example":
                ability = 0.85  # keeps the main demo student's path through the demo predictable
            entries.append({"registration": registration.id, "component": components[0].id,
                            "score": round(min(40, 40 * ability + random.uniform(-4, 4)), 1)})
            entries.append({"registration": registration.id, "component": components[1].id,
                            "score": round(max(0, min(100, 100 * ability + random.uniform(-10, 10))), 1)})
        results_services.save_marks(sheet, entries, actor=lecturer)
        results_services.submit_sheet(sheet, actor=lecturer)
        results_services.approve_sheet(sheet, actor=officer)
        results_services.publish_sheet(sheet, actor=publisher)
