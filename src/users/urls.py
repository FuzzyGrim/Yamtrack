from django.urls import path

from users import views

urlpatterns = [
    path("settings/account", views.account, name="account"),
    path("settings/notifications", views.notifications, name="notifications"),
    path("notifications/search/", views.search_items, name="search_notification_items"),
    path(
        "notifications/exclude/",
        views.exclude_item,
        name="exclude_notification_item",
    ),
    path(
        "notifications/include/",
        views.include_item,
        name="include_notification_item",
    ),
    path("test_notification", views.test_notification, name="test_notification"),
    path("settings/sidebar", views.sidebar, name="sidebar"),
    path("settings/integrations", views.integrations, name="integrations"),
    path("settings/import", views.import_data, name="import_data"),
    path("settings/export", views.export_data, name="export_data"),
    path("settings/advanced", views.advanced, name="advanced"),
    path("settings/about", views.about, name="about"),
    path(
        "delete_import_schedule",
        views.delete_import_schedule,
        name="delete_import_schedule",
    ),
    path("regenerate_token", views.regenerate_token, name="regenerate_token"),
    path("clear_search_cache", views.clear_search_cache, name="clear_search_cache"),
    path(
        "update_plex_usernames",
        views.update_plex_usernames,
        name="update_plex_usernames",
    ),
]
