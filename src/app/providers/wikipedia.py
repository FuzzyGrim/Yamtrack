"""Exact-work Wikipedia article thumbnails with explicit source rights metadata."""

from time import monotonic
from urllib.parse import quote, unquote, urlsplit

import requests
from django.core.cache import cache
from django.utils import timezone

from app.providers import commons, services

VERSION = 2
LANGUAGES = ("en", "fr", "de", "es", "it", "nl")
FILE_NAMESPACES = {
    "en": "File",
    "fr": "Fichier",
    "de": "Datei",
    "es": "Archivo",
    "it": "File",
    "nl": "Bestand",
}
LANGUAGE_LIMIT = 3
REQUEST_LIMIT = 12
TIME_LIMIT = 20
THUMBNAIL_WIDTH = 300
FILE_NAMESPACE = 6


class UnavailableError(Exception):
    """Incomplete source metadata must remain retryable."""


def cache_key(work_id):
    """Identify the cached article selection for a canonical work."""
    return f"wikipedia_stage_artwork_v{VERSION}_subject1_{work_id}"


def invalidate(source, media_type, work_id):
    """Refresh article selection only for provider-backed Stage metadata sync."""
    if source == "wikidata" and media_type == "stage":
        cache.delete(cache_key(work_id))


def page_url(language, title):
    """Build source links from validated site identity and exact page titles."""
    encoded = quote(title.replace(" ", "_"), safe="")
    return f"https://{language}.wikipedia.org/wiki/{encoded}"


def request_pages(language, params, budget):
    """Bound optional Wikipedia calls and respect shared source back-pressure."""
    remaining = budget["deadline"] - monotonic()
    if (
        remaining <= 0
        or budget["calls"] >= REQUEST_LIMIT
        or cache.get("wikipedia_retry_after")
    ):
        raise UnavailableError
    budget["calls"] += 1
    try:
        data = services.api_request(
            "wikidata",
            "GET",
            f"https://{language}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "redirects": 1,
                **params,
            },
            headers={"User-Agent": "Yamtrack (https://github.com/FuzzyGrim/Yamtrack)"},
            timeout=min(8, remaining),
        )
    except requests.RequestException as error:
        response = getattr(error, "response", None)
        if response is not None and response.status_code in {429, 503}:
            value = response.headers.get("Retry-After", "60")
            cache.set(
                "wikipedia_retry_after",
                "limited",
                min(86400, max(5, int(value))) if value.isdigit() else 60,
            )
        raise UnavailableError from error
    if not isinstance(data, dict) or "error" in data:
        raise UnavailableError
    continuation = data.get("continue", {})
    if not isinstance(continuation, dict) or set(continuation) - {
        "iistart",
        "continue",
    }:
        raise UnavailableError
    query = data.get("query")
    if not isinstance(query, dict) or not isinstance(query.get("pages"), list):
        raise UnavailableError
    if not all(isinstance(page, dict) for page in query["pages"]):
        raise UnavailableError
    return query


def find_page(query, title):
    """Follow only normalization and redirects returned for the requested title."""
    for field in ("normalized", "redirects"):
        entries = query.get(field, [])
        if not isinstance(entries, list):
            raise UnavailableError
        for _step in range(len(entries) + 1):
            target = next(
                (
                    entry.get("to")
                    for entry in entries
                    if isinstance(entry, dict) and entry.get("from") == title
                ),
                title,
            )
            if target == title:
                break
            if not isinstance(target, str):
                raise UnavailableError
            title = target
    matches = [page for page in query["pages"] if page.get("title") == title]
    if len(matches) != 1:
        raise UnavailableError
    return matches[0]


def valid_file_source(url, language, file_page):
    """Match the resolved file title on its actual local or shared repository."""
    shared = file_page.get("imagerepository") == "shared"
    host = "commons.wikimedia.org" if shared else f"{language}.wikipedia.org"
    if not commons.safe_url(url, host):
        return False
    path = unquote(urlsplit(url).path).replace("_", " ")
    namespaces = {"File", "Image", "File" if shared else FILE_NAMESPACES[language]}
    filename = file_page["title"].split(":", 1)[1].replace("_", " ")
    return any(path == f"/wiki/{namespace}:{filename}" for namespace in namespaces)


