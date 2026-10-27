from django.urls import path

from . import views

urlpatterns = [
    path("institution/", views.InstitutionBrandingView.as_view(), name="institution-branding"),
    path("institution/settings/", views.InstitutionSettingsView.as_view(), name="institution-settings"),
    path("academic-regulations/", views.AcademicRegulationView.as_view(), name="academic-regulations"),
    path("academic-regulations/presets/", views.AcademicRegulationPresetsView.as_view(),
         name="academic-regulation-presets"),
]
