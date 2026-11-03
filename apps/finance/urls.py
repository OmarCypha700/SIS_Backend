from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter(trailing_slash=True)
router.register("financial-clearances", views.FinancialClearanceViewSet, basename="financial-clearance")
router.register("fee-schedules", views.FeeScheduleViewSet, basename="fee-schedule")
router.register("student-accounts", views.StudentAccountViewSet, basename="student-account")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("my-fees", views.MyFeesViewSet, basename="my-fees")

urlpatterns = [
    path("payments/paystack/webhook/", views.PaystackWebhookView.as_view(), name="paystack-webhook"),
    *router.urls,
]
