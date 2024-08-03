from django.apps import apps
from django.db.models import F

from app import models
from app.models import Item


def get_media_list(user, media_type, status_filter, sort_filter):
    """Get media list based on filters and sorting."""
    model = apps.get_model(app_label="app", model_name=media_type)
    queryset = model.objects.filter(user=user.id)

    if "All" not in status_filter:
        queryset = queryset.filter(status__in=status_filter)

    # Apply prefetch related based on media type
    prefetch_map = {
        "tv": ["seasons", "seasons__episodes"],
        "season": ["episodes", "episodes__item"],
        "default": [None],
    }
    prefetch_related_fields = prefetch_map.get(media_type, prefetch_map["default"])
    queryset = queryset.prefetch_related(*prefetch_related_fields).select_related(
        "item",
    )

    sort_is_property = sort_filter in get_properties(model)
    sort_is_item_field = sort_filter in get_fields(Item)
    if media_type in ("tv", "season") and sort_is_property:
        return sorted(queryset, key=lambda x: getattr(x, sort_filter), reverse=True)

    if sort_is_item_field:
        sort_field = f"item__{sort_filter}"
        return queryset.order_by(
            F(sort_field).asc() if sort_filter == "title" else F(sort_field).desc(),
        )
    return queryset.order_by(F(sort_filter).desc(nulls_last=True))


def get_fields(model):
    """Get fields of a model."""
    return [f.name for f in model._meta.fields]  # noqa: SLF001


def get_properties(model):
    """Get properties of a model."""
    return [name for name in dir(model) if isinstance(getattr(model, name), property)]


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


def get_media(media_type, item, user):
    """Get user media object given the media type and item."""
    model = apps.get_model(app_label="app", model_name=media_type)
    params = {"item": item}

    if media_type == "episode":
        params["related_season__user"] = user
    else:
        params["user"] = user

    try:
        return model.objects.get(**params)
    except model.DoesNotExist:
        return None
