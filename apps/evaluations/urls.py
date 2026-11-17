from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter(trailing_slash=True)
router.register("my-assessments", views.MyAssessmentsViewSet, basename="my-assessment")
router.register("questionnaires", views.QuestionnaireViewSet, basename="questionnaire")
router.register("tutor-assessments", views.TutorAssessmentViewSet, basename="tutor-assessment")
router.register("my-teaching-feedback", views.MyTeachingFeedbackViewSet, basename="my-teaching-feedback")

urlpatterns = router.urls
