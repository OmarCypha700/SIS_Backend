from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("registrations", views.RegistrationViewSet, basename="registration")
router.register("class-lists", views.ClassListViewSet, basename="class-list")

urlpatterns = router.urls
