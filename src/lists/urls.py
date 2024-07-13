from django.urls import path

from lists import views

urlpatterns = [
    path("lists", views.lists, name="lists"),
    path("lists_modal", views.lists_modal, name="lists_modal"),
    path("list/<int:list_id>", views.list_detail, name="list_detail"),
    path("list/create", views.create, name="create"),
    path("list/edit", views.edit, name="edit"),
    path("list/delete", views.delete, name="delete"),
    path("list_item_toggle", views.list_item_toggle, name="list_item_toggle"),
]
