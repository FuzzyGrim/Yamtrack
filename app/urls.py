from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

from . import views

urlpatterns = [
    path("", views.home, name = "home"),
    path("search/<str:content>/<str:query>/", views.search, name = "search"),
    path("register/", views.register, name = "register"),
    path("profile/", views.profile, name = "profile"),
    path("login/", views.login, name = "login"),
    path("logout/", auth_views.LogoutView.as_view(), name = "logout"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)