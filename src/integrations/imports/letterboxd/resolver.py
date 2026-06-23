import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from django.utils import timezone
from requests_ratelimiter import LimiterSession

from app.models import MediaTypes
from app.providers import tmdb
from integrations.models import LetterboxdUriCache

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)
IMDB_RE = re.compile(r"tt\d+")
TMDB_RE = re.compile(r"/movie/(\d+)|themoviedb\.org/movie/(\d+)")


class LetterboxdResolver:
    """Resolve Letterboxd URLs to TMDB movie IDs."""

    def __init__(self, max_workers=10):
        self.max_workers = max_workers
        self.session = LimiterSession(per_second=5)
        self.session.headers.update({"User-Agent": USER_AGENT})

    def resolve_rows(self, rows):
        """Resolve all unique row URIs and return uri -> result."""
        uris = sorted({row.get("uri") for row in rows if row.get("uri")})
        return self.resolve_many(uris, rows)

    def resolve_many(self, uris, rows=()):
        """Resolve unique URIs through cache, scrape, IMDb fallback, then search."""
        cached = {
            cache.uri: {
                "tmdb_id": cache.tmdb_id,
                "media_type": cache.media_type,
                "imdb_id": cache.imdb_id,
                "confidence": cache.confidence,
            }
            for cache in LetterboxdUriCache.objects.filter(uri__in=uris)
        }
        by_uri = {row.get("uri"): row for row in rows if row.get("uri")}
        missing = [uri for uri in uris if uri not in cached]
        resolved = dict(cached)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.resolve_one, uri, by_uri.get(uri, {})): uri
                for uri in missing
            }
            for future in as_completed(futures):
                uri = futures[future]
                try:
                    result = future.result()
                except requests.RequestException as error:
                    logger.warning("Could not resolve Letterboxd URI %s: %s", uri, error)
                    result = None
                if result:
                    resolved[uri] = result
                    LetterboxdUriCache.objects.update_or_create(
                        uri=uri,
                        defaults={
                            "tmdb_id": str(result["tmdb_id"]),
                            "media_type": MediaTypes.MOVIE.value,
                            "imdb_id": result.get("imdb_id"),
                            "resolved_at": timezone.now(),
                            "confidence": result.get("confidence", ""),
                        },
                    )
        return resolved

    def resolve_one(self, uri, row=None):
        """Resolve one URI."""
        response = self.session.get(uri, allow_redirects=True, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Ported from kopiro/letterboxd-sync: prefer Letterboxd's TMDB link.
        tmdb_id = _tmdb_id_from_html(soup)
        if tmdb_id:
            return {"tmdb_id": str(tmdb_id), "confidence": "scrape"}

        imdb_id = _imdb_id_from_html(soup)
        if imdb_id:
            found = tmdb.find(imdb_id, "imdb_id")
            if found.get("movie_results"):
                return {
                    "tmdb_id": str(found["movie_results"][0]["id"]),
                    "imdb_id": imdb_id,
                    "confidence": "imdb",
                }

        if row:
            searched = _search_by_name_year(row)
            if searched:
                return searched
        return None


def _tmdb_id_from_html(soup):
    link = soup.select_one('a.micro-button[data-track-action="TMDB"]')
    candidates = []
    if link:
        candidates.extend([link.get("data-tmdb-id"), link.get("href")])
    data_node = soup.select_one("[data-tmdb-id]")
    if data_node:
        candidates.append(data_node.get("data-tmdb-id"))
    for candidate in candidates:
        if not candidate:
            continue
        if str(candidate).isdigit():
            return str(candidate)
        match = TMDB_RE.search(candidate)
        if match:
            return next(group for group in match.groups() if group)
    return None


def _imdb_id_from_html(soup):
    for candidate in soup.select("[href]"):
        match = IMDB_RE.search(candidate.get("href", ""))
        if match:
            return match.group(0)
    match = IMDB_RE.search(soup.get_text(" "))
    return match.group(0) if match else None


def _search_by_name_year(row):
    results = tmdb.search("movie", row.get("name", ""), page=1).get("results", [])
    if not results:
        return None
    year = row.get("year")
    for result in results:
        if year and str(result.get("release_date", ""))[:4] == str(year):
            return {"tmdb_id": str(result["media_id"]), "confidence": "search"}
    if not year:
        return {"tmdb_id": str(results[0]["media_id"]), "confidence": "search"}
    return None
