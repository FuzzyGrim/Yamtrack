from django.utils.text import slugify

from app.models import BasicMedia, Status


def build_home_section(key, media_types):
    """Build home section payload."""
    return {
        "key": key,
        "id": slugify(key),
        "media_types": media_types,
        "count": sum(media_list["total"] for media_list in media_types.values()),
    }


def is_active_in_progress_media(media):
    """Return True when media still has released backlog."""
    if not _is_incoming_media(media):
        return True

    return media.max_progress is not None and media.progress < media.max_progress


def get_home_media_types(
    request,
    sort_by,
    section_key,
    items_limit,
    specific_media_type=None,
    *,
    hide_unreleased=False,
):
    """Return media types for a home section, or one type's load-more page."""
    media_types = BasicMedia.objects.get_home_status(
        user=request.user,
        status=section_key,
        sort_by=sort_by,
        items_limit=None if hide_unreleased else items_limit,
        specific_media_type=specific_media_type,
    )

    if not hide_unreleased:
        return media_types

    return _paginate_home_media_types(
        _filter_home_media_types(
            media_types,
            lambda media: _is_released_home_media(media, section_key),
        ),
        items_limit,
        page_start=items_limit if specific_media_type else 0,
    )


def _filter_home_media_types(media_types, predicate):
    """Filter home media entries by predicate."""
    filtered_media_types = {}
    for media_type, media_list in media_types.items():
        filtered_items = [media for media in media_list["items"] if predicate(media)]
        if filtered_items:
            filtered_media_types[media_type] = {
                "items": filtered_items,
                "total": len(filtered_items),
            }
    return filtered_media_types


def _paginate_home_media_types(media_types, items_limit, page_start=0):
    """Paginate already-grouped home media entries."""
    paginated_media_types = {}
    for media_type, media_list in media_types.items():
        page_end = None if items_limit is None else page_start + items_limit
        items = media_list["items"][page_start:page_end]

        if items or (page_start > 0 and media_list["total"]):
            paginated_media_types[media_type] = {
                "items": items,
                "total": media_list["total"],
            }
    return paginated_media_types


def _is_incoming_media(media):
    """Return True when media has a real upcoming release."""
    return bool(media.next_event and not media.next_event.is_max_datetime)


def _is_released_home_media(media, section_key):
    """Return True when media should remain after hiding unreleased entries."""
    if section_key == Status.IN_PROGRESS.value:
        return is_active_in_progress_media(media)

    return not _is_incoming_media(media)
