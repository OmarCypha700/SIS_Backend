from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("transcripts", views.TranscriptViewSet, basename="transcript")

urlpatterns = [
    path("transcripts/verify/<str:code>/", views.VerifyTranscriptView.as_view(), name="transcript-verify"),
    *router.urls,
]
