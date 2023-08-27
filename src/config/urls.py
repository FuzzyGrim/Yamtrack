"""Yamtrack base URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import include, path
from django.contrib import admin
from django.conf import settings
from app import views


def error_404_view(request, exception=None):
    return views.error_view(request, status_code=404)


def error_400_view(request, exception=None):
    return views.error_view(request, status_code=400)


def error_403_view(request, exception=None):
    return views.error_view(request, status_code=403)


def error_500_view(request, exception=None):
    return views.error_view(request, status_code=500)


handler404 = error_404_view
handler400 = error_400_view
handler403 = error_403_view
handler500 = error_500_view

urlpatterns = [
    path("", include("app.urls")),
    path("", include("integrations.urls")),
    path("", include("users.urls")),
]

if settings.ADMIN_ENABLED:
    urlpatterns.append(path("admin/", admin.site.urls))

if settings.DEBUG:
    import debug_toolbar  # noqa

    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
