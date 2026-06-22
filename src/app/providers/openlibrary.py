import asyncio
import logging
from datetime import datetime
from urllib.parse import quote
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
headers = {"User-Agent": "Yamtrack/1.0 (github@fuzzygrim.com)"}


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
            "fields": "title,key,editions,editions.key,editions.cover_i,editions.title",
            "limit": settings.PER_PAGE,
            "page": page,
        }

        try:
            response = services.api_request(
                Sources.OPENLIBRARY.value,
                "GET",
                search_url,
                params=params,
                headers=headers,
            )
        except requests.RequestException as e:
            handle_error(e)

        results = []
        for doc in response.get("docs", []):
            if doc["editions"]["docs"] == []:
                continue

            top_edition = doc["editions"]["docs"][0]
            media_id = extract_openlibrary_id(top_edition["key"])
            title = doc["title"]
            edition_title = top_edition["title"]

            if edition_title != title:
                result_title = f"{edition_title}: {title}"
            else:
                result_title = title

            results.append(
                {
                    "media_id": media_id,
                    "source": Sources.OPENLIBRARY.value,
                    "media_type": MediaTypes.BOOK.value,
                    "title": result_title,
                    "image": get_image_url(top_edition),
                },
            )

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
        path (str): A path like '/works/OL123W' or 'OL123A'

    Returns:
        str: The extracted ID (e.g., 'OL123W')
    """
    if not path:
        return None
    s = str(path).strip()
    if "/" in s:
        return s.rstrip("/").split("/")[-1]
    return s


def _author_name_variants(name):
    """
    Generate search query variants for an author name to handle spelling,
    punctuation, and initial formats (e.g. "J.K. Rowling", "J. K. Rowling", "JK Rowling").
    """
    s = str(name).strip()
    if not s:
        return []
    variants = [s]
    parts = s.replace(".", " ").split()
    if not parts:
        return variants
    # Last part is usually surname
    last = parts[-1] if parts else ""
    # Build variants for initials + last (e.g. "J. K. Rowling" -> "J K Rowling", "JK Rowling", "Rowling")
    if len(parts) >= 2 and all(len(p) <= 2 for p in parts[:-1]):
        initials = "".join(p[0] for p in parts[:-1] if p).upper()
        with_space = " ".join(parts[:-1]) + " " + last
        if with_space.strip() and with_space.strip() not in variants:
            variants.append(with_space.strip())
        if initials and last:
            jk_style = initials + " " + last
            if jk_style not in variants:
                variants.append(jk_style)
    if last and len(last) > 2 and last not in variants:
        variants.append(last)
    # "J. K. Rowling" style (space after each initial) when we have single-letter parts
    if len(parts) >= 2 and all(len(p) == 1 and p.isalpha() for p in parts[:-1]):
        spaced = " ".join(p + "." for p in parts[:-1]) + (" " + last if last else "")
        if spaced.strip() not in variants:
            variants.append(spaced.strip())
    return variants


def _extract_person_id_from_author_doc(doc):
    """
    Get OpenLibrary person_id from a search/authors.json doc.
    The API can return key as "/authors/OL123A" or "OL123A".
    """
    key = doc.get("key") or doc.get("olid")
    if not key:
        return None
    key = str(key).strip()
    if not key:
        return None
    if key.startswith("/authors/"):
        return extract_openlibrary_id(key)
    # Plain OLID e.g. "OL23919A"
    if key.upper().startswith("OL") and len(key) >= 4:
        return key
    return None


def _pick_best_author_doc(docs, preferred_name):
    """
    From search results, pick the author that best matches the name we searched for.
    Prefers name/alternate_names match, then highest work_count.
    """

    def _norm(t):
        return "".join(c for c in (t or "").lower() if c.isalnum())

    pref = _norm(preferred_name)
    if not pref:
        # pick by work_count
        for d in sorted(docs, key=lambda d: -(d.get("work_count") or 0)):
            pid = _extract_person_id_from_author_doc(d)
            if pid:
                return d, pid
        return None, None

    best = None
    best_score = -1
    best_pid = None

    for d in docs:
        pid = _extract_person_id_from_author_doc(d)
        if not pid:
            continue
        name = (d.get("name") or "").lower()
        alts = [a for a in (d.get("alternate_names") or []) if a]
        nn = _norm(d.get("name"))
        in_name = pref == nn or pref in nn or nn in pref
        in_alt = any(pref in _norm(a) or _norm(a) in pref for a in alts)
        work = d.get("work_count") or 0
        if in_name:
            score = 1000 + work
        elif in_alt:
            score = 500 + work
        else:
            score = work
        if score > best_score:
            best_score = score
            best = d
            best_pid = pid

    if best is not None:
        return best, best_pid
    # fallback: first doc with valid id
    for d in docs:
        pid = _extract_person_id_from_author_doc(d)
        if pid:
            return d, pid
    return None, None


def search_author_by_name(name):
    """
    Search OpenLibrary for an author by name. Tries multiple query variants
    (punctuation, initials, last-only) and picks the best match. Returns
    {name, person_id} or None.
    """
    if not name or not str(name).strip():
        return None
    q = str(name).strip()
    cache_key = f"{Sources.OPENLIBRARY.value}_author_search_v2_{q}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    variants = _author_name_variants(q)
    seen = set()

    for v in variants:
        if not v or v in seen:
            continue
        seen.add(v)
        try:
            url = f"https://openlibrary.org/search/authors.json?q={quote(v)}&limit=20"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            docs = data.get("docs") or []
            if not docs:
                continue
            doc, person_id = _pick_best_author_doc(docs, q)
            if doc and person_id:
                out = {
                    "name": doc.get("name") or q,
                    "person_id": person_id,
                }
                cache.set(cache_key, out, timeout=86400)
                return out
        except Exception as e:  # noqa: BLE001
            logger.debug("OpenLibrary author search variant %r failed: %s", v, e)
            continue

    # only cache failure briefly so format/API changes can be retried
    cache.set(cache_key, None, timeout=300)
    return None


def get_image_url(doc):
    """Get the cover image URL for a book."""
    try:
        cover_id = doc["cover_i"]
        if cover_id:
            return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

    except KeyError:
        return settings.IMG_NONE


def book(media_id):
    """Get metadata for a book from Open Library."""
    return asyncio.run(async_book(media_id))


async def async_book(media_id):
    """Asynchronous implementation of book metadata retrieval."""
    cache_key = f"{Sources.OPENLIBRARY.value}_{MediaTypes.BOOK.value}_{media_id}_v3"
    data = cache.get(cache_key)

    if data is None:
        book_url = f"https://openlibrary.org/books/{media_id}.json"

        try:
            response_book = services.api_request(
                Sources.OPENLIBRARY.value,
                "GET",
                book_url,
                headers=headers,
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
                    headers=headers,
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

        authors_result = await authors_task
        if not authors_result and response_book:
            authors_result = await get_authors(response_book)

        # Extract details with debugging
        publishers = get_publishers(response_book)
        languages = get_languages(response_book, response_work)
        isbns = get_isbns(response_book)

        logger.info("OpenLibrary book data for ID %s: %s", media_id, {
            "title": response_book.get("title"),
            "has_publishers_in_response": "publishers" in response_book,
            "publishers_value": response_book.get("publishers"),
            "has_languages_in_book": "languages" in response_book,
            "has_languages_in_work": "languages" in response_work,
            "languages_book": response_book.get("languages"),
            "languages_work": response_work.get("languages"),
        })

        logger.info("Extracted details for OpenLibrary book %s: publishers=%s, languages=%s, isbns=%s",
                   media_id, publishers, languages, isbns)

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
                "release_date": get_publish_date(response_book),  # Add release_date for details display
                "author": ", ".join(a["name"] for a in authors_result) if authors_result else None,
                "authors": authors_result,
                "publishers": publishers,
                "isbn": isbns,
                "languages": languages,
            },
            "related": {
                "other_editions": await editions_task,
            },
        }

        cache.set(cache_key, data)

    return data


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
            "%b %d, %Y",  # Oct 01, 2017
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


def _extract_author_key(author):
    """Extract /authors/OL123A key from an author entry. Handles multiple OL JSON shapes."""
    if not isinstance(author, dict):
        return None
    ref = author.get("author")
    if isinstance(ref, dict) and "key" in ref:
        return ref["key"]
    if isinstance(ref, str) and ref.startswith("/authors/"):
        return ref
    if "key" in author and str(author["key"]).startswith("/authors/"):
        return author["key"]
    return None


async def get_authors(response):
    """Get list of author dicts with name, person_id, and source."""
    author_entries = response.get("authors", []) or []
    if not author_entries:
        return None

    to_fetch = []
    for author in author_entries:
        author_key = _extract_author_key(author)
        if author_key:
            author_url = f"https://openlibrary.org{author_key.rstrip('/')}.json"
            to_fetch.append((author_key, author_url))

    if not to_fetch:
        return None

    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [fetch_author_data(session, url) for (_, url) in to_fetch]
        author_data_list = await asyncio.gather(*tasks)

    authors = []
    for i, data in enumerate(author_data_list):
        if i < len(to_fetch) and data:
            author_key = to_fetch[i][0]
            person_id = extract_openlibrary_id(author_key)
            name = data.get("name", "Unknown Author")
            authors.append({
                "name": name,
                "person_id": person_id,
                "source": Sources.OPENLIBRARY.value,
            })

    return authors or None


async def fetch_author_data(session, url):
    """Fetch author data asynchronously."""
    async with session.get(url) as response:
        if response.status == requests.codes.ok:
            return await response.json()

    return None


def person_page(person_id):
    """Return person details and works for the OpenLibrary author page.
    Works are fetched via the search API (author_key) for cover_i. Uses -M
    (medium) cover size for faster loading in the grid.
    """
    cache_key = f"{Sources.OPENLIBRARY.value}_person_{person_id}_v3"
    data = cache.get(cache_key)

    if data is None:
        author_url = f"https://openlibrary.org/authors/{person_id}.json"
        # Search API returns cover_i; no language filter to match original behaviour
        search_url = (
            f"https://openlibrary.org/search.json?author_key={person_id}"
            f"&limit=500&fields=key,title,first_publish_year,cover_i"
        )

        try:
            author_resp = services.api_request(
                Sources.OPENLIBRARY.value,
                "GET",
                author_url,
            )
        except requests.exceptions.RequestException as e:
            handle_error(e)

        try:
            works_resp = services.api_request(
                Sources.OPENLIBRARY.value,
                "GET",
                search_url,
            )
        except requests.exceptions.RequestException as e:
            handle_error(e)

        bio = author_resp.get("bio")
        if isinstance(bio, dict):
            bio = bio.get("value", "")
        biography = (bio or "").strip() or None

        image = settings.IMG_NONE
        if author_resp.get("photos"):
            image = f"https://covers.openlibrary.org/a/olid/{person_id}-L.jpg"

        credits = []
        for doc in works_resp.get("docs", []) or []:
            work_key = doc.get("key", "")
            work_id = extract_openlibrary_id(work_key)
            title = doc.get("title") or ""
            cover_i = doc.get("cover_i")
            work_image = (
                f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg"
                if cover_i
                else settings.IMG_NONE
            )
            fpy = doc.get("first_publish_year")
            year = str(fpy) if fpy is not None else None
            work_url = f"https://openlibrary.org/works/{work_id}" if work_id else None

            credits.append({
                "media_type": MediaTypes.BOOK.value,
                "source": Sources.OPENLIBRARY.value,
                "media_id": work_id or "",
                "title": title,
                "image": work_image,
                "roles": ["Author"],
                "year": year,
                "url": work_url,
            })

        credits.sort(
            key=lambda x: ((x.get("year") or "0000"), (x.get("title") or "")),
            reverse=True,
        )

        data = {
            "source": Sources.OPENLIBRARY.value,
            "person_id": str(person_id),
            "name": author_resp.get("name") or "",
            "image": image,
            "biography": biography,
            "credits": credits,
        }

        cache.set(cache_key, data)

    return data


def get_subjects(response):
    """Get list of subjects/genres."""
    if "subjects" in response:
        return response["subjects"][:5]
    return None


def get_publishers(response):
    """Get list of publishers."""
    publishers = response.get("publishers", [])
    logger.debug("get_publishers: response has 'publishers' key: %s, value: %s", 
                "publishers" in response, publishers)
    if publishers:
        result = publishers[:5]
        logger.debug("get_publishers: returning %s", result)
        return result
    logger.debug("get_publishers: returning None (no publishers found)")
    return None


def get_isbns(response):
    """Get list of ISBNs."""
    isbn_13 = response.get("isbn_13", [])
    isbn_10 = response.get("isbn_10", [])
    isbns = isbn_13 + isbn_10
    if isbns:
        return isbns
    return None


def get_languages(response_book, response_work):
    """Get list of languages from book or work response."""
    # Try book first, then work
    languages_book = response_book.get("languages")
    languages_work = response_work.get("languages")
    languages = languages_book or languages_work
    
    logger.debug("get_languages: book has 'languages': %s, value: %s", 
                "languages" in response_book, languages_book)
    logger.debug("get_languages: work has 'languages': %s, value: %s", 
                "languages" in response_work, languages_work)
    logger.debug("get_languages: selected languages: %s", languages)
    
    if not languages:
        logger.debug("get_languages: returning None (no languages found)")
        return None
    
    # Ensure it's a list
    if not isinstance(languages, list):
        languages = [languages]
    
    # Languages can be stored as:
    # 1. List of dicts with "key" field: [{"key": "/languages/eng"}]
    # 2. List of strings: ["eng", "spa"]
    # 3. List of dicts with "code" field
    
    language_names = []
    language_codes = {
        "eng": "English",
        "spa": "Spanish",
        "fre": "French",
        "ger": "German",
        "ita": "Italian",
        "por": "Portuguese",
        "rus": "Russian",
        "jpn": "Japanese",
        "chi": "Chinese",
        "ara": "Arabic",
        "hin": "Hindi",
        "kor": "Korean",
        "dut": "Dutch",
        "pol": "Polish",
        "swe": "Swedish",
        "nor": "Norwegian",
        "dan": "Danish",
        "fin": "Finnish",
        "gre": "Greek",
        "heb": "Hebrew",
        "tur": "Turkish",
        "cze": "Czech",
        "hun": "Hungarian",
        "rum": "Romanian",
    }
    
    for lang in languages:
        if isinstance(lang, dict):
            # Extract code from key like "/languages/eng"
            key = lang.get("key", "")
            if key:
                code = key.split("/")[-1] if "/" in key else key
            else:
                code = lang.get("code", "")
        else:
            code = lang
        
        # Convert code to readable name
        if code in language_codes:
            language_names.append(language_codes[code])
        elif code:
            # If we don't have a mapping, use the code capitalized
            language_names.append(code.upper())
    
    return language_names if language_names else None


async def get_editions(response_book, response_work):
    """Get list of editions asynchronously."""
    book_id = extract_openlibrary_id(response_book.get("key", ""))
    work_id = extract_openlibrary_id(response_work.get("key", ""))

    if not work_id:
        work_id = book_id

    # limit to 500 editions, pagination is not supported
    url = f"https://openlibrary.org/works/{work_id}/editions.json?limit=500"

    async with (
        aiohttp.ClientSession(headers=headers) as session,
        session.get(url) as response,
    ):
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

    async with (
        aiohttp.ClientSession(headers=headers) as session,
        session.get(url) as response,
    ):
        if response.status == requests.codes.ok:
            data = await response.json()
            summary = data.get("summary", {})
            average = summary.get("average")
            count = summary.get("count")

            if average and count:
                score = round(summary["average"], 1)
                score_count = summary["count"]
                return score, score_count

    return None, 0


def get_book_cover_images(isbns):
    """
    Get book cover images from Open Library using ISBNs.
    
    Args:
        isbns: List of ISBN-10 and ISBN-13 numbers
        
    Returns:
        List of cover image dictionaries with url, thumbnail_url, isbn, and is_original
    """
    if not isbns:
        return []
    
    covers = []
    seen_isbns = set()
    
    # Limit to first 6 ISBNs to avoid rate limiting and improve performance
    # 6 covers per book = ~16 books can be browsed before hitting 100 req/5min limit
    for i, isbn in enumerate(isbns[:6]):
        # Remove dashes and spaces from ISBN
        clean_isbn = isbn.replace("-", "").replace(" ", "")
        
        # Skip duplicates
        if clean_isbn in seen_isbns:
            continue
            
        seen_isbns.add(clean_isbn)
        
        # Build Open Library cover URL with ?default=false to detect missing covers
        cover_url = f"https://covers.openlibrary.org/b/isbn/{clean_isbn}-L.jpg"
        thumbnail_url = f"https://covers.openlibrary.org/b/isbn/{clean_isbn}-M.jpg"
        
        covers.append({
            "url": cover_url,
            "thumbnail_url": thumbnail_url,
            "isbn": isbn,
            "is_original": i == 0,  # First ISBN is considered the original
            "width": 0,
            "height": 0,
            "aspect_ratio": 0.667,
            "language": None,
        })
    
    return covers


async def get_editions_covers(isbns):
    """
    Get book cover images from multiple editions asynchronously.
    
    Args:
        isbns: List of ISBN numbers from the primary edition
        
    Returns:
        List of cover dictionaries from various editions
    """
    if not isbns:
        return []
    
    # Start with covers from the provided ISBNs (limited to 10)
    covers = get_book_cover_images(isbns)
    
    # Don't fetch additional editions to avoid rate limiting and slow response
    # The primary ISBNs should provide enough cover options
    
    return covers


async def _fetch_json(session, url):
    async with session.get(url) as response:
        if response.status == requests.codes.ok:
            return await response.json()
    return None


def _build_cover_from_id(cover_id, isbn_hint=None):
    return {
        "url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg",
        "thumbnail_url": f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg",
        "isbn": isbn_hint or "N/A",
        "width": 0,
        "height": 0,
        "aspect_ratio": 0.667,
        "language": None,
        "is_original": False,
    }


def _dedupe_covers_by_url(covers):
    seen = set()
    deduped = []
    for c in covers:
        url = c.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(c)
    return deduped


async def get_reliable_covers_for_book(book_id, isbns=None, cap=16):
    """
    Deterministically fetch reliable alternate covers for an Open Library book.
    - Prefer editions with cover_i/covers via Works editions API.
    - Optionally backfill via Books API for given ISBNs.
    - Deduplicate and cap results.
    - Cache per work id for stability.
    """
    cache_key = f"ol_reliable_covers:{book_id}:{cap}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    editions_covers = []
    work_id = None
    async with aiohttp.ClientSession() as session:
        # Fetch book to resolve work id
        book_url = f"https://openlibrary.org/books/{book_id}.json"
        book_json = await _fetch_json(session, book_url)
        if book_json:
            works = book_json.get("works", [])
            if works:
                work_id = extract_openlibrary_id(works[0].get("key"))
            # Fallback to book id if no work
            if not work_id:
                work_id = extract_openlibrary_id(book_json.get("key", ""))

        if work_id:
            editions_url = f"https://openlibrary.org/works/{work_id}/editions.json?limit=500"
            editions_json = await _fetch_json(session, editions_url)
            if editions_json and isinstance(editions_json.get("entries"), list):
                for ed in editions_json["entries"]:
                    covers_list = ed.get("covers") or []
                    if not covers_list:
                        continue
                    cover_id = covers_list[0]
                    if not cover_id:
                        continue
                    editions_covers.append(_build_cover_from_id(cover_id))

        # Deduplicate and cap
        editions_covers = _dedupe_covers_by_url(editions_covers)[:cap]

        # Optional backfill via Books API if under cap and ISBNs provided
        remaining = max(0, cap - len(editions_covers))
        backfill_covers = []
        if remaining > 0 and isbns:
            # Clean and limit ISBNs
            clean_isbns = []
            seen_isbn = set()
            for raw in isbns:
                cleaned = raw.replace("-", "").replace(" ", "")
                if cleaned and cleaned not in seen_isbn:
                    seen_isbn.add(cleaned)
                    clean_isbns.append(cleaned)
            if clean_isbns:
                # Batch in chunks to avoid very long URLs
                chunk_size = 20
                for i in range(0, len(clean_isbns), chunk_size):
                    chunk = clean_isbns[i : i + chunk_size]
                    bibkeys = ",".join([f"ISBN:{x}" for x in chunk])
                    books_url = (
                        f"https://openlibrary.org/api/books?bibkeys={bibkeys}&format=json&jscmd=data"
                    )
                    data = await _fetch_json(session, books_url)
                    if not data:
                        continue
                    for key, val in data.items():
                        cover = val.get("cover")
                        if not cover:
                            continue
                        # Prefer large then medium
                        url_l = cover.get("large") or cover.get("medium") or cover.get("small")
                        if not url_l:
                            continue
                        thumb = cover.get("medium") or cover.get("small") or url_l
                        backfill_covers.append(
                            {
                                "url": url_l,
                                "thumbnail_url": thumb,
                                "isbn": key.replace("ISBN:", ""),
                                "width": 0,
                                "height": 0,
                                "aspect_ratio": 0.667,
                                "language": None,
                                "is_original": False,
                            }
                        )

        combined = _dedupe_covers_by_url(editions_covers + backfill_covers)[:cap]

    # Cache ~30 days
    cache.set(cache_key, combined, timeout=30 * 24 * 60 * 60)
    return combined


async def resolve_work_id_from_isbns(isbns):
    """Resolve an Open Library work id from a list of ISBNs.
    Tries the fast /isbn/{isbn}.json endpoint first.
    Returns the first work id found, or None.
    """
    if not isbns:
        return None
    async with aiohttp.ClientSession() as session:
        seen = set()
        for raw in isbns:
            cleaned = raw.replace("-", "").replace(" ", "")
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            book_url = f"https://openlibrary.org/isbn/{cleaned}.json"
            data = await _fetch_json(session, book_url)
            if not data:
                continue
            works = data.get("works") or []
            if works:
                return extract_openlibrary_id((works[0] or {}).get("key"))
    return None


async def get_reliable_covers_by_isbns(isbns, cap=20):
    """Fetch reliable covers using ISBNs by resolving to a work id, then
    retrieving editions with cover ids. Falls back to Books API covers.
    Caches by a normalized ISBN set fingerprint.
    """
    if not isbns:
        return []
    norm = sorted({x.replace("-", "").replace(" ", "") for x in isbns if x})
    cache_key = f"ol_reliable_covers:isbn:{','.join(norm)}:{cap}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    covers = []
    work_id = await resolve_work_id_from_isbns(norm)
    if work_id:
        # Reuse editions logic by fabricating a book id == work id for cache keying
        # Fetch editions directly against the work id
        async with aiohttp.ClientSession() as session:
            editions_url = f"https://openlibrary.org/works/{work_id}/editions.json?limit=500"
            editions_json = await _fetch_json(session, editions_url)
            if editions_json and isinstance(editions_json.get("entries"), list):
                for ed in editions_json["entries"]:
                    covers_list = ed.get("covers") or []
                    if not covers_list:
                        continue
                    cover_id = covers_list[0]
                    if not cover_id:
                        continue
                    covers.append(_build_cover_from_id(cover_id))

    covers = _dedupe_covers_by_url(covers)[:cap]

    # Backfill via Books API if needed
    remaining = max(0, cap - len(covers))
    if remaining > 0:
        async with aiohttp.ClientSession() as session:
            chunk_size = 20
            for i in range(0, len(norm), chunk_size):
                chunk = norm[i : i + chunk_size]
                bibkeys = ",".join([f"ISBN:{x}" for x in chunk])
                books_url = (
                    f"https://openlibrary.org/api/books?bibkeys={bibkeys}&format=json&jscmd=data"
                )
                data = await _fetch_json(session, books_url)
                if not data:
                    continue
                for key, val in data.items():
                    cover = val.get("cover")
                    if not cover:
                        continue
                    url_l = cover.get("large") or cover.get("medium") or cover.get("small")
                    if not url_l:
                        continue
                    thumb = cover.get("medium") or cover.get("small") or url_l
                    covers.append(
                        {
                            "url": url_l,
                            "thumbnail_url": thumb,
                            "isbn": key.replace("ISBN:", ""),
                            "width": 0,
                            "height": 0,
                            "aspect_ratio": 0.667,
                            "language": None,
                            "is_original": False,
                        }
                    )
                if len(covers) >= cap:
                    break

    covers = _dedupe_covers_by_url(covers)[:cap]
    cache.set(cache_key, covers, timeout=30 * 24 * 60 * 60)
    return covers
