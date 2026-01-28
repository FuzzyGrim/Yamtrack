import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

base_url = "https://api.hardcover.app/v1/graphql"


def handle_error(error):
    """Handle Hardcover API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.HARDCOVER.value, error) from json_error

    if status_code == requests.codes.unauthorized:
        details = error_json["error"]
        raise services.ProviderAPIError(Sources.HARDCOVER.value, error, details)

    raise services.ProviderAPIError(Sources.HARDCOVER.value, error)


def search(query, page):
    """Search for books on Hardcover."""
    cache_key = (
        f"search_{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_{query}_{page}"
    )
    data = cache.get(cache_key)

    if data is None:
        search_query = """
        query SearchBooks($query: String!, $per_page: Int!, $page: Int!) {
          search(
            query: $query,
            query_type: "Book",
            per_page: $per_page,
            page: $page,
          ) {
            results
          }
        }
        """

        variables = {
            "query": query,
            "per_page": settings.PER_PAGE,
            "page": page,
        }

        try:
            response = services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                base_url,
                params={"query": search_query, "variables": variables},
                headers={"Authorization": settings.HARDCOVER_API},
            )
        except requests.exceptions.HTTPError as error:
            response = handle_error(error)

        hits = response["data"]["search"]["results"]["hits"]
        results = [
            {
                "media_id": hit["document"]["id"],
                "source": Sources.HARDCOVER.value,
                "media_type": MediaTypes.BOOK.value,
                "title": hit["document"]["title"],
                "image": get_image_url(hit["document"]),
            }
            for hit in hits
        ]
        total_results = response["data"]["search"]["results"]["found"]

        data = helpers.format_search_response(
            page,
            settings.PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def book(media_id):
    """Get metadata for a book from Hardcover."""
    cache_key = f"{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_{media_id}_v3"
    data = cache.get(cache_key)

    if data is None:
        book_query = """
        query GetBookDetails($book_id: Int!) {
          books_by_pk(id: $book_id) {
            id
            title
            cached_image(path: "url")
            description
            cached_tags(path: "Genre")
            rating
            ratings_count
            pages
            release_date
            slug
            canonical_id
            cached_contributors(path: "[0]['author']['name']")
            default_cover_edition {
              edition_format
              isbn_13
              isbn_10
              release_date
              publisher {
                name
              }
            }
          }
        }
        """

        variables = {
            "book_id": int(media_id),
        }

        try:
            response = services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                base_url,
                params={"query": book_query, "variables": variables},
                headers={"Authorization": settings.HARDCOVER_API},
            )
        except requests.exceptions.HTTPError as error:
            logger.error("HTTP error fetching book %s: %s", media_id, error)
            handle_error(error)

        if "errors" in response:
            logger.error("GraphQL errors for book %s: %s", media_id, response["errors"])
            logger.error("Full response: %s", response)
            return None
        
        if "data" not in response:
            logger.error("No 'data' key in response for book %s. Full response: %s", media_id, response)
            return None

        book_data = response["data"].get("books_by_pk")
        if not book_data:
            services.raise_not_found_error(
                Sources.HARDCOVER.value,
                media_id,
                "book",
            )
            
        logger.info("Hardcover book data for ID %s: title=%s, has_default_cover_edition=%s, language=%s", 
                   media_id, 
                   book_data.get("title"),
                   bool(book_data.get("default_cover_edition")),
                   book_data.get("language"))
        
        edition_details = get_edition_details(book_data.get("default_cover_edition"))
        publishers = get_publishers(book_data)
        isbns = get_isbns_from_book(book_data)

        # Resolve author name to OpenLibrary author so we can link to the author page.
        # Always set details.authors when we have a name so the template can render
        # either a person_detail link (if OL resolves) or an OpenLibrary search fallback.
        authors_for_details = None
        author_name = book_data.get("cached_contributors")
        if author_name and isinstance(author_name, str) and author_name.strip():
            from app.providers import openlibrary
            an = author_name.strip()
            ol = openlibrary.search_author_by_name(an)
            if ol:
                authors_for_details = [{
                    "name": ol.get("name") or an,
                    "person_id": ol["person_id"],
                    "source": Sources.OPENLIBRARY.value,
                }]
            else:
                authors_for_details = [{"name": an}]

        # Prefer release_date from default_cover_edition if available, otherwise use book's release_date
        default_edition = book_data.get("default_cover_edition")
        edition_release_date = default_edition.get("release_date") if default_edition else None
        book_release_date = book_data.get("release_date")
        
        # Use edition date if available, otherwise fall back to book date
        raw_release_date = edition_release_date or book_release_date
        release_date = format_release_date(raw_release_date)
        
        logger.info("Raw release_date from Hardcover for book %s: book=%s, edition=%s, using=%s", 
                   media_id, book_release_date, edition_release_date, raw_release_date)
        logger.info("Extracted details for book %s: publishers=%s, isbns=%s, release_date=%s", 
                   media_id, publishers, isbns, release_date)
        
        recommendations = None

        # Try multiple approaches to get recommendations
        recommendations = []
        book_id = int(media_id)
        canonical_id = book_data.get("canonical_id")
        
        # Approach 1: Try nested recommendations on the book itself
        try:
            nested_query = """
            query GetBookWithRecommendations($book_id: Int!) {
              books_by_pk(id: $book_id) {
                recommendations(limit: 10) {
                  item_book {
                    id
                    title
                    cached_image(path: "url")
                  }
                }
              }
            }
            """
            nested_resp = services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                base_url,
                params={"query": nested_query, "variables": {"book_id": book_id}},
                headers={"Authorization": settings.HARDCOVER_API},
            )
            nested_recs = nested_resp["data"]["books_by_pk"].get("recommendations", [])
            if nested_recs:
                recommendations = nested_recs
        except Exception as e:
            logger.warning(f"Nested recommendations failed: {e}")
        
        # Approach 2: Try top-level recommendations with canonical_id
        if not recommendations and canonical_id:
            try:
                top_level_query = """
                query GetRecommendationsByCanonicalId($rec_id: bigint!) {
                  recommendations(
                    where: {
                      subject_id: {_eq: $rec_id},
                      subject_type: {_eq: "Book"},
                      item_type: {_eq: "Book"}
                    },
                    limit: 10
                  ) {
                    item_book {
                      id
                      title
                      cached_image(path: "url")
                    }
                  }
                }
                """
                top_resp = services.api_request(
                    Sources.HARDCOVER.value,
                    "POST",
                    base_url,
                    params={"query": top_level_query, "variables": {"rec_id": int(canonical_id)}},
                    headers={"Authorization": settings.HARDCOVER_API},
                )
                top_recs = top_resp["data"].get("recommendations", [])
                if top_recs:
                    recommendations = top_recs
            except Exception as e:
                logger.warning(f"Top-level recommendations failed: {e}")
        
        # Approach 3: Try with book_id instead of canonical_id
        if not recommendations:
            try:
                book_id_query = """
                query GetRecommendationsByBookId($rec_id: bigint!) {
                  recommendations(
                    where: {
                      subject_id: {_eq: $rec_id},
                      subject_type: {_eq: "Book"},
                      item_type: {_eq: "Book"}
                    },
                    limit: 10
                  ) {
                    item_book {
                      id
                      title
                      cached_image(path: "url")
                    }
                  }
                }
                """
                book_id_resp = services.api_request(
                    Sources.HARDCOVER.value,
                    "POST",
                    base_url,
                    params={"query": book_id_query, "variables": {"rec_id": book_id}},
                    headers={"Authorization": settings.HARDCOVER_API},
                )
                book_id_recs = book_id_resp["data"].get("recommendations", [])
                if book_id_recs:
                    recommendations = book_id_recs
            except Exception as e:
                logger.warning(f"Book ID recommendations failed: {e}")
        
        # Fallback: If no recommendations, get books by same author
        if not recommendations:
            author_name = book_data.get("cached_contributors")
            if author_name:
                try:
                    author_query = """
                    query BooksByAuthor($author: String!, $exclude_id: Int!) {
                      books(
                        where: {
                          id: {_neq: $exclude_id},
                          contributions: {author: {name: {_eq: $author}}}
                        },
                        limit: 10,
                        order_by: {users_count: desc}
                      ) {
                        id
                        title
                        cached_image(path: "url")
                      }
                    }
                    """
                    author_resp = services.api_request(
                        Sources.HARDCOVER.value,
                        "POST",
                        base_url,
                        params={"query": author_query, "variables": {"author": author_name, "exclude_id": book_id}},
                        headers={"Authorization": settings.HARDCOVER_API},
                    )
                    author_books = author_resp["data"].get("books", [])
                    # Convert to recommendations format
                    recommendations = [{"item_book": book} for book in author_books]
                except Exception as e:
                    logger.warning(f"Author fallback failed: {e}")

        data = {
            "media_id": book_data["id"],
            "source": Sources.HARDCOVER.value,
            "source_url": f"https://hardcover.app/books/{book_data['slug']}",
            "media_type": MediaTypes.BOOK.value,
            "title": book_data["title"],
            "max_progress": book_data.get("pages"),
            "image": book_data.get("cached_image") or settings.IMG_NONE,
            "synopsis": book_data.get("description") or "No synopsis available.",
            "genres": get_tags(book_data.get("cached_tags")),
            "score": get_ratings(book_data.get("rating")),
            "score_count": book_data.get("ratings_count", 0),
            "details": {
                "format": edition_details.get("format"),
                "number_of_pages": book_data.get("pages"),
                "publish_date": release_date,
                "release_date": release_date,  # Formatted release date for details display
                "author": book_data.get("cached_contributors"),
                "authors": authors_for_details,
                "publishers": publishers,
                "isbn": isbns,
            },
            "related": {
                "recommendations": get_recommendations(recommendations),
            },
        }

        cache.set(cache_key, data)

    return data


def format_release_date(release_date):
    """Format release date from Hardcover API.
    
    Hardcover may return:
    - Full date string (YYYY-MM-DD)
    - Just a year (YYYY)
    - None
    
    If it's just a year, return just the year.
    If it's a full date, return it as-is.
    """
    if not release_date:
        return None
    
    # If it's already a string, check the format
    if isinstance(release_date, str):
        # Check if it's just a year (4 digits)
        if len(release_date) == 4 and release_date.isdigit():
            return release_date
        # Check if it's a date with January 1st (likely just a year stored as date)
        if release_date.endswith("-01-01"):
            year = release_date[:4]
            if year.isdigit():
                logger.debug("Release date %s appears to be just a year, returning %s", release_date, year)
                return year
        # Return the date as-is if it's a proper date
        return release_date
    
    # If it's a date object, format it
    if hasattr(release_date, 'year'):
        # If it's January 1st, it's likely just a year
        if release_date.month == 1 and release_date.day == 1:
            return str(release_date.year)
        return release_date.strftime("%Y-%m-%d")
    
    return str(release_date)


def get_tags(tags_data):
    """Get processed tags/genres from API data."""
    if not tags_data:
        return None
    return [tag["tag"] for tag in tags_data]


def get_ratings(rating_data):
    """Get processed rating from API data."""
    if not rating_data:
        return None
    return round(float(rating_data) * 2, 1)


def get_edition_details(edition_data):
    """Get processed edition details from API data."""
    if not edition_data:
        return {}

    isbns = []
    if edition_data.get("isbn_10"):
        isbns.append(edition_data["isbn_10"])
    if edition_data.get("isbn_13"):
        isbns.append(edition_data["isbn_13"])

    publisher_name = None
    if edition_data.get("publisher"):
        publisher_name = edition_data["publisher"].get("name")

    return {
        "format": edition_data.get("edition_format") or "Unknown",
        "publisher": publisher_name,
        "isbn": isbns if isbns else None,
        "release_date": edition_data.get("release_date"),
    }


def get_publishers(book_data):
    """Get list of unique publishers from book data."""
    publishers = set()
    
    # Get publisher from default cover edition
    default_edition = book_data.get("default_cover_edition")
    if default_edition and default_edition.get("publisher"):
        publisher_name = default_edition["publisher"].get("name")
        if publisher_name:
            publishers.add(publisher_name)
    
    # Note: We're only getting publisher from default_cover_edition for now
    # If we need publishers from all editions, we'd need a separate query
    
    result = list(publishers) if publishers else None
    logger.debug("Extracted publishers: %s", result)
    return result


def get_languages(book_data):
    """Get list of languages from book data."""
    language = book_data.get("language")
    
    if not language:
        logger.debug("No language found in book data")
        return None
    
    # Language might be a string or a list
    if isinstance(language, list):
        languages = [lang for lang in language if lang]
    else:
        languages = [language] if language else []
    
    # Convert language codes to readable names if needed
    language_names = {
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "it": "Italian",
        "pt": "Portuguese",
        "ru": "Russian",
        "ja": "Japanese",
        "zh": "Chinese",
        "ar": "Arabic",
        "hi": "Hindi",
        "ko": "Korean",
        "nl": "Dutch",
        "pl": "Polish",
        "sv": "Swedish",
        "no": "Norwegian",
        "da": "Danish",
        "fi": "Finnish",
        "el": "Greek",
        "he": "Hebrew",
        "tr": "Turkish",
        "cs": "Czech",
        "hu": "Hungarian",
        "ro": "Romanian",
    }
    
    result = []
    for lang in languages:
        # If it's already a readable name, use it
        if len(lang) > 2 or lang not in language_names:
            result.append(lang)
        else:
            # Convert code to name
            result.append(language_names.get(lang, lang.upper()))
    
    logger.debug("Extracted languages: %s (from %s)", result, language)
    return result if result else None


def get_isbns_from_book(book_data):
    """Get all ISBNs from book data."""
    isbns = []
    
    # Get ISBNs from default cover edition
    default_edition = book_data.get("default_cover_edition")
    if default_edition:
        if default_edition.get("isbn_13"):
            isbns.append(default_edition["isbn_13"])
        if default_edition.get("isbn_10"):
            isbns.append(default_edition["isbn_10"])
    
    # Note: For now we only get ISBNs from default_cover_edition
    # If we need ISBNs from all editions, we can use the existing get_book_isbns function
    
    result = isbns if isbns else None
    logger.debug("Extracted ISBNs: %s", result)
    return result


def get_recommendations(recommendations_data):
    """Get processed recommendations from API data."""
    if not recommendations_data:
        return []

    return [
        {
            "media_id": rec["item_book"]["id"],
            "source": Sources.HARDCOVER.value,
            "title": rec["item_book"]["title"],
            "media_type": MediaTypes.BOOK.value,
            "image": rec["item_book"].get("cached_image") or settings.IMG_NONE,
        }
        for rec in recommendations_data
        if rec.get("item_book")
    ]


def get_image_url(response):
    """Get the cover image URL for a book."""
    if response.get("image") and response["image"].get("url"):
        return response["image"]["url"]
    return settings.IMG_NONE


def get_book_isbns(media_id):
    """
    Get ISBNs for a book from Hardcover, including editions.
    
    Args:
        media_id: Hardcover book ID
        
    Returns:
        List of ISBN-10 and ISBN-13 numbers
    """
    query = """
    query GetBookISBNs($book_id: Int!) {
      books_by_pk(id: $book_id) {
        editions {
          isbn_10
          isbn_13
        }
        default_cover_edition {
          isbn_10
          isbn_13
        }
      }
    }
    """
    
    variables = {"book_id": int(media_id)}
    
    try:
        response = services.api_request(
            Sources.HARDCOVER.value,
            "POST",
            base_url,
            params={"query": query, "variables": variables},
            headers={"Authorization": settings.HARDCOVER_API},
        )
        
        if "errors" in response:
            logger.error("GraphQL errors fetching ISBNs: %s", response["errors"])
            return []
        
        book_data = response["data"]["books_by_pk"]
        isbns = []
        
        # Get ISBNs from default cover edition first
        default_edition = book_data.get("default_cover_edition")
        if default_edition:
            if default_edition.get("isbn_13"):
                isbns.append(default_edition["isbn_13"])
            if default_edition.get("isbn_10"):
                isbns.append(default_edition["isbn_10"])
        
        # Get ISBNs from other editions
        editions = book_data.get("editions", [])
        for edition in editions:
            if edition.get("isbn_13") and edition["isbn_13"] not in isbns:
                isbns.append(edition["isbn_13"])
            if edition.get("isbn_10") and edition["isbn_10"] not in isbns:
                isbns.append(edition["isbn_10"])
        
        return isbns
        
    except Exception as e:
        logger.error(f"Error fetching ISBNs for book {media_id}: {e}")
        return []
