from app import models
from app.models import MediaTypes, Sources


def metadata(media_id, media_type):
    """Return the metadata for a manual media item."""
    item = models.Item.objects.get(
        media_id=media_id,
        media_type=media_type,
        source=Sources.MANUAL.value,
    )
    response = {
        "media_id": item.media_id,
        "source": Sources.MANUAL.value,
        "media_type": item.media_type,
        "title": item.title,
        "max_progress": None,
        "image": item.image,
        "synopsis": "No synopsis available.",
        "score": None,
        "score_count": None,
        "details": {},
        "related": {},
    }

    season_items = get_season_items(media_id)
    if season_items.count() > 0:
        response["details"]["seasons"] = season_items.count()

    num_episodes = process_seasons(season_items, response)
    set_max_progress(response, num_episodes, item.media_type)

    return response


def season(media_id, season_number):
    """Return the metadata for a manual season."""
    tv_metadata = metadata(media_id, MediaTypes.TV.value)
    return tv_metadata[f"season/{season_number}"]


def get_season_items(media_id):
    """Get all season items for a media ID."""
    return models.Item.objects.filter(
        media_id=media_id,
        source=Sources.MANUAL.value,
        media_type=MediaTypes.SEASON.value,
    )


def process_seasons(season_items, response):
    """Process all seasons and return total episode count."""
    num_episodes = 0

    for season in season_items:
        if "seasons" not in response["related"]:
            response["related"]["seasons"] = []

        season_episodes = get_season_episodes(season)
        episodes_response = build_episodes_response(season_episodes)
        season_response = build_season_response(
            season,
            episodes_response,
            season_episodes,
        )

        response[f"season/{season.season_number}"] = season_response

        season_response["title"] = response["title"]
        response["related"]["seasons"].append(season_response)
        num_episodes += season_episodes.count()

    return num_episodes


def build_season_response(season, episodes_response, season_episodes):
    """Build the season response dictionary."""
    return {
        "source": Sources.MANUAL.value,
        "media_id": season.media_id,
        "media_type": MediaTypes.SEASON.value,
        "title": season.title,
        "season_title": f"Season {season.season_number}",
        "image": season.image,
        "season_number": season.season_number,
        "episodes": episodes_response,
        "max_progress": season_episodes.count(),
        "score": None,
        "score_count": None,
        "details": {
            "episodes": season_episodes.count(),
        },
    }


def get_season_episodes(season):
    """Get all episodes for a season."""
    return models.Item.objects.filter(
        media_id=season.media_id,
        source=Sources.MANUAL.value,
        media_type=MediaTypes.EPISODE.value,
        season_number=season.season_number,
    )


def episode(media_id, season_number, episode_number):
    """Return the metadata for a manual episode."""
    season_metadata = season(media_id, season_number)
    for episode in season_metadata["episodes"]:
        if episode["episode_number"] == int(episode_number):
            return {
                "source": Sources.MANUAL.value,
                "media_id": media_id,
                "media_type": MediaTypes.EPISODE.value,
                "title": season_metadata["title"],
                "season_title": season_metadata["season_title"],
                "episode_title": episode["title"],
                "image": episode["image"],
            }

    return None


def process_episodes(season_metadata, episodes_in_db):
    """Process the episodes for the selected season."""
    # Convert the queryset to a dictionary for efficient lookups
    tracked_episodes = {}
    for ep in episodes_in_db:
        episode_number = ep.item.episode_number
        if episode_number not in tracked_episodes:
            tracked_episodes[episode_number] = []
        tracked_episodes[episode_number].append(ep)

    episodes_metadata = []

    for episode in season_metadata["episodes"]:
        episode_number = episode["episode_number"]

        episode_data = {
            "source": Sources.MANUAL.value,
            "media_id": episode["media_id"],
            "media_type": MediaTypes.EPISODE.value,
            "season_number": season_metadata["season_number"],
            "episode_number": episode_number,
            "air_date": episode["air_date"],
            "image": episode["image"],
            "title": episode["title"],
            "overview": "No synopsis available.",
            "history": tracked_episodes.get(episode_number, []),
        }
        episodes_metadata.append(episode_data)

    return episodes_metadata


def build_episodes_response(season_episodes):
    """Build the episodes response list."""
    return [
        {
            "media_id": episode.media_id,
            "source": Sources.MANUAL.value,
            "title": episode.title,
            "image": episode.image,
            "episode_number": episode.episode_number,
            "air_date": None,
        }
        for episode in season_episodes
    ]


def set_max_progress(response, num_episodes, media_type):
    """Set the max progress and episode count in the response."""
    if num_episodes > 0:
        response["max_progress"] = num_episodes
        response["details"]["episodes"] = num_episodes
    elif media_type == MediaTypes.MOVIE.value:
        response["max_progress"] = 1
