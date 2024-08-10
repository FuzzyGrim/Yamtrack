from django.urls import path

from events import views

urlpatterns = [
    path("calendar", views.calendar, name="calendar"),
    path("refresh_events", views.refresh_events, name="refresh_events"),
]
