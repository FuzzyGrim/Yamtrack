import html
import logging
import re
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils.html import strip_tags

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

base_url = "https://graphql.anilist.co"

# Shared fields for search results, related media and recommendations
media_card_fragment = """
fragment MediaCard on Media {
    id
    type
    title {
        userPreferred
    }
    coverImage {
        large
    }
}
"""

search_query = (
    """
query (
    $search: String
    $type: MediaType
    $page: Int
    $perPage: Int
    $isAdult: Boolean
) {
    Page(page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }
        media(search: $search, type: $type, isAdult: $isAdult, sort: SEARCH_MATCH) {
            ...MediaCard
        }
    }
}
"""
    + media_card_fragment
)

media_query = (
    """
query ($id: Int, $type: MediaType) {
    Media(id: $id, type: $type) {
        id
        idMal
        siteUrl
        title {
            userPreferred
        }
        coverImage {
            extraLarge
            large
        }
        description
        format
        status
        startDate {
            year
            month
            day
        }
        endDate {
            year
            month
            day
        }
        season
        seasonYear
        episodes
        duration
        chapters
        source
        genres
        averageScore
        stats {
            scoreDistribution {
                amount
            }
        }
        studios(isMain: true) {
            nodes {
                name
            }
        }
        nextAiringEpisode {
            airingAt
        }
        relations {
            edges {
                node {
                    ...MediaCard
                }
            }
        }
        recommendations(sort: RATING_DESC) {
            nodes {
                mediaRecommendation {
                    ...MediaCard
                }
            }
        }
    }
}
"""
    + media_card_fragment
)


def handle_error(error):
    """Handle AniList API errors."""
    error_resp = error.response

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.ANILIST.value, error) from json_error

    errors = error_json.get("errors") or [{}]
    details = errors[0].get("message")
    raise services.ProviderAPIError(Sources.ANILIST.value, error, details)


def graphql_request(query, variables):
    """Send a GraphQL query to AniList and return the data."""
    try:
        response = services.api_request(
            Sources.ANILIST.value,
            "POST",
            base_url,
            params={"query": query, "variables": variables},
        )
    except requests.exceptions.HTTPError as error:
        handle_error(error)

    return response["data"]


