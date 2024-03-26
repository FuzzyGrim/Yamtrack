from datetime import datetime

from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://api.igdb.com/v4"


def get_acess_token():
    """Return the access token for the IGDB API."""
    access_token = cache.get("igdb_access_token")

    if not access_token:
        url = "https://id.twitch.tv/oauth2/token"
        json = {
            "client_id": settings.IGDB_ID,
            "client_secret": settings.IGDB_SECRET,
            "grant_type": "client_credentials",
        }

        response = services.api_request("POST", url, params=json)

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

    if not data:
        access_token = get_acess_token()

        url = f"{base_url}/games"
        data = (
            "fields name,cover.image_id;"
            f'search "{query}";'
            "where category = (0,8,9) & themes != 42;"
        )  # exclude adult and select only main video games, remakes and remasters

        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        response = services.api_request("POST", url, data=data, headers=headers)

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

    if not data:
        access_token = get_acess_token()

        url = f"{base_url}/games"
        data = (
            "fields name,cover.image_id,summary,category,first_release_date,"
            "genres.name,themes.name,platforms.name,involved_companies.company.name,"
            "similar_games.name,similar_games.cover.image_id;"
            f"where id = {media_id};"
        )
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        response = services.api_request("POST", url, data=data, headers=headers)
        response = response[0]  # response is a list with a single element

        data = {
            "media_id": response["id"],
            "media_type": "game",
            "title": response["name"],
            "max_progress": "Unknown",
            "image": get_image_url(response),
            "synopsis": response["summary"],
            "details": {
                "format": get_category(response["category"]),
                "start_date": get_start_date(response),
                "genres": get_str_list(response, "genres"),
                "themes": get_str_list(response, "themes"),
                "platforms": get_str_list(response, "platforms"),
                "companies": get_companies(response),
            },
            "related": {
                "recommendations": get_related(response["similar_games"]),
            },
        }

        cache.set(f"game_{media_id}", data)

    return data


def get_image_url(response):
    """Return the image URL for the media."""
    # when no image, cover is not present in the response
    # e.g game: 287348
    if response.get("cover"):
        return f"https://images.igdb.com/igdb/image/upload/t_original/{response['cover']['image_id']}.jpg"
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
        10: "Expanded_game",
        11: "Port",
        12: "Fork",
        13: "Pack",
        14: "Update",
    }

    return category_mapping[category_id]


def get_start_date(response):
    """Return the start date of the game."""
    # when no release date, first_release_date is not present in the response
    # e.g game: 210710
    if "first_release_date" in response:
        return datetime.fromtimestamp(
            response["first_release_date"],
            tz=settings.TZ,
        ).strftime(
            "%Y-%m-%d",
        )
    return "Unknown"


def get_str_list(response, field):
    """Return the list of names from a list of dictionaries."""
    # when no data of field, field is not present in the response
    # e.g game: 25222
    if field in response:
        return ", ".join(item["name"] for item in response[field])
    return "Unknown"


def get_companies(response):
    """Return the companies involved in the game."""
    # when no companies, involved_companies is not present in the response
    # e.g game: 238417
    if "involved_companies" in response:
        return ", ".join(
            company["company"]["name"] for company in response["involved_companies"]
        )
    return "Unknown"


def get_related(related_medias):
    """Return the related games to the selected game."""
    return [
        {
            "media_id": game["id"],
            "title": game["name"],
            "image": get_image_url(game),
        }
        for game in related_medias
    ]
