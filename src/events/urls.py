from django.urls import path

from events import views

urlpatterns = [
    path("refresh_calendar", views.refresh_calendar, name="refresh_calendar"),
]
