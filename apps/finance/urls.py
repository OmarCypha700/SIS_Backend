from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter(trailing_slash=True)
router.register("financial-clearances", views.FinancialClearanceViewSet, basename="financial-clearance")

urlpatterns = router.urls
