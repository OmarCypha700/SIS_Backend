from django.urls import path
from rest_framework.routers import DefaultRouter

from . import device_views, passkey_views, views

router = DefaultRouter(trailing_slash=True)
router.register("users", views.UserViewSet, basename="user")
router.register("appointments", views.AppointmentViewSet, basename="appointment")

urlpatterns = [
    path("auth/csrf/", views.CsrfView.as_view(), name="auth-csrf"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/password/change/", views.PasswordChangeView.as_view(), name="auth-password-change"),
    path("auth/password/reset/", views.PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path("auth/password/reset/confirm/", views.PasswordResetConfirmView.as_view(), name="auth-password-reset-confirm"),
    path("auth/devices/", device_views.SignedInDeviceListView.as_view(), name="auth-devices"),
    path("auth/devices/<int:pk>/", device_views.SignedInDeviceDetailView.as_view(), name="auth-device"),
    path("auth/devices/sign-out-others/", device_views.SignOutOtherDevicesView.as_view(),
         name="auth-devices-sign-out-others"),
    path("auth/passkeys/", passkey_views.PasskeyListView.as_view(), name="auth-passkeys"),
    path("auth/passkeys/<int:pk>/", passkey_views.PasskeyDetailView.as_view(), name="auth-passkey"),
    path("auth/passkeys/register/options/", passkey_views.PasskeyRegisterOptionsView.as_view(),
         name="auth-passkey-register-options"),
    path("auth/passkeys/register/", passkey_views.PasskeyRegisterView.as_view(), name="auth-passkey-register"),
    path("auth/passkeys/sign-in/options/", passkey_views.PasskeySignInOptionsView.as_view(),
         name="auth-passkey-sign-in-options"),
    path("auth/passkeys/sign-in/", passkey_views.PasskeySignInView.as_view(), name="auth-passkey-sign-in"),
    *router.urls,
]
