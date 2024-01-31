"""Yamtrack base URL Configuration.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/stable/topics/http/urls/

"""
from django.conf import settings
from django.urls import include, path

urlpatterns = [
    path("", include("app.urls")),
    path("", include("integrations.urls")),
    path("", include("users.urls")),
]


if settings.DEBUG:
    import debug_toolbar  # noqa: F401

    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
