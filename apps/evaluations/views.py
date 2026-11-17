from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import Role
from apps.audit.services import log_action
from apps.students.models import Student
from common.exceptions import DomainError
from common.permissions import RoleBasedPermission
from common.tabular import requested_format, table_response

from . import services
from .models import AssessmentComment, AssessmentCompletion, CommentStatus, CourseAssessment, Questionnaire
from .presets import SCALE
from .serializers import CommentReviewSerializer, QuestionnaireSerializer, SubmitSerializer

QA = (Role.ADMIN, Role.QA_OFFICER)
ID = OpenApiParameter("id", int, OpenApiParameter.PATH)


def _assessments():
    return (CourseAssessment.objects
            .select_related("offering__course__department", "offering__semester__academic_year")
            .prefetch_related("lecturers"))


# --- Students -------------------------------------------------------------------------------

class MyAssessmentsViewSet(viewsets.ViewSet):
    """A student's tutor assessments: what is waiting, the questions, and submitting answers."""

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.STUDENT,)
    write_roles = (Role.STUDENT,)

    def _student(self, request):
        student = Student.objects.filter(user=request.user).first()
        if student is None:
            raise DomainError("No student record is linked to this account.", code="not_found", status_code=404)
        return student

    def _payload(self, assessment, student, done):
        offering = assessment.offering
        return {
            "id": assessment.pk,
            "course_code": offering.course.code, "course_title": offering.course.title,
            "semester": f"{offering.semester.academic_year.name} {offering.semester.name}",
            "tutors": [{"id": u.pk, "name": u.get_full_name(), "done": (assessment.pk, u.pk) in done}
                       for u in assessment.lecturers.all()],
        }

    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="my_assessments_list")
    def list(self, request):
        """Assessments still to complete (the results they are holding back)."""
        student = self._student(request)
        waiting = services.pending(student)
        ids = [item["assessment"].pk for item in waiting.values()]
        assessments = _assessments().filter(pk__in=ids).order_by("closes_on")
        done = set(AssessmentCompletion.objects.filter(student=student, assessment_id__in=ids)
                   .values_list("assessment_id", "lecturer_id"))
        return Response([self._payload(a, student, done) for a in assessments])

    @extend_schema(parameters=[ID], responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        student = self._student(request)
        assessment = (_assessments().filter(pk=pk, offering__course_results__student=student,
                                            offering__course_results__is_published=True).distinct().first())
        if assessment is None:
            raise DomainError("This assessment is not for you.", code="not_found", status_code=404)
        done = set(AssessmentCompletion.objects.filter(student=student, assessment=assessment)
                   .values_list("assessment_id", "lecturer_id"))
        data = self._payload(assessment, student, done)
        data.update(is_open=assessment.opens_on <= timezone.localdate() <= assessment.closes_on,
                    questions=assessment.questions, scale=SCALE)
        return Response(data)

    @extend_schema(parameters=[ID], request=SubmitSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """Assess one tutor. When every tutor of the course is done, the result is returned."""
        student = self._student(request)
        payload = SubmitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        assessment = _assessments().filter(pk=pk).first()
        if assessment is None:
            raise DomainError("This assessment is not for you.", code="not_found", status_code=404)
        services.submit(student, assessment, payload.validated_data["lecturer"], payload.validated_data["answers"])
        from apps.results.services import build_academic_record

        record = services.hold(build_academic_record(student, published_only=True), student)
        course = next((c for s in record["semesters"] for c in s["courses"]
                       if c["offering_id"] == assessment.offering_id), None)
        unlocked = course is not None and not course.get("assessment_required")
        return Response({"unlocked": unlocked, "result": course if unlocked else None,
                         "assessments_pending": record["assessments_pending"]}, status=status.HTTP_201_CREATED)


# --- Quality assurance ----------------------------------------------------------------------

class QuestionnaireViewSet(viewsets.ModelViewSet):
    permission_classes = [RoleBasedPermission]
    read_roles = QA
    write_roles = QA
    serializer_class = QuestionnaireSerializer
    pagination_class = None

    def get_queryset(self):
        return Questionnaire.objects.prefetch_related("questions")

    def perform_create(self, serializer):
        questionnaire = serializer.save(created_by=self.request.user)
        log_action("evaluations.questionnaire_created", actor=self.request.user, instance=questionnaire,
                   summary=f"Created questionnaire '{questionnaire.name}'")

    def perform_update(self, serializer):
        if "questions" in serializer.validated_data:
            services.require_editable(serializer.instance)
        questionnaire = serializer.save()
        log_action("evaluations.questionnaire_updated", actor=self.request.user, instance=questionnaire,
                   summary=f"Changed questionnaire '{questionnaire.name}'")

    def perform_destroy(self, instance):
        if instance.is_active or instance.is_used:
            raise DomainError("Only a questionnaire that is not active and was never used can be deleted.",
                              code="questionnaire_in_use", status_code=409)
        log_action("evaluations.questionnaire_deleted", actor=self.request.user, instance=instance,
                   summary=f"Deleted questionnaire '{instance.name}'")
        instance.delete()

    @extend_schema(request=None, responses=QuestionnaireSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        questionnaire = services.activate_questionnaire(self.get_object(), actor=request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=questionnaire.pk)).data)

    @extend_schema(request=None, responses=QuestionnaireSerializer)
    @action(detail=True, methods=["post"])
    def copy(self, request, pk=None):
        copy = services.copy_questionnaire(self.get_object(), actor=request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=copy.pk)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=QuestionnaireSerializer)
    @action(detail=False, methods=["post"], url_path="load-preset")
    def load_preset(self, request):
        questionnaire = services.load_preset(actor=request.user,
                                             activate=not Questionnaire.objects.filter(is_active=True).exists())
        return Response(self.get_serializer(self.get_queryset().get(pk=questionnaire.pk)).data,
                        status=status.HTTP_201_CREATED)


