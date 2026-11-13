from django.contrib.auth import views as auth
from django.urls import include, path
from documents.auth_views import AccountView, registration_settings

urlpatterns = [
    path("login/", AccountView.as_view(), name="login"),
    path("invite/<uuid:token>/", AccountView.as_view(), name="register_invited"),
    path("registration/", registration_settings, name="registration_settings"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("", include("documents.urls")),
]
