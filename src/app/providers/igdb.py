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

        response = services.api_request("POST", url, json=json)

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
        params = {
            "search": query,
            "fields": "name,cover.image_id",
            "where": "themes != (42);",  # exclude erotic games
        }
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        response = services.api_request("GET", url, params=params, headers=headers)

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


def get_image_url(response):
    """Return the image URL for the media."""
    # when no image, cover is not present in the response
    # e.g game: 287348
    if response.get("cover"):
        return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{response['cover']['image_id']}.jpg"
    return settings.IMG_NONE
