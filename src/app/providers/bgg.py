"""BoardGameGeek (BGG) API provider for board game metadata.

API Documentation: https://boardgamegeek.com/wiki/page/BGG_XML_API2
API Terms: https://boardgamegeek.com/wiki/page/XML_API_Terms_of_Use
"""

import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://boardgamegeek.com/xmlapi2"

# BGG's /thing endpoint has a max of 20 IDs per request
RESULTS_PER_PAGE = 20


def handle_error(error):
    """Handle BGG API errors."""
    if error.response.status_code == requests.codes.unauthorized:
        raise services.ProviderAPIError(
            Sources.BGG.value,
            error,
            "BGG API requires authorization",
        )
    raise services.ProviderAPIError(Sources.BGG.value, error)


def search(query, page):
    """Search for board games on BoardGameGeek."""
    cache_key = (
        f"search_{Sources.BGG.value}_{MediaTypes.BOARDGAME.value}_{query}_{page}"
    )
    data = cache.get(cache_key)

    if data is None:
        # Cache search results separately so page changes don't re-query BGG
        search_results_cache_key = (
            f"search_results_{Sources.BGG.value}_{MediaTypes.BOARDGAME.value}_{query}"
        )
        all_results = cache.get(search_results_cache_key)

        if all_results is None:
            try:
                root = services.api_request(
                    Sources.BGG.value,
                    "GET",
                    f"{base_url}/search",
                    params={"query": query, "type": "boardgame"},
                    headers={"Authorization": f"Bearer {settings.BGG_API_TOKEN}"},
                    response_format="xml",
                )
            except requests.exceptions.HTTPError as error:
                handle_error(error)

            # Parse all results (BGG returns all at once, no server-side pagination)
            all_results = []
            for item in root.findall(".//item"):
                game_id = item.get("id")
                name_elem = item.find("name")
                if name_elem is not None and game_id:
                    all_results.append(
                        {
                            "id": game_id,
                            "name": name_elem.get("value", "Unknown"),
                        }
                    )

            cache.set(search_results_cache_key, all_results)

        # Client-side pagination
        total_results = len(all_results)
        start_idx = (page - 1) * RESULTS_PER_PAGE
        end_idx = start_idx + RESULTS_PER_PAGE
        page_results = all_results[start_idx:end_idx]

        # Fetch thumbnails for this page
        thumbnails = _fetch_thumbnails([r["id"] for r in page_results])

        results = [
            {
                "media_id": r["id"],
                "source": Sources.BGG.value,
                "media_type": MediaTypes.BOARDGAME.value,
                "title": r["name"],
                "image": thumbnails.get(r["id"], settings.IMG_NONE),
            }
            for r in page_results
        ]

        data = helpers.format_search_response(
            page,
            RESULTS_PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def _fetch_thumbnails(game_ids):
    """Fetch thumbnail images for a list of game IDs."""
    if not game_ids:
        return {}

    try:
        root = services.api_request(
            Sources.BGG.value,
            "GET",
            f"{base_url}/thing",
            params={"id": ",".join(game_ids)},
            headers={"Authorization": f"Bearer {settings.BGG_API_TOKEN}"},
            response_format="xml",
        )

        thumbnails = {}
        for item in root.findall(".//item"):
            game_id = item.get("id")
            thumbnail_elem = item.find("thumbnail")
            if thumbnail_elem is not None and thumbnail_elem.text:
                thumbnails[game_id] = thumbnail_elem.text
            else:
                image_elem = item.find("image")
                if image_elem is not None and image_elem.text:
                    thumbnails[game_id] = image_elem.text
    except (requests.exceptions.HTTPError, services.ProviderAPIError):
        logger.exception("Failed to fetch thumbnails from BGG")
        return {}
    else:
        return thumbnails


def boardgame(media_id):
    """Return the metadata for the selected board game from BGG."""
    cache_key = f"{Sources.BGG.value}_{MediaTypes.BOARDGAME.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        try:
            root = services.api_request(
                Sources.BGG.value,
                "GET",
                f"{base_url}/thing",
                params={"id": media_id, "stats": "1"},
                headers={"Authorization": f"Bearer {settings.BGG_API_TOKEN}"},
                response_format="xml",
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        item = root.find(".//item")
        if item is None:
            services.raise_not_found_error(Sources.BGG.value, media_id, "boardgame")

        data = {
            "media_id": media_id,
            "source": Sources.BGG.value,
            "source_url": f"https://boardgamegeek.com/boardgame/{media_id}",
            "media_type": MediaTypes.BOARDGAME.value,
            "title": get_title(item),
            "max_progress": None,
            "image": get_image(item),
            "synopsis": get_description(item),
            "genres": get_categories(item),
            "score": get_score(item),
            "score_count": get_score_count(item),
            "details": {
                "year": get_year(item),
                "players": get_players(item),
                "playtime": get_playtime(item),
                "min_age": get_min_age(item),
                "designers": get_designers(item),
                "publishers": get_publishers(item),
            },
        }

        cache.set(cache_key, data)

    return data


def get_title(item):
    """Return the primary name of the game."""
    name_elem = item.find(".//name[@type='primary']")
    return name_elem.get("value", "Unknown") if name_elem is not None else "Unknown"


def get_image(item):
    """Return the image URL."""
    image_elem = item.find("image")
    if image_elem is not None and image_elem.text:
        return image_elem.text
    return settings.IMG_NONE


def get_description(item):
    """Return the description."""
    desc_elem = item.find("description")
    if desc_elem is not None and desc_elem.text:
        return desc_elem.text
    return "No synopsis available"


def get_year(item):
    """Return the year published."""
    year_elem = item.find("yearpublished")
    return year_elem.get("value") if year_elem is not None else None


def get_players(item):
    """Return the player count range."""
    minplayers_elem = item.find("minplayers")
    maxplayers_elem = item.find("maxplayers")
    minplayers = minplayers_elem.get("value") if minplayers_elem is not None else None
    maxplayers = maxplayers_elem.get("value") if maxplayers_elem is not None else None

    if minplayers and maxplayers:
        if minplayers == maxplayers:
            return f"{minplayers} players"
        return f"{minplayers}-{maxplayers} players"
    return None


def get_playtime(item):
    """Return the playing time."""
    playtime_elem = item.find("playingtime")
    playtime = playtime_elem.get("value") if playtime_elem is not None else None
    return f"{playtime} min" if playtime else None


def get_min_age(item):
    """Return the minimum age."""
    minage_elem = item.find("minage")
    minage = minage_elem.get("value") if minage_elem is not None else None
    return f"{minage}+" if minage else None


def get_score(item):
    """Return the average rating."""
    avg_rating_elem = item.find(".//statistics/ratings/average")
    if avg_rating_elem is not None:
        try:
            return round(float(avg_rating_elem.get("value", 0)), 1)
        except ValueError:
            return None
    return None


def get_score_count(item):
    """Return the number of ratings."""
    usersrated_elem = item.find(".//statistics/ratings/usersrated")
    if usersrated_elem is not None:
        try:
            return int(usersrated_elem.get("value", 0))
        except ValueError:
            return None
    return None


def get_categories(item):
    """Return the list of categories."""
    categories = [
        link.get("value")
        for link in item.findall(".//link[@type='boardgamecategory']")
        if link.get("value")
    ]
    return categories or None


def get_designers(item):
    """Return the list of designers."""
    designers = [
        link.get("value")
        for link in item.findall(".//link[@type='boardgamedesigner']")
        if link.get("value")
    ]
    return ", ".join(designers) if designers else None


def get_publishers(item):
    """Return the first few publishers."""
    publishers = [
        link.get("value")
        for link in item.findall(".//link[@type='boardgamepublisher']")[:3]
        if link.get("value")
    ]
    return ", ".join(publishers) if publishers else None
