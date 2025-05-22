import logging

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://comicvine.gamespot.com/api"
headers = {
    "User-Agent": "Mozilla/5.0",
}


def handle_error(error):
    """Handle ComicVine API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.COMICVINE.value, error) from json_error

    # Handle invalid API key
    if status_code == requests.codes.unauthorized:
        details = error_json["error"]
        raise services.ProviderAPIError(Sources.COMICVINE.value, error, details)

    raise services.ProviderAPIError(Sources.COMICVINE.value, error)


def search(query, page):
    """Search for comics on Comic Vine."""
    cache_key = (
        f"search_{Sources.COMICVINE.value}_{MediaTypes.COMIC.value}_{query}_{page}"
    )
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "query": query,
            "resources": "volume",
            "field_list": "id,name,image",
            "limit": settings.PER_PAGE,
            "page": page,
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/search/",
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        results = [
            {
                "media_id": str(item["id"]),
                "source": Sources.COMICVINE.value,
                "media_type": MediaTypes.COMIC.value,
                "title": item["name"],
                "image": get_image(item),
            }
            for item in response["results"]
        ]

        total_results = response["number_of_total_results"]
        data = helpers.format_search_response(
            page,
            settings.PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def comic(media_id):
    """Return the metadata for the selected comic volume from Comic Vine."""
    cache_key = f"{Sources.COMICVINE.value}_{MediaTypes.COMIC.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": (
                "publisher,site_detail_url,name,last_issue,image,description,"
                "concepts,start_year,count_of_issues,people,date_last_updated"
            ),
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/volume/4050-{media_id}/",
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        response = response.get("results", {})
        publisher_id = response["publisher"]["id"]
        recommendations = []
        if publisher_id:
            recommendations = get_similar_comics(publisher_id, media_id)

        data = {
            "media_id": media_id,
            "source": Sources.COMICVINE.value,
            "source_url": response["site_detail_url"],
            "media_type": MediaTypes.COMIC.value,
            "title": response["name"],
            "max_progress": get_issue_number(response["last_issue"]["issue_number"]),
            "image": get_image(response),
            "synopsis": get_synopsis(response),
            "genres": get_genres(response),
            "score": None,
            "score_count": None,
            "details": {
                "start_date": get_start_year(response),
                "publisher": get_publisher_name(response),
                "issues_count": get_issues_count(response),
                "last_issue_name": get_last_issue_name(response),
                "last_issue_number": get_last_issue_number(response),
                "people": get_people(response),
                "last_updated": response.get("date_last_updated").split()[0],
            },
            "related": {
                "from_the_same_publisher": recommendations,
            },
            # used for events fetching
            "last_issue_id": response["last_issue"]["id"],
        }

        cache.set(cache_key, data)

    return data


def get_image(response):
    """Return the image URL."""
    if "image" in response:
        return response["image"]["medium_url"]
    return settings.IMG_NONE


def get_issue_number(issue_number):
    """
    Return the last issue number as an integer if possible.

    For compound issue numbers (like "463-464"), returns the highest number.
    Returns None if no valid issue number can be extracted.
    """
    try:
        return int(issue_number)
    except ValueError:
        # Handle compound issue numbers like "463-464"
        try:
            # Split by hyphen and get the highest number
            parts = [int(part.strip()) for part in issue_number.split("-")]
            return max(parts)
        except (ValueError, AttributeError):
            return None


def get_synopsis(response):
    """Return the synopsis."""
    if not response.get("description"):
        return "No synopsis available"

    soup = BeautifulSoup(response["description"], "html.parser")
    text = soup.get_text(separator=" ")
    return " ".join(text.split())


def get_genres(response):
    """Return the list of genres."""
    if "concepts" in response:
        return [concept["name"] for concept in response["concepts"][:5]]
    return None


def get_start_year(response):
    """Return the start year of the comic volume."""
    return response.get("start_year")


def get_publisher_name(response):
    """Return the publisher name of the comic volume."""
    publisher = response.get("publisher")
    if publisher and isinstance(publisher, dict):
        return publisher.get("name")
    return None


def get_issues_count(response):
    """Return the count of issues in the comic volume."""
    return response.get("count_of_issues")


def get_last_issue_name(response):
    """Return the name of the last issue in the comic volume."""
    last_issue = response.get("last_issue")
    if last_issue and isinstance(last_issue, dict):
        return last_issue.get("name")
    return None


def get_last_issue_number(response):
    """Return the last issue number."""
    last_issue = response.get("last_issue")
    if last_issue and isinstance(last_issue, dict):
        return last_issue.get("issue_number")
    return None


def get_people(response):
    """Return the people associated with the comic volume."""
    people = response.get("people", [])
    return [person["name"] for person in people[:5] if isinstance(person, dict)]


def get_similar_comics(publisher_id, current_id, limit=10):
    """Get similar comics from the same publisher."""
    cache_key = f"{Sources.COMICVINE.value}_similar_{publisher_id}_{current_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": "id,name,image,start_year,publisher",
            "filter": f"publisher:{publisher_id}",
            "limit": limit + 1,  # Get one extra to account for current comic
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/volumes/",
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # Filter out the current comic and format the response
        data = [
            {
                "media_id": str(item["id"]),
                "source": Sources.COMICVINE.value,
                "media_type": MediaTypes.COMIC.value,
                "title": item["name"],
                "image": get_image(item),
            }
            for item in response["results"]
            if str(item["id"]) != current_id
        ][:limit]

        cache.set(cache_key, data)

    return data


def issue(media_id):
    """Return the metadata for the selected comic issue from Comic Vine."""
    cache_key = f"{Sources.COMICVINE.value}_issue_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": ("cover_date,store_date"),
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/issue/4000-{media_id}/",
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        response = response.get("results", {})

        data = {
            "cover_date": response.get("cover_date"),
            "store_date": response.get("store_date"),
        }

        cache.set(cache_key, data)

    return data