def search(media_type, query, page):
    """Search for anime or manga on AniList."""
    cache_key = f"search_{Sources.ANILIST.value}_{media_type}_{query}_{page}"
    data = cache.get(cache_key)

    if data is None:
        variables = {
            "search": query,
            "type": media_type.upper(),
            "page": page,
            "perPage": settings.PER_PAGE,
        }
        # AniList filters by null instead of ignoring it, leave it out to get all
        if not settings.ANILIST_NSFW:
            variables["isAdult"] = False

        response = graphql_request(search_query, variables)["Page"]
        results = [get_media_card(media, media_type) for media in response["media"]]

        # AniList caps the total results, so only count up to the next page
        total_results = (page - 1) * settings.PER_PAGE + len(results)
        if response["pageInfo"]["hasNextPage"]:
            total_results += settings.PER_PAGE

        data = helpers.format_search_response(
            page,
            settings.PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def anime(media_id):
    """Return the metadata for the selected anime from AniList."""
    cache_key = f"{Sources.ANILIST.value}_{MediaTypes.ANIME.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        response = get_media(media_id, MediaTypes.ANIME.value)

        data = {
            **get_base_metadata(response, media_id, MediaTypes.ANIME.value),
            "max_progress": response["episodes"],
            "details": {
                "format": get_format(response),
                "start_date": get_date(response["startDate"]),
                "end_date": get_date(response["endDate"]),
                "status": get_readable_status(response, MediaTypes.ANIME.value),
                "episodes": response["episodes"],
                "runtime": get_runtime(response),
                "studios": get_studios(response),
                "season": get_season(response),
                "broadcast": get_broadcast(response),
                "source": get_source(response),
            },
            "related": {
                "related_anime": get_related(response, MediaTypes.ANIME.value),
                "related_manga": get_related(response, MediaTypes.MANGA.value),
                "recommendations": get_recommendations(
                    response,
                    MediaTypes.ANIME.value,
                ),
            },
        }

        cache.set(cache_key, data)

    return data


def manga(media_id):
    """Return the metadata for the selected manga from AniList."""
    cache_key = f"{Sources.ANILIST.value}_{MediaTypes.MANGA.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        response = get_media(media_id, MediaTypes.MANGA.value)

        data = {
            **get_base_metadata(response, media_id, MediaTypes.MANGA.value),
            "max_progress": response["chapters"],
            "details": {
                "format": get_format(response),
                "start_date": get_date(response["startDate"]),
                "end_date": get_date(response["endDate"]),
                "status": get_readable_status(response, MediaTypes.MANGA.value),
                "number_of_chapters": response["chapters"],
            },
            "related": {
                "related_manga": get_related(response, MediaTypes.MANGA.value),
                "related_anime": get_related(response, MediaTypes.ANIME.value),
                "recommendations": get_recommendations(
                    response,
                    MediaTypes.MANGA.value,
                ),
            },
        }

        cache.set(cache_key, data)

    return data


def get_media(media_id, media_type):
    """Return the AniList media response for the selected anime or manga."""
    variables = {"id": int(media_id), "type": media_type.upper()}
    return graphql_request(media_query, variables)["Media"]


def get_base_metadata(response, media_id, media_type):
    """Return the metadata shared by anime and manga."""
    return {
        "media_id": media_id,
        "source": Sources.ANILIST.value,
        "source_url": response["siteUrl"],
        "media_type": media_type,
        "title": response["title"]["userPreferred"],
        "image": get_image_url(response),
        "synopsis": get_synopsis(response),
        "genres": response["genres"] or None,
        "score": get_score(response),
        "score_count": get_score_count(response),
        "external_links": get_external_links(response, media_type),
    }


def get_media_card(media, media_type):
    """Return the search and related entry for an AniList media."""
    return {
        "media_id": media["id"],
        "source": Sources.ANILIST.value,
        "media_type": media_type,
        "title": media["title"]["userPreferred"],
        "image": get_image_url(media),
    }


def get_image_url(response):
    """Return the image URL for the media."""
    cover = response["coverImage"]
    return cover.get("extraLarge") or cover.get("large") or settings.IMG_NONE


def get_synopsis(response):
    """Return the synopsis as plain text."""
    # descriptions include HTML tags like <br> and <i>, and can be null
    description = response["description"]
    if not description:
        return "No synopsis available."

    description = re.sub(r"<br\s*/?>\s*", "\n", description)
    return html.unescape(strip_tags(description)).strip()


def get_format(response):
    """Return the format of the media in human-readable format."""
    media_format = response["format"]
    if not media_format:
        return None

    format_map = {
        "TV": "TV",
        "TV_SHORT": "TV Short",
        "OVA": "OVA",
        "ONA": "ONA",
        "NOVEL": "Light Novel",
    }
    return format_map.get(media_format, media_format.replace("_", " ").title())


def get_readable_status(response, media_type):
    """Return the status in human-readable format."""
    status = response["status"]
    if not status:
        return None

    status_map = {
        "FINISHED": "Finished",
        "RELEASING": (
            "Airing" if media_type == MediaTypes.ANIME.value else "Publishing"
        ),
        "NOT_YET_RELEASED": "Upcoming",
        "CANCELLED": "Discontinued",
        "HIATUS": "On Hiatus",
    }
    return status_map.get(status, status.replace("_", " ").title())


def get_date(date):
    """Return the AniList fuzzy date as YYYY-MM-DD, YYYY-MM or YYYY."""
    if not date["year"]:
        return None
    if not date["month"]:
        return str(date["year"])
    if not date["day"]:
        return f"{date['year']}-{date['month']:02d}"
    return f"{date['year']}-{date['month']:02d}-{date['day']:02d}"


def get_runtime(response):
    """Return the average episode duration."""
    # duration is in minutes and null when unknown
    duration = response["duration"]
    if duration:
        hours, minutes = divmod(duration, 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes} min"
    return None


def get_studios(response):
    """Return the main studios for the media."""
    studios = [studio["name"] for studio in response["studios"]["nodes"]]
    return studios or None


def get_season(response):
    """Return the season for the media."""
    if response["season"] and response["seasonYear"]:
        return f"{response['season'].title()} {response['seasonYear']}"
    return None


def get_broadcast(response):
    """Return the broadcast day and time of the next episode."""
    next_episode = response["nextAiringEpisode"]
    if not next_episode:
        return None

    airing_at = datetime.fromtimestamp(next_episode["airingAt"], tz=settings.TZ)
    return airing_at.strftime("%A %H:%M")


def get_source(response):
    """Return the source material of the media."""
    source = response["source"]
    if source:
        return source.replace("_", " ").title()
    return None


def get_score(response):
    """Return the score for the media in a 0-10 scale."""
    # averageScore is a 0-100 value, null when there are not enough ratings
    score = response["averageScore"]
    if score:
        return round(score / 10, 1)
    return None


def get_score_count(response):
    """Return the number of ratings for the media."""
    if not get_score(response):
        return 0
    return sum(item["amount"] for item in response["stats"]["scoreDistribution"])


def get_external_links(response, media_type):
    """Return links to the media on other sites."""
    if response["idMal"]:
        return {
            Sources.MAL.label: (
                f"https://myanimelist.net/{media_type}/{response['idMal']}"
            ),
        }
    return {}


def get_related(response, media_type):
    """Return the related media of the selected type."""
    return [
        get_media_card(edge["node"], media_type)
        for edge in response["relations"]["edges"]
        if edge["node"]["type"] == media_type.upper()
    ]


def get_recommendations(response, media_type):
    """Return the recommended media for the selected media."""
    # mediaRecommendation is null when the recommended media was deleted
    return [
        get_media_card(node["mediaRecommendation"], media_type)
        for node in response["recommendations"]["nodes"]
        if node["mediaRecommendation"]
    ]
