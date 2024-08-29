import logging

import app

logger = logging.getLogger(__name__)


def importer(username, user):
    """Import anime and manga ratings from Kitsu."""
    user_id = get_user_id(username)


def get_user_id(username):
    """Get the user ID from Kitsu."""
    url = "https://kitsu.io/api/edge/users"
    response = app.providers.services.api_request(
        "KITSU",
        "GET",
        url,
        params={"filter[name]": username},
    )
    print(response)
    return response["data"][0]["id"]
