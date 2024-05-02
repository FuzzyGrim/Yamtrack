from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from users import views

urlpatterns = [
    path("profile", views.profile, name="profile"),
    path("tasks", views.tasks, name="tasks"),
    path("login", views.CustomLoginView.as_view(), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
]

if settings.REGISTRATION:
    urlpatterns.append(path("register/", views.register, name="register"))
