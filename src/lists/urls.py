from django.urls import path

from lists import views

urlpatterns = [
    path("lists", views.lists, name="lists"),
    path("lists/create", views.create, name="create"),
    path("lists/edit", views.edit, name="edit"),
    path("lists/delete", views.delete, name="delete"),
    path("lists_modal", views.lists_modal, name="lists_modal"),
    path("list_item_toggle", views.list_item_toggle, name="list_item_toggle"),
]
