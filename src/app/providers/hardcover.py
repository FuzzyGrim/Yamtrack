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
    cache_key = f"{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_{media_id}"
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
            handle_error(error)

        if "errors" in response:
            logger.error("GraphQL errors: %s", response["errors"])
            return None

        book_data = response["data"]["books_by_pk"]
        edition_details = get_edition_details(book_data.get("default_cover_edition"))
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
                "publish_date": book_data.get("release_date"),
                "author": book_data.get("cached_contributors"),
                "publisher": edition_details.get("publisher"),
                "isbn": edition_details.get("isbn"),
            },
            "related": {
                "recommendations": get_recommendations(recommendations),
            },
        }

        cache.set(cache_key, data)

    return data


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
    }


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
