"""Course catalogue import, optionally placing each course in a programme's curriculum."""
from django.db import transaction
from rest_framework import serializers

from apps.audit.services import log_action
from common.exceptions import DomainError
from common.importing import row_result, serializer_errors

from .models import Course, CourseCategory, CourseType, Department, Level, Programme, ProgrammeCourse

COLUMNS = [
    "code", "title", "credits", "department_code", "course_type", "description", "prerequisites",
    "programme_code", "level", "term_number", "category",
]
REQUIRED = {"code", "title", "credits", "department_code"}
TEMPLATE_ROWS = [
    ["CS101", "Introduction to Computing", "3", "CS", "THEORY", "", "", "BSC-CS", "Level 100", "1", "COMPULSORY"],
    ["CS201", "Data Structures", "3", "CS", "THEORY", "", "CS101", "BSC-CS", "Level 200", "1", "COMPULSORY"],
]


class CourseRowSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    title = serializers.CharField(max_length=200)
    credits = serializers.IntegerField(min_value=1, max_value=60)
    department_code = serializers.SlugRelatedField(slug_field="code", queryset=Department.objects.filter(is_active=True))
    course_type = serializers.ChoiceField(choices=CourseType.choices, required=False, default=CourseType.THEORY)
    description = serializers.CharField(required=False, allow_blank=True)
    prerequisites = serializers.CharField(required=False, allow_blank=True)
    programme_code = serializers.SlugRelatedField(slug_field="code", queryset=Programme.objects.all(), required=False,
                                                  allow_null=True)
    level = serializers.SlugRelatedField(slug_field="name", queryset=Level.objects.all(), required=False, allow_null=True)
    term_number = serializers.IntegerField(min_value=1, max_value=12, required=False, allow_null=True)
    category = serializers.ChoiceField(choices=CourseCategory.choices, required=False, default=CourseCategory.COMPULSORY)

    def to_internal_value(self, data):
        data = dict(data)
        for key in ("code", "department_code", "programme_code", "course_type", "category"):
            if data.get(key):
                data[key] = data[key].strip().upper()
        for key in ("programme_code", "level", "term_number", "course_type", "category"):
            if data.get(key, "") == "":
                data.pop(key, None)
        return super().to_internal_value(data)

    def validate(self, attrs):
        programme = attrs.get("programme_code")
        if programme:
            if not attrs.get("level") or not attrs.get("term_number"):
                raise serializers.ValidationError({"level": "Level and term_number are required when programme_code is given."})
            if programme.levels.exists() and not programme.levels.filter(pk=attrs["level"].pk).exists():
                raise serializers.ValidationError({"level": f"{attrs['level']} is not a level of {programme.code}."})
        attrs["prerequisite_codes"] = [c.strip().upper() for c in (attrs.pop("prerequisites", "") or "").replace(",", ";").split(";") if c.strip()]
        return attrs


def validate_rows(rows):
    results, seen = [], {}
    parsed = []
    for index, raw in enumerate(rows, start=2):
        serializer = CourseRowSerializer(data=raw)
        if serializer.is_valid():
            parsed.append((index, raw, dict(serializer.validated_data), {}))
        else:
            parsed.append((index, raw, None, serializer_errors(serializer)))

    codes_in_file = {d["code"] for _, _, d, _ in parsed if d}
    existing = set(Course.objects.filter(code__in=codes_in_file).values_list("code", flat=True))
    known = set(Course.objects.values_list("code", flat=True)) | codes_in_file
    graph = {d["code"]: set(d["prerequisite_codes"]) for _, _, d, _ in parsed if d}
    for code, prereqs in Course.prerequisites.through.objects.values_list("from_course__code", "to_course__code"):
        graph.setdefault(code, set()).add(prereqs)

    for index, raw, data, errors in parsed:
        if data:
            code = data["code"]
            if code in existing:
                errors.setdefault("code", []).append("A course with this code already exists.")
            if code in seen:
                errors.setdefault("code", []).append(f"Duplicate of row {seen[code]} in this file.")
            seen.setdefault(code, index)
            unknown = [p for p in data["prerequisite_codes"] if p not in known]
            if unknown:
                errors.setdefault("prerequisites", []).append(f"Unknown course code(s): {', '.join(unknown)}.")
            if code in data["prerequisite_codes"] or _reaches(graph, data["prerequisite_codes"], code):
                errors.setdefault("prerequisites", []).append("These prerequisites would create a circular requirement.")
        results.append(row_result(index, raw, data, errors))
    return results


def _reaches(graph, start, target):
    stack, seen = list(start), set()
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, ()))
    return False


@transaction.atomic
def import_courses(rows, *, actor):
    results = validate_rows(rows)
    invalid = [r for r in results if not r["valid"]]
    if invalid:
        raise DomainError(f"{len(invalid)} row(s) have errors. Nothing was imported.", code="import_invalid",
                          errors=[{"row": r["row"], "errors": r["errors"]} for r in invalid])
    created = {}
    for result in results:
        data = result["data"]
        created[data["code"]] = Course.objects.create(
            code=data["code"], title=data["title"], credits=data["credits"], department=data["department_code"],
            course_type=data["course_type"], description=data.get("description", ""),
        )
    placements = 0
    for result in results:
        data = result["data"]
        course = created[data["code"]]
        if data["prerequisite_codes"]:
            prerequisites = [created.get(c) or Course.objects.get(code=c) for c in data["prerequisite_codes"]]
            course.prerequisites.set(prerequisites)
        if data.get("programme_code"):
            ProgrammeCourse.objects.create(programme=data["programme_code"], course=course, level=data["level"],
                                           term_number=data["term_number"], category=data["category"])
            placements += 1
    log_action("course.imported", actor=actor, entity_type="academics.course",
               summary=f"Imported {len(created)} courses ({placements} curriculum placements)",
               metadata={"codes": sorted(created)[:500]})
    return len(created), placements
