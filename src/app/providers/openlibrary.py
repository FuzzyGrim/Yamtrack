import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

base_url = "https://openlibrary.org/api"
search_url = "https://openlibrary.org/search.json"


def handle_error(error):
    """Handle Open Library API errors."""
    raise services.ProviderAPIError(
        Sources.OPENLIBRARY.value,
        error,
    )


def search(query, page):
    """Search for books on Open Library."""
    cache_key = (
        f"search_{Sources.OPENLIBRARY.value}_{MediaTypes.BOOK.value}_{query}_{page}"
    )
    data = cache.get(cache_key)

    if data is None:
        params = {
            "q": query,
            "fields": "title,key,cover_edition_key,cover_i,editions",
            "limit": settings.PER_PAGE,
            "page": page,
        }

        try:
            response = services.api_request(
                Sources.OPENLIBRARY.value,
                "GET",
                search_url,
                params=params,
            )
        except requests.RequestException as e:
            handle_error(e)

        results = [
            {
                "media_id": media_id,
                "source": Sources.OPENLIBRARY.value,
                "media_type": MediaTypes.BOOK.value,
                "title": doc["title"],
                "image": get_image_url(doc),
            }
            for doc in response.get("docs", [])
            if (media_id := get_media_id(doc)) and "title" in doc
        ]

        total_results = response["numFound"]
        data = helpers.format_search_response(
            page,
            settings.PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)
    return data


def extract_openlibrary_id(path):
    """
    Extract the ID from an OpenLibrary path.

    Args:
        path (str): A path like '/works/OL123W' or a full URL

    Returns:
        str: The extracted ID (e.g., 'OL123W')
    """
    if not path:
        return None

    # Handle both full URLs and path fragments
    return path.rstrip("/").split("/")[-1]


def get_media_id(doc):
    """Get media ID from document with fallback logic."""
    if "cover_edition_key" in doc:
        return doc["cover_edition_key"]

    try:
        # Fallback to top ranked edition key
        extract_openlibrary_id(doc["editions"]["docs"][0]["key"])
    except IndexError:
        # editions docs is empty
        return None


def book(media_id):
    """Get metadata for a book from Open Library."""
    return asyncio.run(async_book(media_id))


async def async_book(media_id):
    """Asynchronous implementation of book metadata retrieval."""
    cache_key = f"{Sources.OPENLIBRARY.value}_{MediaTypes.BOOK.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        book_url = f"https://openlibrary.org/books/{media_id}.json"

        try:
            response_book = services.api_request(
                Sources.OPENLIBRARY.value,
                "GET",
                book_url,
            )
        except requests.RequestException as e:
            handle_error(e)

        works = response_book.get("works", [])
        if works:
            work = works[0]
            work_id = extract_openlibrary_id(work["key"])
            work_url = f"https://openlibrary.org/works/{work_id}.json"

            try:
                response_work = services.api_request(
                    Sources.OPENLIBRARY.value,
                    "GET",
                    work_url,
                )
            except requests.RequestException as e:
                handle_error(e)
        else:
            response_work = {}

        # Run authors, editions, and ratings concurrently
        authors_task = asyncio.create_task(
            get_authors(response_work),
        )
        editions_task = asyncio.create_task(
            get_editions(response_book, response_work),
        )
        ratings_task = asyncio.create_task(
            get_ratings(response_work),
        )
        score, score_count = await ratings_task

        data = {
            "media_id": media_id,
            "source": Sources.OPENLIBRARY.value,
            "source_url": f"https://openlibrary.org/books/{media_id}",
            "media_type": MediaTypes.BOOK.value,
            "title": response_book["title"],
            "max_progress": response_book.get("number_of_pages"),
            "image": get_cover_image_url(response_book),
            "synopsis": get_description(response_book, response_work),
            "genres": get_subjects(response_work),
            "score": score,
            "score_count": score_count,
            "details": {
                "physical_format": get_physical_format(response_book),
                "number_of_pages": response_book.get("number_of_pages"),
                "publish_date": get_publish_date(response_book),
                "author": await authors_task,
                "publishers": get_publishers(response_book),
                "isbn": get_isbns(response_book),
            },
            "related": {
                "other_editions": await editions_task,
            },
        }

        cache.set(cache_key, data)

    return data


def get_image_url(doc):
    """Get the cover image URL for a book."""
    # when no picture, cover_i is not present in the response
    # e.g book: OL31949778W
    cover_id = doc.get("cover_i")
    if cover_id:
        return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
    return settings.IMG_NONE


