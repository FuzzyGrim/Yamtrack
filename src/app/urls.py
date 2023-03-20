from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

from decouple import config
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("medialist/<str:media_type>/", views.medialist, name="medialist"),
    path("medialist/<str:media_type>/<str:status>", views.medialist, name="medialist"),
    path("search", views.search, name="search"),
    path("profile", views.profile, name="profile"),
    path("login", views.UpdatedLoginView.as_view(extra_context={'page': 'Login'}), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
    path("edit/", views.edit, name="edit"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if config("REGISTRATION", default=True, cast=bool):
    urlpatterns.append(path("register/", views.register, name="register"))
