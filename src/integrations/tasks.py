import requests
from celery import shared_task

from integrations.imports import anilist, kitsu, mal, tmdb, trakt, yamtrack

ERROR_TITLE = "Couldn't import the following media: \n"


@shared_task(name="Import from Trakt")
def import_trakt(username, user):
    """Celery task for importing anime and manga data from Trakt."""
    try:
        msg = trakt.importer(username, user)
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.not_found:
            msg = f"User {username} not found."
            raise ValueError(msg) from error
        raise  # re-raise for other errors
    return msg


@shared_task(name="Import from MyAnimeList")
def import_mal(username, user):
    """Celery task for importing anime and manga data from MyAnimeList."""
    try:
        num_anime_imported, num_manga_imported = mal.importer(username, user)
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.not_found:
            msg = f"User {username} not found."
            raise ValueError(msg) from error
        raise  # re-raise for other errors
    return f"Imported {num_anime_imported} anime and {num_manga_imported} manga."


@shared_task(name="Import from TMDB")
def import_tmdb(file, user, status):
    """Celery task for importing TMDB tv shows and movies."""
    try:
        num_tv_imported, num_movie_imported = tmdb.importer(file, user, status)
    except UnicodeDecodeError as error:
        msg = "Invalid file format. Please upload a CSV file."
        raise ValueError(msg) from error
    except KeyError as error:
        msg = "Error parsing TMDB CSV file."
        raise ValueError(msg) from error
    return f"Imported {num_tv_imported} TV shows and {num_movie_imported} movies."


@shared_task(name="Import from AniList")
def import_anilist(username, user):
    """Celery task for importing anime and manga data from AniList."""
    try:
        num_anime_imported, num_manga_imported, warning_message = anilist.importer(
            username,
            user,
        )
    except requests.exceptions.HTTPError as error:
        error_message = error.response.json()["errors"][0].get("message")
        if error_message == "User not found":
            msg = f"User {username} not found."
            raise ValueError(msg) from error
        if error_message == "Private User":
            msg = f"User {username} is private."
            raise ValueError(msg) from error
        raise  # re-raise for other errors

    info_message = (
        f"Imported {num_anime_imported} anime and {num_manga_imported} manga."
    )
    if warning_message:
        return f"{info_message} {ERROR_TITLE}{warning_message}"
    return info_message


@shared_task(name="Import from Kitsu by username")
def import_kitsu_name(username, user):
    """Celery task for importing anime and manga data from Kitsu."""
    num_anime_imported, num_manga_imported, warning_message = kitsu.import_by_username(
        username,
        user,
    )

    info_message = (
        f"Imported {num_anime_imported} anime and {num_manga_imported} manga."
    )
    if warning_message:
        return f"{info_message} {ERROR_TITLE}{warning_message}"
    return info_message


@shared_task(name="Import from Kitsu by user ID")
def import_kitsu_id(user_id, user):
    """Celery task for importing anime and manga data from Kitsu."""
    num_anime_imported, num_manga_imported, warning_message = kitsu.import_by_user_id(
        user_id,
        user,
    )

    info_message = (
        f"Imported {num_anime_imported} anime and {num_manga_imported} manga."
    )
    if warning_message:
        return f"{info_message} {ERROR_TITLE}{warning_message}"
    return info_message


@shared_task(name="Import from Yamtrack")
def import_yamtrack(file, user):
    """Celery task for importing media data from Yamtrack."""
    try:
        imported_counts = yamtrack.importer(file, user)
    except UnicodeDecodeError as error:
        msg = "Invalid file format. Please upload a CSV file."
        raise ValueError(msg) from error
    except KeyError as error:
        msg = "Error parsing Yamtrack CSV file."
        raise ValueError(msg) from error

    imported_summary_list = [
        f"{count} TV shows" if media_type == "tv" else f"{count} {media_type}s"
        for media_type, count in imported_counts.items()
    ]
    imported_summary = (
        ", ".join(imported_summary_list[:-1]) + " and " + imported_summary_list[-1]
    )
    return f"Imported {imported_summary}."
