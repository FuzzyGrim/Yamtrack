from decouple import config
from django.contrib.auth import views as auth_views
from django.urls import path

from users import views

urlpatterns = [
    path("profile", views.profile, name="profile"),
    path("login", views.CustomLoginView.as_view(), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
]

if config("REGISTRATION", default=True, cast=bool):
    urlpatterns.append(path("register/", views.register, name="register"))
