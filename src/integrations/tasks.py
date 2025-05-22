import logging

from celery import shared_task
from django.contrib.auth import get_user_model

import events
from app.mixins import disable_fetch_releases
from app.models import MediaTypes
from app.templatetags import app_tags
from integrations import helpers
from integrations.imports import anilist, hltb, kitsu, mal, simkl, trakt, yamtrack

logger = logging.getLogger(__name__)
ERROR_TITLE = "\n\n\n Couldn't import the following media: \n\n"


def format_media_type_display(count, media_type):
    """Format media type display with proper pluralization."""
    if count == 0:
        return None
    if count == 1:
        return f"{count} {dict(MediaTypes.choices).get(media_type, media_type)}"
    return f"{count} {app_tags.media_type_readable_plural(media_type)}"


def format_import_message(imported_counts, warning_messages=None):
    """Format the import result message based on counts and warnings."""
    parts = [
        format_media_type_display(count, media_type)
        for media_type, count in imported_counts.items()
    ]
    parts = [p for p in parts if p is not None]

    if not parts:
        info_message = "No media was imported."
    else:
        info_message = f"Imported {helpers.join_with_commas_and(parts)}."

    if warning_messages:
        return f"{info_message} {ERROR_TITLE} {warning_messages}"
    return info_message


def import_media(importer_func, identifier, user_id, mode):
    """Handle the import process for different media services."""
    user = get_user_model().objects.get(id=user_id)

    with disable_fetch_releases():
        imported_counts, warnings = importer_func(identifier, user, mode)

    events.tasks.reload_calendar.delay()

    return format_import_message(imported_counts, warnings)


@shared_task(name="Import from Trakt")
def import_trakt(username, user_id, mode):
    """Celery task for importing media data from Trakt."""
    return import_media(trakt.importer, username, user_id, mode)


@shared_task(name="Import from SIMKL")
def import_simkl(username, user_id, mode):
    """Celery task for importing media data from SIMKL."""
    return import_media(simkl.importer, username, user_id, mode)


@shared_task(name="Import from MyAnimeList")
def import_mal(username, user_id, mode):
    """Celery task for importing anime and manga data from MyAnimeList."""
    return import_media(mal.importer, username, user_id, mode)


@shared_task(name="Import from AniList")
def import_anilist(username, user_id, mode):
    """Celery task for importing anime and manga data from AniList."""
    return import_media(anilist.importer, username, user_id, mode)


@shared_task(name="Import from Kitsu")
def import_kitsu(username, user_id, mode):
    """Celery task for importing anime and manga data from Kitsu."""
    return import_media(kitsu.importer, username, user_id, mode)


@shared_task(name="Import from Yamtrack")
def import_yamtrack(file, user_id, mode):
    """Celery task for importing media data from Yamtrack."""
    return import_media(yamtrack.importer, file, user_id, mode)


@shared_task(name="Import from HowLongToBeat")
def import_hltb(file, user_id, mode):
    """Celery task for importing media data from HowLongToBeat."""
    return import_media(hltb.importer, file, user_id, mode)