FILTERS = [OpenApiParameter(name, str) for name in ("semester", "department", "lecturer", "search")]


class TutorAssessmentViewSet(viewsets.ViewSet):
    """Reports: per tutor and course, averages only above the minimum responses; never who said what."""

    permission_classes = [RoleBasedPermission]
    read_roles = QA
    write_roles = QA

    def _filtered(self, request):
        queryset = _assessments()
        params = request.query_params
        if params.get("semester"):
            queryset = queryset.filter(offering__semester_id=params["semester"])
        if params.get("department"):
            queryset = queryset.filter(offering__course__department_id=params["department"])
        if params.get("lecturer"):
            queryset = queryset.filter(lecturers=params["lecturer"])
        if params.get("search"):
            term = params["search"]
            queryset = queryset.filter(Q(offering__course__code__icontains=term) | Q(offering__course__title__icontains=term)
                                       | Q(lecturers__first_name__icontains=term) | Q(lecturers__last_name__icontains=term))
        return queryset.distinct().order_by("-closes_on", "offering__course__code")

    def _rows(self, request):
        rows = services.report_rows(self._filtered(request))
        lecturer = request.query_params.get("lecturer")
        if lecturer:
            rows = [r for r in rows if str(r["lecturer"]["id"]) == lecturer]
        return rows

    @extend_schema(parameters=FILTERS, responses=OpenApiTypes.OBJECT, operation_id="tutor_assessments_list")
    def list(self, request):
        rows = self._rows(request)
        pending = AssessmentComment.objects.filter(status=CommentStatus.PENDING).count()
        return Response({"results": rows, "comments_to_review": pending})

    @extend_schema(parameters=[ID], responses=OpenApiTypes.OBJECT)
    def retrieve(self, request, pk=None):
        assessment = _assessments().filter(pk=pk).first()
        if assessment is None:
            raise DomainError("Assessment not found.", code="not_found", status_code=404)
        statuses = (CommentStatus.PENDING, CommentStatus.APPROVED)
        eligible = services.eligible_count(assessment)
        tutors = []
        for lecturer in assessment.lecturers.all():
            report = services.tutor_report(assessment, lecturer, comment_statuses=statuses, eligible=eligible)
            report["trend"] = services.lecturer_trend(lecturer)
            tutors.append(report)
        return Response({"id": assessment.pk, "tutors": tutors})

    @extend_schema(parameters=FILTERS + [OpenApiParameter("file_format", str, enum=["csv", "xlsx"])],
                   responses={(200, "text/csv"): OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"])
    def export(self, request):
        rows = self._rows(request)
        labels = []
        for row in rows:
            for q in row["questions"]:
                if q["label"] not in labels:
                    labels.append(q["label"])
        header = ["Semester", "Course", "Title", "Department", "Tutor", "Responses", "Eligible", "Response rate %",
                  "Overall", *labels]
        table = []
        for row in rows:
            by_label = {q["label"]: q["average"] for q in row["questions"]}
            table.append([row["semester"], row["course_code"], row["course_title"], row["department"],
                          row["lecturer"]["name"], row["responses"], row["eligible"], row["response_rate"],
                          row["overall"] if row["overall"] is not None else "Too few responses",
                          *[by_label.get(label) for label in labels]])
        log_action("evaluations.report_exported", actor=request.user, entity_type="evaluations.courseassessment",
                   summary=f"Exported the tutor assessment report ({len(table)} rows)")
        return table_response("tutor-assessments", header, table, requested_format(request), "Tutor assessments")

    @extend_schema(parameters=[OpenApiParameter("status", str)], responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def comments(self, request):
        """Written answers to review. Shown by course and tutor, in random order, never by student."""
        wanted = request.query_params.get("status") or CommentStatus.PENDING
        comments = (AssessmentComment.objects.filter(status=wanted)
                    .select_related("assessment__offering__course", "assessment__offering__semester__academic_year",
                                    "lecturer").order_by("assessment_id", "id")[:300])
        return Response([{
            "id": str(c.id), "text": c.text, "status": c.status,
            "question": next((q["label"] for q in c.assessment.questions if q["key"] == c.question_key), ""),
            "course_code": c.assessment.offering.course.code,
            "semester": f"{c.assessment.offering.semester.academic_year.name} {c.assessment.offering.semester.name}",
            "lecturer": c.lecturer.get_full_name(),
        } for c in comments])

    @extend_schema(request=CommentReviewSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"], url_path=r"comments/(?P<comment_id>[0-9a-f-]{36})/review")
    def review_comment(self, request, comment_id=None):
        comment = AssessmentComment.objects.select_related("assessment__offering__course").filter(pk=comment_id).first()
        if comment is None:
            raise DomainError("Comment not found.", code="not_found", status_code=404)
        payload = CommentReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.review_comment(comment, status=payload.validated_data["status"],
                                reason=payload.validated_data.get("reason", ""), actor=request.user)
        return Response({"id": str(comment.id), "status": comment.status})


# --- Tutors ------------------------------------------------------------------------------------

class MyTeachingFeedbackViewSet(viewsets.ViewSet):
    """A tutor's own feedback: closed assessments only, averages above the minimum, approved comments."""

    permission_classes = [RoleBasedPermission]
    read_roles = (Role.LECTURER,)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def list(self, request):
        if not services.tutors_see_feedback():
            raise DomainError("Your institution does not share student assessments with tutors.",
                              code="feedback_not_shared", status_code=403)
        user = request.user
        today = timezone.localdate()
        closed = _assessments().filter(lecturers=user, closes_on__lt=today).order_by("-closes_on")
        open_now = _assessments().filter(lecturers=user, closes_on__gte=today).order_by("closes_on")
        return Response({
            "feedback": [services.tutor_report(a, user) for a in closed],
            "open": [{"course_code": a.offering.course.code, "course_title": a.offering.course.title,
                      "closes_on": a.closes_on} for a in open_now],  # no live counts while students answer
            "trend": services.lecturer_trend(user),
        })
