from django.urls import path

from integrations import views

urlpatterns = [
    path("import/trakt", views.import_trakt, name="import_trakt"),
    path("simkl-oauth", views.simkl_oauth, name="simkl_oauth"),
    path("import/simkl", views.import_simkl, name="import_simkl"),
    path("import/mal", views.import_mal, name="import_mal"),
    path("import/anilist", views.import_anilist, name="import_anilist"),
    path("import/kitsu", views.import_kitsu, name="import_kitsu"),
    path("import/yamtrack", views.import_yamtrack, name="import_yamtrack"),
    path("import/hltb", views.import_hltb, name="import_hltb"),
    path("import/imdb", views.import_imdb, name="import_imdb"),
    path("export/csv", views.export_csv, name="export_csv"),
    path(
        "webhook/jellyfin/<str:token>",
        views.jellyfin_webhook,
        name="jellyfin_webhook",
    ),
    path(
        "webhook/plex/<str:token>",
        views.plex_webhook,
        name="plex_webhook",
    ),
    path(
        "webhook/emby/<str:token>",
        views.emby_webhook,
        name="emby_webhook",
    ),
]
