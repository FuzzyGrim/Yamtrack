from app.models import Anime, Game, Manga, Movie, Season


def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = int(total_minutes / 60)
    minutes = int(total_minutes % 60)
    if hours == 0:
        return f"{minutes}min"
    return f"{hours}h {minutes:02d}min"


def get_media_by_status(user, status):
    """Fetch media items for a given user filtered by their status."""
    media_status = {}

    # Define a list of media types to iterate over
    media_types = [
        (Movie, "movie", None),
        (Season, "season", "episodes"),  # Season needs prefetch_related for episodes
        (Anime, "anime", None),
        (Manga, "manga", None),
        (Game, "game", None),
    ]

    for model, key, prefetch_related in media_types:
        query = model.objects.filter(user=user, status=status)
        if prefetch_related:
            query = query.prefetch_related(prefetch_related)
        if query.exists():
            media_status[key] = query

    return media_status
