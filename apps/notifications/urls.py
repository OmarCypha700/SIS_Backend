from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("notifications", views.NotificationViewSet, basename="notification")

urlpatterns = [
    path("notifications/push/public-key/", views.PushPublicKeyView.as_view(), name="push-public-key"),
    path("notifications/push/subscribe/", views.PushSubscribeView.as_view(), name="push-subscribe"),
    path("notifications/push/unsubscribe/", views.PushUnsubscribeView.as_view(), name="push-unsubscribe"),
    path("notifications/push/test/", views.PushTestView.as_view(), name="push-test"),
    path("notifications/delivery-status/", views.DeliveryStatusView.as_view(), name="notification-delivery-status"),
    *router.urls,
]
