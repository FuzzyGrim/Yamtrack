from django.urls import path
from app import views

urlpatterns = [
    # Home
    path("", views.home, name="home"),

    # Lists and search
    path("search", views.media_search, name="search"),
    path("media/<media_type:media_type>", views.media_list, name="medialist"),

    # Details
    path(
        "details/<source:source>/<media_type:media_type>/<str:media_id>/<slug:title>",
        views.media_details,
        name="media_details",
    ),
    path(
        "details/<source:source>/<str:media_id>/<slug:title>/season/<int:season_number>",
        views.season_details,
        name="season_details",
    ),

    # Track/history modals
    path(
        "track/<source:source>/<media_type:media_type>/<str:media_id>",
        views.track_modal,
        name="track_modal",
    ),
    path(
        "log/<source:source>/<media_type:media_type>/<str:media_id>",
        views.log_modal,
        name="log_modal",
    ),
    path(
        "log/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.log_modal,
        name="log_modal",
    ),
    path(
        "history/<source:source>/<media_type:media_type>/<str:media_id>",
        views.history_modal,
        name="history_modal",
    ),
    path(
        "history/<media_type:media_type>/<int:history_id>/delete",
        views.delete_history_record,
        name="delete_history_record",
    ),

    # Create/edit/delete media
    path("media/save", views.media_save, name="media_save"),
    path("media/delete", views.media_delete, name="media_delete"),
    path("episode/save", views.episode_save, name="episode_save"),
    path(
        "progress/<media_type:media_type>/<int:instance_id>",
        views.progress_edit,
        name="progress_edit",
    ),
    # Book actions
    path(
        "book/mark_read/<source:source>/<str:media_id>",
        views.mark_book_read,
        name="mark_book_read",
    ),
    path(
        "book/start/<source:source>/<str:media_id>",
        views.start_reading_book,
        name="start_reading_book",
    ),

    # Entry creation
    path("create", views.create_entry, name="create_entry"),
    path("search_parent_tv", views.search_parent_tv, name="search_parent_tv"),
    path(
        "search_parent_season",
        views.search_parent_season,
        name="search_parent_season",
    ),

    # Posters
    path(
        "season_poster_selection/<source:source>/<str:media_id>/<int:season_number>",
        views.season_poster_selection_modal,
        name="season_poster_selection_modal",
    ),
    path("save_poster_preference", views.save_poster_preference, name="save_poster_preference"),

    # Diary entries
    path(
        "diary/add/<source:source>/<media_type:media_type>/<str:media_id>",
        views.add_movie_diary_entry,
        name="add_movie_diary_entry",
    ),
    path(
        "diary/add/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.add_movie_diary_entry,
        name="add_movie_diary_entry",
    ),
    path("diary/delete/<int:entry_id>", views.delete_diary_entry, name="delete_diary_entry"),

    # Statistics
    path("statistics", views.statistics, name="statistics"),

    # Hall of Fame (HOF)
    path("hof/search", views.hof_search, name="hof_search"),
    path("hof/toggle", views.toggle_hof, name="toggle_hof"),

    # Poster/book cover selection
    path(
        "poster_selection/<source:source>/<media_type:media_type>/<str:media_id>",
        views.poster_selection_modal,
        name="poster_selection_modal",
    ),
    path(
        "book_cover_selection/<source:source>/<str:media_id>",
        views.book_cover_selection_modal,
        name="book_cover_selection_modal",
    ),
    path(
        "book_cover_selection/content/<source:source>/<str:media_id>",
        views.book_cover_selection_content,
        name="book_cover_selection_content",
    ),

    # Movie watch/unwatch
    path(
        "movie/watch/<source:source>/<str:media_id>",
        views.mark_movie_watched,
        name="mark_movie_watched",
    ),
    path(
        "movie/unwatch/<source:source>/<str:media_id>",
        views.unmark_movie_watched,
        name="unmark_movie_watched",
    ),

    # TV show start/mark/unmark
    path(
        "tv/start/<source:source>/<media_type:media_type>/<str:media_id>",
        views.start_tracking_tv,
        name="start_tracking_tv",
    ),
    path(
        "tv/watch/<source:source>/<media_type:media_type>/<str:media_id>",
        views.mark_tv_watched,
        name="mark_tv_watched",
    ),
    path(
        "tv/unwatch/<source:source>/<media_type:media_type>/<str:media_id>",
        views.unmark_tv_watched,
        name="unmark_tv_watched",
    ),

    # Episodes watch/unwatch
    path(
        "episode/watch/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>/<int:episode_number>",
        views.watch_episode,
        name="watch_episode",
    ),
    path(
        "episode/unwatch/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>/<int:episode_number>",
        views.unwatch_episode,
        name="unwatch_episode",
    ),

    # Season watch/unwatch
    path(
        "season/watch/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.mark_season_watched,
        name="mark_season_watched",
    ),
    path(
        "season/unwatch/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.unmark_season_watched,
        name="unmark_season_watched",
    ),

    # TV/Season logging helpers
    path(
        "log/season/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.log_modal,
        name="log_modal_season",
    ),
    path(
        "tv/log/season/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.log_modal,
        name="tv_log_season",
    ),

    # Diary list and edit
    path("diary", views.diary_list, name="diary_list"),
    path("diary/edit/<int:entry_id>", views.edit_diary_entry, name="edit_diary_entry"),

    # Book logging/progress
    path(
        "book/progress/modal/<source:source>/<str:media_id>",
        views.book_progress_modal,
        name="book_progress_modal",
    ),
    path(
        "book/progress/log/<source:source>/<str:media_id>",
        views.log_book_progress,
        name="log_book_progress",
    ),
    path(
        "book/completed/log/<source:source>/<str:media_id>",
        views.log_book_completed,
        name="log_book_completed",
    ),
]
