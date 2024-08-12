from django.urls import path

from events import views

urlpatterns = [
    path("calendar", views.calendar, name="calendar"),
    path("reload_calendar", views.reload_calendar, name="reload_calendar"),
]