def get_cover_image_url(response):
    """Get the cover image URL from a work response."""
    covers = response.get("covers", [])
    if covers:
        return f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg"
    return settings.IMG_NONE


def get_description(response_book, response_work):
    """Extract and clean up the book description."""
    if "description" in response_book:
        description = response_book["description"]
    elif "description" in response_work:
        description = response_work["description"]
    else:
        description = "No synopsis available."

    # sometimes the description is a dict
    # like {'type': '/type/text', 'value': '...'}
    if isinstance(description, dict):
        description = description["value"]

    if description != "No synopsis available.":
        soup = BeautifulSoup(description, "html.parser")
        text = soup.get_text(separator=" ")
        description = " ".join(text.split())

    return description


def get_physical_format(response):
    """Get the physical format of the book."""
    format_value = response.get("physical_format")
    if format_value:
        return format_value.title()
    return None


def get_publish_date(response):
    """Get the first publication date."""
    if "publish_date" in response:
        publish_date = response["publish_date"].removeprefix("cop. ")

        date_formats = [
            "%B %d, %Y",  # January 19, 2001
            "%d %B %Y",  # 18 March 2025
        ]
        for date_format in date_formats:
            try:
                parsed_date = datetime.strptime(publish_date, date_format).replace(
                    tzinfo=ZoneInfo("UTC"),
                )
                return parsed_date.strftime("%Y-%m-%d")
            except ValueError:
                continue
        # If no format matches, return the original string
        return publish_date
    return None


async def get_authors(response):
    """Get list of author names asynchronously."""
    authors = []
    author_entries = response.get("authors", [])

    async with aiohttp.ClientSession() as session:
        tasks = []
        for author in author_entries:
            if isinstance(author, dict) and "author" in author:
                author_key = author["author"]["key"]
                author_url = f"https://openlibrary.org{author_key}.json"
                tasks.append(fetch_author_data(session, author_url))

        author_data_list = await asyncio.gather(*tasks)
        authors = [
            data.get("name", "Unknown Author") for data in author_data_list if data
        ]

    return authors if authors else None


async def fetch_author_data(session, url):
    """Fetch author data asynchronously."""
    async with session.get(url) as response:
        if response.status == requests.codes.ok:
            return await response.json()

    return None


def get_subjects(response):
    """Get list of subjects/genres."""
    if "subjects" in response:
        return response["subjects"][:5]
    return None


def get_publishers(response):
    """Get list of publishers."""
    if "publishers" in response:
        return response.get("publishers", [])[:5]
    return None


def get_isbns(response):
    """Get list of ISBNs."""
    isbn_13 = response.get("isbn_13", [])
    isbn_10 = response.get("isbn_10", [])
    isbns = isbn_13 + isbn_10
    if isbns:
        return isbns
    return None


async def get_editions(response_book, response_work):
    """Get list of editions asynchronously."""
    book_id = extract_openlibrary_id(response_book.get("key", ""))
    work_id = extract_openlibrary_id(response_work.get("key", ""))

    if not work_id:
        work_id = book_id

    # limit to 500 editions, pagination is not supported
    url = f"https://openlibrary.org/works/{work_id}/editions.json?limit=500"

    async with aiohttp.ClientSession() as session, session.get(url) as response:
        if response.status == requests.codes.ok:
            data = await response.json()
            return [
                {
                    "source": Sources.OPENLIBRARY.value,
                    "source_url": f"https://openlibrary.org/books/{extract_openlibrary_id(edition['key'])}",
                    "media_id": extract_openlibrary_id(edition["key"]),
                    "media_type": MediaTypes.BOOK.value,
                    "title": edition.get("title"),
                    "image": get_cover_image_url(edition),
                }
                for edition in data["entries"]
                if extract_openlibrary_id(edition["key"]) != book_id
                and edition.get("title")
            ]
    return []


async def get_ratings(response_work):
    """Get ratings data for a book asynchronously."""
    work_id = extract_openlibrary_id(response_work.get("key", ""))

    if not work_id:
        return None, None

    url = f"https://openlibrary.org/works/{work_id}/ratings.json"

    async with aiohttp.ClientSession() as session, session.get(url) as response:
        if response.status == requests.codes.ok:
            data = await response.json()
            summary = data.get("summary", {})
            average = summary.get("average")
            count = summary.get("count")

            if average and count:
                # Convert to 10-point scale (multiply by 2) and round to 1 decimal place
                score = round(summary["average"] * 2, 1)
                score_count = summary["count"]
                return score, score_count

    return None, 0