def build_artwork(work_id, language, article, file_page):
    """Use the exact article's representative image and source-provided credits."""
    expected_host = (
        "commons.wikimedia.org"
        if file_page.get("imagerepository") == "shared"
        else f"{language}.wikipedia.org"
    )
    info = file_page["imageinfo"][0]
    if not valid_file_source(info.get("descriptionurl", ""), language, file_page):
        return {}
    return commons.from_file(
        file_page,
        work_id,
        source_host=expected_host,
        article_url=page_url(language, article["title"]),
    )


def checked_artwork(work_id, language, article, file_page):
    """Treat malformed nested API values as unavailable optional artwork."""
    try:
        if article["pageprops"]["wikibase_item"] != work_id or article["ns"] != 0:
            return {}
        if (
            "disambiguation" in article["pageprops"]
            or file_page["ns"] != FILE_NAMESPACE
        ):
            return {}
        if file_page.get("imagerepository") not in {"local", "shared"}:
            raise UnavailableError
        return build_artwork(work_id, language, article, file_page)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise UnavailableError from error


def site_artworks(language, entries, budget):
    """Resolve a page of exact articles and their files in two batched requests."""
    result = {}
    query = request_pages(
        language,
        {
            "titles": "|".join(title for _identifier, title in entries),
            "prop": "pageprops|pageimages|info",
            "ppprop": "wikibase_item|disambiguation",
            "piprop": "name",
            "pilicense": "any",
        },
        budget,
    )
    selected = {}
    for identifier, title in entries:
        try:
            article = find_page(query, title)
        except UnavailableError:
            result[identifier] = None
            continue
        if "missing" in article:
            result[identifier] = {}
            continue
        props = article.get("pageprops", {})
        if (
            not isinstance(props, dict)
            or not isinstance(props.get("wikibase_item"), str)
            or ("pageimage" in article and not isinstance(article["pageimage"], str))
        ):
            result[identifier] = None
            continue
        if (
            props.get("wikibase_item") != identifier
            or "disambiguation" in props
            or not article.get("pageimage")
        ):
            result[identifier] = {}
        else:
            selected[identifier] = article
    if not selected:
        return result
    try:
        files = request_pages(
            language,
            {
                "titles": "|".join(
                    dict.fromkeys(
                        "File:" + article["pageimage"] for article in selected.values()
                    )
                ),
                "prop": "imageinfo|info",
                "iiprop": "url|size|mime|sha1|extmetadata",
                "iiurlwidth": THUMBNAIL_WIDTH,
                "iilimit": 1,
                "iiextmetadatalanguage": "en",
            },
            budget,
        )
    except UnavailableError:
        return {**result, **dict.fromkeys(selected)}
    for identifier, article in selected.items():
        try:
            file_page = find_page(files, "File:" + article["pageimage"])
            result[identifier] = checked_artwork(
                identifier, language, article, file_page
            )
        except UnavailableError:
            result[identifier] = None
    return result


def artworks(works):
    """Prefer exact article imagery without hiding works during outages."""
    result = {}
    pending = {}
    budget = {"calls": 0, "deadline": monotonic() + TIME_LIMIT}
    for work in works:
        identifier = work["media_id"]
        links = work.get("wikipedia_sitelinks", {})
        signature = [work.get("work_revision"), links]
        cached = cache.get(cache_key(identifier))
        if cached is not None and cached.get("signature") == signature:
            result[identifier] = cached["artwork"]
        else:
            pending[identifier] = (
                signature,
                [
                    (language, links[language])
                    for language in LANGUAGES
                    if language in links
                ][:LANGUAGE_LIMIT],
            )
            result[identifier] = {}
    failures = set()
    for language in LANGUAGES:
        entries = [
            (identifier, title)
            for identifier, (_signature, links) in pending.items()
            for site, title in links
            if site == language and not result[identifier]
        ]
        if not entries:
            continue
        try:
            found = site_artworks(language, entries, budget)
            failures.update(
                identifier for identifier, value in found.items() if value is None
            )
            result.update(found)
        except (UnavailableError, TypeError, ValueError, AttributeError):
            failures.update(identifier for identifier, _title in entries)
    cache_results(result, pending, failures)
    return result


def cache_results(result, pending, failures):
    """Cache complete selections and mark incomplete per-work results retryable."""
    for identifier, (signature, _links) in pending.items():
        if result[identifier]:
            result[identifier]["retrieved_at"] = timezone.now().isoformat()
        if identifier in failures:
            result[identifier] = (
                {**result[identifier], "lookup_incomplete": "yes"}
                if result[identifier]
                else None
            )
        else:
            cache.set(
                cache_key(identifier),
                {"signature": signature, "artwork": result[identifier]},
                3600,
            )
