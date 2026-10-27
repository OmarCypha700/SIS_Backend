from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("progression-reviews", views.ProgressionReviewViewSet, basename="progression-review")
router.register("progression-decisions", views.ProgressionDecisionViewSet, basename="progression-decision")

urlpatterns = router.urls
