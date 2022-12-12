from django.urls import path

from . import views

urlpatterns = [
    path("", views.index),
    path("search/<str:content>/<str:query>/", views.search),
]