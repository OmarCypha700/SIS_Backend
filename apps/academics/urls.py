from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("academic-years", views.AcademicYearViewSet, basename="academic-year")
router.register("semesters", views.SemesterViewSet, basename="semester")
router.register("departments", views.DepartmentViewSet, basename="department")
router.register("levels", views.LevelViewSet, basename="level")
router.register("programmes", views.ProgrammeViewSet, basename="programme")
router.register("courses", views.CourseViewSet, basename="course")
router.register("programme-courses", views.ProgrammeCourseViewSet, basename="programme-course")
router.register("course-offerings", views.CourseOfferingViewSet, basename="course-offering")

urlpatterns = [
    path("academic/current-period/", views.CurrentPeriodView.as_view(), name="current-period"),
    *router.urls,
]
