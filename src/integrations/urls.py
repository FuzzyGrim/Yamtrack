from django.urls import path

from integrations import views

urlpatterns = [
    path("import/trakt", views.import_trakt, name="import_trakt"),
    path("simkl-oauth", views.simkl_oauth, name="simkl_oauth"),
    path("import/simkl", views.import_simkl, name="import_simkl"),
    path("import/mal", views.import_mal, name="import_mal"),
    path("import/tmdb_ratings", views.import_tmdb_ratings, name="import_tmdb_ratings"),
    path(
        "import/tmdb_watchlist",
        views.import_tmdb_watchlist,
        name="import_tmdb_watchlist",
    ),
    path("import/anilist", views.import_anilist, name="import_anilist"),
    path("import/kitsu/name", views.import_kitsu_name, name="import_kitsu_name"),
    path("import/kitsu/id", views.import_kitsu_id, name="import_kitsu_id"),
    path("import/yamtrack", views.import_yamtrack, name="import_yamtrack"),
    path("export/csv", views.export_csv, name="export_csv"),
]
