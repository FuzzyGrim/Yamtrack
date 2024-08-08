from datetime import datetime

from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://api.igdb.com/v4"


def get_access_token():
    """Return the access token for the IGDB API."""
    access_token = cache.get("igdb_access_token")
    if access_token is None:
        url = "https://id.twitch.tv/oauth2/token"
        json = {
            "client_id": settings.IGDB_ID,
            "client_secret": settings.IGDB_SECRET,
            "grant_type": "client_credentials",
        }
        response = services.api_request("IGDB", "POST", url, params=json)
        access_token = response["access_token"]
        cache.set(
            "igdb_access_token",
            access_token,
            response["expires_in"] - 5,
        )  # 5 seconds buffer to avoid using an expired token
    return access_token


def search(query):
    """Search for games on IGDB."""
    data = cache.get(f"search_games_{query}")
    if data is None:
        access_token = get_access_token()
        url = f"{base_url}/games"
        data = (
            "fields name,cover.image_id;"
            f'search "{query}";'
            "where category = (0,2,4,8,9,10)"
        )  # main, expansion, standalone expansion, remakes, remasters, expanded games

        # exclude adult games depending on the settings
        if not settings.IGDB_NSFW:
            data += " & themes != (42);"
        else:
            data += ";"

        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }
        response = services.api_request("IGDB", "POST", url, data=data, headers=headers)
        data = [
            {
                "media_id": media["id"],
                "media_type": "game",
                "title": media["name"],
                "image": get_image_url(media),
            }
            for media in response
        ]
        cache.set(f"search_games_{query}", data)
    return data


def game(media_id):
    """Return the metadata for the selected game from IGDB."""
    data = cache.get(f"game_{media_id}")
    if data is None:
        access_token = get_access_token()
        url = f"{base_url}/games"
        data = (
            "fields name,cover.image_id,summary,category,first_release_date,"
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
        response = services.api_request("IGDB", "POST", url, data=data, headers=headers)
        response = response[0]  # response is a list with a single element
        data = {
            "media_id": response["id"],
            "media_type": "game",
            "title": response["name"],
            "max_progress": None,
            "image": get_image_url(response),
            "synopsis": response["summary"],
            "details": {
                "format": get_category(response["category"]),
                "release_date": get_start_date(response),
                "genres": get_str_list(response, "genres"),
                "themes": get_str_list(response, "themes"),
                "platforms": get_str_list(response, "platforms"),
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
        cache.set(f"game_{media_id}", data)
    return data


def get_image_url(response):
    """Return the image URL for the media."""
    # when no image, cover is not present in the response
    # e.g game: 287348
    try:
        return f"https://images.igdb.com/igdb/image/upload/t_original/{response['cover']['image_id']}.jpg"
    except KeyError:
        return settings.IMG_NONE


def get_category(category_id):
    """Return the category of the game."""
    category_mapping = {
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
    return category_mapping.get(category_id)


def get_start_date(response):
    """Return the start date of the game."""
    # when no release date, first_release_date is not present in the response
    # e.g game: 210710
    try:
        return datetime.fromtimestamp(
            response["first_release_date"],
            tz=settings.TZ,
        ).strftime("%Y-%m-%d")
    except KeyError:
        return None


def get_str_list(response, field):
    """Return the list of names from a list of dictionaries."""
    # when no data of field, field is not present in the response
    # e.g game: 25222
    try:
        return ", ".join(item["name"] for item in response[field])
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


def get_parent(parent_game):
    """Return the parent game to the selected game."""
    if parent_game:
        return [
            {
                "media_id": parent_game["id"],
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
                "media_id": game["id"],
                "title": game["name"],
                "image": get_image_url(game),
            }
            for game in related_medias
        ]
    return []
