import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://api.igdb.com/v4"


def handle_error(error):
    """Handle IGDB API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    # Invalid access token, expired or revoked
    if status_code == requests.codes.unauthorized:
        logger.warning(
            "%s: Invalid access token, refreshing",
            Sources.IGDB.label,
        )
        cache.delete(f"{Sources.IGDB.value}_access_token")
        return {"retry": True}

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.IGDB.value, error) from json_error

    # Invalid keys
    if status_code in (requests.codes.bad_request, requests.codes.forbidden):
        try:
            details = error_json.get("message").capitalize()
            if details:
                raise services.ProviderAPIError(
                    Sources.IGDB.value,
                    error,
                    details,
                )
        # it can be other error format
        except (KeyError, AttributeError):
            logger.exception("Unexpected error format from IGDB API")
            raise services.ProviderAPIError(Sources.IGDB.value, error) from None

    raise services.ProviderAPIError(Sources.IGDB.value, error)


def get_access_token():
    """Return the access token for the IGDB API."""
    access_token = cache.get(f"{Sources.IGDB.value}_access_token")
    if access_token is None:
        url = "https://id.twitch.tv/oauth2/token"
        json = {
            "client_id": settings.IGDB_ID,
            "client_secret": settings.IGDB_SECRET,
            "grant_type": "client_credentials",
        }

        try:
            response = services.api_request(
                Sources.IGDB.value,
                "POST",
                url,
                params=json,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        access_token = response["access_token"]
        cache.set(
            f"{Sources.IGDB.value}_access_token",
            access_token,
            response["expires_in"] - 60,
        )  # 1 min buffer to avoid using an expired token
    return access_token


def search(query, page):
    """Search for games on IGDB using MultiQuery."""
    cache_key = f"search_{Sources.IGDB.value}_{MediaTypes.GAME.value}_{query}_{page}"
    data = cache.get(cache_key)

    if data is None:
        access_token = get_access_token()
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        base_conditions = (
            f'where name ~ *"{query}"* & game_type = (0,1,2,3,4,5,6,7,8,9,10)'
        )

        if not settings.IGDB_NSFW:
            base_conditions += " & themes != (42)"

        offset = (page - 1) * settings.PER_PAGE

        # Create the multiquery with both search and count
        multiquery = (
            'query games "SearchResults" {'
            "fields name,cover.image_id;"
            "sort total_rating_count desc;"
            f"limit {settings.PER_PAGE};"
            f"offset {offset};"
            f"{base_conditions};"
            "};"
            'query games/count "TotalCount" {'
            f"{base_conditions};"
            "};"
        )

        try:
            response = services.api_request(
                Sources.IGDB.value,
                "POST",
                f"{base_url}/multiquery",
                data=multiquery,
                headers=headers,
            )

        except requests.exceptions.HTTPError as error:
            handle_error(error)

        search_results = next(
            (item["result"] for item in response if item["name"] == "SearchResults"),
            [],
        )
        total_results = next(
            (item["count"] for item in response if item["name"] == "TotalCount"),
            0,
        )

        results = [
            {
                "media_id": media["id"],
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "title": media["name"],
                "image": get_image_url(media),
            }
            for media in search_results
        ]

        data = helpers.format_search_response(
            page,
            settings.PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def game(media_id):
    """Return the metadata for the selected game from IGDB."""
    cache_key = f"{Sources.IGDB.value}_{MediaTypes.GAME.value}_{media_id}"
    data = cache.get(cache_key)
    if data is None:
        access_token = get_access_token()
        url = f"{base_url}/games"
        data = (
            "fields name,cover.image_id,artworks.image_id,"
            "url,summary,game_type,first_release_date,total_rating,total_rating_count,"
            "genres.name,themes.name,platforms.name,involved_companies.company.name,"
            "parent_game.name,parent_game.cover.image_id,"
            "remasters.name,remasters.cover.image_id,"
            "remakes.name,remakes.cover.image_id,"
            "expansions.name,expansions.cover.image_id,"
            "standalone_expansions.name,standalone_expansions.cover.image_id,"
            "expanded_games.name,expanded_games.cover.image_id,"
            "similar_games.name,similar_games.cover.image_id;"
            f"where id = {media_id};"
        )
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = services.api_request(
                Sources.IGDB.value,
                "POST",
                url,
                data=data,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            error_resp = handle_error(error)
            if error_resp and error_resp.get("retry"):
                # Retry the request with the new access token
                headers["Authorization"] = f"Bearer {get_access_token()}"
                response = services.api_request(
                    Sources.IGDB.value,
                    "POST",
                    url,
                    data=data,
                    headers=headers,
                )

        response = response[0]  # response is a list with a single element
        data = {
            "media_id": response["id"],
            "source": Sources.IGDB.value,
            "source_url": response["url"],
            "media_type": MediaTypes.GAME.value,
            "title": response["name"],
            "max_progress": None,
            "image": get_image_url(response),
            "synopsis": response.get("summary", "No synopsis available."),
            "genres": get_list(response, "genres"),
            "score": get_score(response),
            "score_count": response.get("total_rating_count"),
            "details": {
                "format": get_game_type(response["game_type"]),
                "release_date": get_start_date(response),
                "themes": get_list(response, "themes"),
                "platforms": get_list(response, "platforms"),
                "companies": get_companies(response),
            },
            "related": {
                "parent_game": get_parent(response.get("parent_game")),
                "remasters": get_related(response.get("remasters")),
                "remakes": get_related(response.get("remakes")),
                "expansions": get_related(response.get("expansions")),
                "standalone_expansions": get_related(
                    response.get("standalone_expansions"),
                ),
                "expanded_games": get_related(response.get("expanded_games")),
                "recommendations": get_related(response.get("similar_games")),
            },
        }
        cache.set(cache_key, data)
    return data


def get_image_url(response):
    """Return the image URL for the media."""
    # when no image, cover is not present in the response
    # e.g game: 287348
    try:
        return f"https://images.igdb.com/igdb/image/upload/t_original/{response['cover']['image_id']}.jpg"
    except KeyError:
        return settings.IMG_NONE


def get_game_type(game_type_id):
    """Return the game_type of the game."""
    game_type_mapping = {
        0: "Main game",
        1: "DLC",
        2: "Expansion",
        3: "Bundle",
        4: "Standalone expansion",
        5: "Mod",
        6: "Episode",
        7: "Season",
        8: "Remake",
        9: "Remaster",
        10: "Expanded game",
        11: "Port",
        12: "Fork",
        13: "Pack",
        14: "Update",
    }
    return game_type_mapping.get(game_type_id)


def get_start_date(response):
    """Return the start date of the game."""
    # when no release date, first_release_date is not present in the response
    # e.g game: 210710
    try:
        return timezone.datetime.fromtimestamp(
            response["first_release_date"],
            tz=timezone.get_current_timezone(),
        ).strftime("%Y-%m-%d")
    except KeyError:
        return None


def get_list(response, field):
    """Return the list of names from a list of dictionaries."""
    # when no data of field, field is not present in the response
    # e.g game: 25222
    try:
        return [item["name"] for item in response[field]]
    except KeyError:
        return None


def get_companies(response):
    """Return the companies involved in the game."""
    # when no companies, involved_companies is not present in the response
    # e.g game: 238417
    try:
        return ", ".join(
            company["company"]["name"] for company in response["involved_companies"]
        )
    except KeyError:
        return None


def get_score(response):
    """Return the score of the game."""
    # when no score, total_rating is not present in the response
    try:
        score = response["total_rating"]  # returns e.g 92.70730625238252
        return round(score / 10, 1)
    except KeyError:
        return None


def get_parent(parent_game):
    """Return the parent game to the selected game."""
    if parent_game:
        return [
            {
                "source": Sources.IGDB.value,
                "media_id": parent_game["id"],
                "media_type": MediaTypes.GAME.value,
                "title": parent_game["name"],
                "image": get_image_url(parent_game),
            },
        ]
    return []


def get_related(related_medias):
    """Return the related games to the selected game."""
    if related_medias:
        return [
            {
                "source": Sources.IGDB.value,
                "media_id": game["id"],
                "media_type": MediaTypes.GAME.value,
                "title": game["name"],
                "image": get_image_url(game),
            }
            for game in related_medias
        ]
    return []
