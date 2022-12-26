from django.urls import path
from django.contrib.auth import views as auth_views

from . import views

urlpatterns = [
    path("", views.home, name = "home"),
    path("search/<str:content>/<str:query>/", views.search, name = "search"),
    path("register/", views.register, name = "register"),
    path("profile/", views.profile, name = "profile"),
    path("login/", views.login, name = "login"),
    path("logout/", auth_views.LogoutView.as_view(), name = "logout"),
]