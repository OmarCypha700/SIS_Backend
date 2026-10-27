from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("grading-schemes", views.GradingSchemeViewSet, basename="grading-scheme")
router.register("result-sheets", views.ResultSheetViewSet, basename="result-sheet")
router.register("result-amendments", views.AmendmentViewSet, basename="result-amendment")
router.register("my-results", views.MyResultsViewSet, basename="my-results")
router.register("resit-sheets", views.ResitSheetViewSet, basename="resit-sheet")
router.register("my-resits", views.MyResitsViewSet, basename="my-resit")

urlpatterns = router.urls
