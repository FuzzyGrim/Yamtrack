def get_media_list_by_type(user, media_types):
    """Get media items by type for a user."""
    list_by_type = {}
    for model, media_type, prefetch_related in media_types:
        media_list = model.objects.filter(
            user=user,
            status__in=["Repeating", "In progress"],
        )
        if prefetch_related:
            media_list = media_list.prefetch_related(prefetch_related)
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
