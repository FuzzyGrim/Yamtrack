from django.urls import path

from integrations import views

app_name = "integrations"

urlpatterns = [
    path("import/mal", views.import_mal, name="import_mal"),
    path("import/tmdb_ratings", views.import_tmdb_ratings, name="import_tmdb_ratings"),
    path("import/tmdb_watchlist", views.import_tmdb_watchlist, name="import_tmdb_watchlist"),
    path("import/anilist", views.import_anilist, name="import_anilist"),
    path("import/yamtrack", views.import_yamtrack, name="import_yamtrack"),
    path("export/csv", views.export_csv, name="export_csv"),
]
