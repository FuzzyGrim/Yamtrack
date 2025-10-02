from django.urls import path, register_converter

from app import converters, views

register_converter(converters.MediaTypeChecker, "media_type")
register_converter(converters.SourceChecker, "source")


urlpatterns = [
    path("", views.home, name="home"),
    path("medialist/<media_type:media_type>", views.media_list, name="medialist"),
    path("search", views.media_search, name="search"),
    path("hof/search", views.hof_search, name="hof_search"),
    path("hof/toggle", views.toggle_hof, name="toggle_hof"),
    path(
        "poster_modal/<source:source>/<media_type:media_type>/<str:media_id>",
        views.poster_selection_modal,
        name="poster_selection_modal",
    ),
    path(
        "poster_modal/<source:source>/season/<str:media_id>/<int:season_number>",
        views.season_poster_selection_modal,
        name="season_poster_selection_modal",
    ),
    path("save_poster", views.save_poster_preference, name="save_poster_preference"),
    path(
        "details/<source:source>/<media_type:media_type>/<str:media_id>/<str:title>",
        views.media_details,
        name="media_details",
    ),
    path(
        "details/<source:source>/tv/<str:media_id>/<str:title>/season/<int:season_number>",
        views.season_details,
        name="season_details",
    ),
    path(
        "update-score/<media_type:media_type>/<int:instance_id>",
        views.update_media_score,
        name="update_media_score",
    ),
    path(
        "details/sync/<source:source>/<media_type:media_type>/<str:media_id>",
        views.sync_metadata,
        name="sync_metadata",
    ),
    path(
        "details/sync/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.sync_metadata,
        name="sync_metadata",
    ),
    path(
        "track_modal/<source:source>/<media_type:media_type>/<str:media_id>",
        views.track_modal,
        name="track_modal",
    ),
    path(
        "track_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.track_modal,
        name="track_modal",
    ),
    path(
        "progress_edit/<media_type:media_type>/<int:instance_id>",
        views.progress_edit,
        name="progress_edit",
    ),
    path("media_save", views.media_save, name="media_save"),
    path("media_delete", views.media_delete, name="media_delete"),
    path("episode_save", views.episode_save, name="episode_save"),
    path(
        "history_modal/<source:source>/<media_type:media_type>/<str:media_id>",
        views.history_modal,
        name="history_modal",
    ),
    path(
        "history_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.history_modal,
        name="history_modal",
    ),
    path(
        "history_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>/<int:episode_number>",
        views.history_modal,
        name="history_modal",
    ),
    path(
        "media/history/<str:media_type>/<int:history_id>/delete/",
        views.delete_history_record,
        name="delete_history_record",
    ),
    path("create", views.create_entry, name="create_entry"),
    path("search/parent_tv", views.search_parent_tv, name="search_parent_tv"),
    path(
        "search/parent_season",
        views.search_parent_season,
        name="search_parent_season",
    ),
    path("statistics", views.statistics, name="statistics"),

    # Diary URLs
    path(
        "media/<media_type:media_type>/<int:instance_id>/consume/",
        views.mark_consumed,
        name="mark_consumed",
    ),
    path(
        "media/<media_type:media_type>/<int:instance_id>/diary/add/",
        views.add_diary_entry,
        name="add_diary_entry",
    ),
    path("diary/", views.diary_list, name="diary_list"),
    path(
        "media/<media_type:media_type>/<int:instance_id>/diary/",
        views.diary_item,
        name="diary_item",
    ),
    path('media/<str:source>/<str:media_type>/<str:media_id>/poster/', views.poster_selection_modal, name='poster_selection_modal'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/log/', views.log_modal, name='log_modal'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/log/season/<int:season_number>/', views.log_modal, name='log_modal_season'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/mark-watched/', views.mark_movie_watched, name='mark_movie_watched'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/unmark-watched/', views.unmark_movie_watched, name='unmark_movie_watched'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/mark-tv-watched/', views.mark_tv_watched, name='mark_tv_watched'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/unmark-tv-watched/', views.unmark_tv_watched, name='unmark_tv_watched'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/season/<int:season_number>/mark-watched/', views.mark_season_watched, name='mark_season_watched'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/season/<int:season_number>/unmark-watched/', views.unmark_season_watched, name='unmark_season_watched'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/season/<int:season_number>/episode/<int:episode_number>/watch/', views.watch_episode, name='watch_episode'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/season/<int:season_number>/episode/<int:episode_number>/unwatch/', views.unwatch_episode, name='unwatch_episode'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/diary-log/', views.add_movie_diary_entry, name='add_movie_diary_entry'),
    path('media/<source:source>/<media_type:media_type>/<str:media_id>/diary-log/season/<int:season_number>/', views.add_movie_diary_entry, name='add_season_diary_entry'),
    
    # Diary entry edit/delete URLs
    path('diary/entry/<int:entry_id>/edit/', views.edit_diary_entry, name='edit_diary_entry'),
    path('diary/entry/<int:entry_id>/update/', views.update_diary_entry, name='update_diary_entry'),
    path('diary/entry/<int:entry_id>/delete/', views.delete_diary_entry, name='delete_diary_entry'),
]
