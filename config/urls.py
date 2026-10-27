from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1 = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.institutions.urls")),
    path("", include("apps.academics.urls")),
    path("", include("apps.students.urls")),
    path("", include("apps.registration.urls")),
    path("", include("apps.results.urls")),
    path("", include("apps.progression.urls")),
    path("", include("apps.audit.urls")),
    path("", include("apps.dashboard.urls")),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

urlpatterns = [path("api/v1/", include(api_v1))]

if settings.DJANGO_ADMIN_ENABLED:
    urlpatterns.append(path("django-admin/", admin.site.urls))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
