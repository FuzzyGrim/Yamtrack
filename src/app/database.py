from django.apps import apps
from django.db.models import F

from app import models


def get_media_list(
    user,
    media_type,
    status_filter,
    sort_filter,
):
    """Get media list based on filters and sorting."""
    filter_params = {"user": user.id}

    if "All" not in status_filter:
        filter_params["status__in"] = status_filter

    model = apps.get_model(app_label="app", model_name=media_type)

    if media_type == "tv":
        media_list = model.objects.filter(**filter_params).prefetch_related(
            "seasons",
            "seasons__episodes",
        )
    elif media_type == "season":
        media_list = model.objects.filter(**filter_params).prefetch_related("episodes")
    else:
        media_list = model.objects.filter(**filter_params)

    sort_is_property = sort_filter in ("progress", "start_date", "end_date", "repeats")
    if media_type in ("tv", "season") and sort_is_property:
        media_list = sorted(
            media_list,
            key=lambda x: getattr(x, sort_filter),
            reverse=True,
        )
    elif sort_filter == "title":
        media_list = media_list.order_by(F(sort_filter).asc())
    else:
        media_list = media_list.order_by(F(sort_filter).desc(nulls_last=True))

    return media_list


def get_media_list_by_type(user):
    """Get media items by type for a user."""
    media_types = ["movie", "season", "anime", "manga", "game"]
    list_by_type = {}

    for media_type in media_types:
        media_list = get_media_list(
            user=user,
            media_type=media_type,
            status_filter=[models.STATUS_IN_PROGRESS, models.STATUS_REPEATING],
            sort_filter="score",
        )
        if media_list:
            list_by_type[media_type] = media_list

    return list_by_type


def get_search_params(media_type, media_id, season_number, episode_number, user):
    """Get search params for media item."""
    if media_type == "season":
        search_params = {
            "media_id": media_id,
            "user": user,
            "season_number": season_number,
        }
    elif media_type == "episode":
        search_params = {
            "related_season__media_id": media_id,
            "related_season__user": user,
            "related_season__season_number": season_number,
            "episode_number": episode_number,
        }
    else:
        search_params = {"media_id": media_id, "user": user}

    return search_params
