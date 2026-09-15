import difflib
import json
import logging
import re
import unicodedata
from html import unescape

import requests
from django.conf import settings
from django.core.cache import cache

from app.models import MediaTypes
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://www.rottentomatoes.com"

# rottentomatoes.com has no public API, the search and title pages are parsed.
# Default python-requests user agents get blocked.
headers = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
}

# Scores change slowly and each lookup costs two page loads
SCORES_CACHE_TIMEOUT = 60 * 60 * 24 * 7  # 7 days

# Release years from different sources can differ by one
MAX_YEAR_DIFFERENCE = 1

# Similarity given to a longer title that starts with the searched one,
# e.g. "Dune" and "Dune: Part Two", when the year does not rule it out
PREFIX_SIMILARITY = 0.85

# Search result sections on rottentomatoes.com
RT_TYPES = {
    MediaTypes.MOVIE.value: "movie",
    MediaTypes.TV.value: "tvSeries",
}

SEARCH_SECTION_RE = re.compile(
    r"<search-page-result\b(?P<attrs>[^>]*)>(?P<body>.*?)</search-page-result>",
    re.DOTALL,
)
SEARCH_ROW_RE = re.compile(
    r"<search-page-media-row\b(?P<attrs>[^>]*)>(?P<body>.*?)</search-page-media-row>",
    re.DOTALL,
)
SEARCH_TITLE_RE = re.compile(
    r'<a\s[^>]*href="(?P<url>[^"]+)"[^>]*slot="title"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
SCORECARD_RE = re.compile(
    r'<script[^>]*data-json="mediaScorecard"[^>]*>\s*(?P<json>\{.*?\})\s*</script>',
    re.DOTALL,
)


def media_scores(media_metadata):
    """Return the Rotten Tomatoes scores for a media details response.

    Only movies, TV shows and anime are looked up; anime is searched by its
    English title and as a movie or series depending on its format. The match
    is only "exact" when it equals the title displayed on the page, so the
    matched title is shown for anime known by its romaji title.
    """
    media_type = media_metadata.get("media_type")
    details = media_metadata.get("details", {})

    if media_type == MediaTypes.ANIME.value:
        titles = [media_metadata.get("title_english"), media_metadata.get("title")]
        rt_type = "movie" if details.get("format") == "Movie" else "tvSeries"
        date = details.get("start_date")
    elif media_type in RT_TYPES:
        titles = [media_metadata.get("title")]
        rt_type = RT_TYPES[media_type]
        date = details.get("release_date") or details.get("first_air_date")
    else:
        return {}

    titles = [title for title in titles if title]
    if not titles:
        return {}

    data = scores(titles, rt_type, get_year(date))
    if data:
        data = {
            **data,
            "exact": normalize_title(data["title"])
            == normalize_title(media_metadata.get("title")),
        }
    return data


def scores(titles, rt_type, year=None):
    """Return the Tomatometer and Popcornmeter for the best matching title.

    Args:
        titles: Title candidates, the first is used for the search and all are
            compared against the results
        rt_type: "movie" or "tvSeries"
        year: Release year to disambiguate results with the same title

    Returns an empty dict when disabled, nothing matches well enough, or the
    request fails, so the details page never depends on rottentomatoes.com.
    """
    if not settings.ROTTEN_TOMATOES:
        return {}

    cache_key = f"rt_scores_{rt_type}_{normalize_title(titles[0])}_{year}"
    data = cache.get(cache_key)

    if data is None:
        try:
            candidates = search(titles[0], rt_type)
            match = best_match(candidates, titles, year)
            data = title_scores(match) if match else {}
        except requests.exceptions.RequestException as error:
            # search results occasionally link to removed pages
            if not is_not_found(error):
                return request_failed(titles[0], error)
            logger.info("Rotten Tomatoes page for %s not found", titles[0])
            data = {}

        cache.set(cache_key, data, SCORES_CACHE_TIMEOUT)

    return data


def season_scores(tv_metadata, season_number):
    """Return the Tomatometer and Popcornmeter for a season of a TV show.

    The show is matched like any other title and the season page below it is
    used, so a non-exact show title carries over to the season.
    """
    # specials have no page on rottentomatoes.com
    if season_number < 1:
        return {}

    show = media_scores(tv_metadata)
    if not show:
        return {}

    cache_key = f"rt_season_scores_{show['url']}_{season_number}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{show['url']}/s{season_number:02d}"
        name = f"{show['title']} season {season_number}"

        try:
            page = page_scores(url)
        except requests.exceptions.RequestException as error:
            # a season not on rottentomatoes.com is a 404
            if not is_not_found(error):
                return request_failed(name, error)
            logger.info("Rotten Tomatoes page for %s not found", name)
            page = None

        data = (
            {
                "title": show["title"],
                "url": url,
                "year": None,
                "season": season_number,
                "exact": show["exact"],
                **page,
            }
            if page
            else {}
        )
        cache.set(cache_key, data, SCORES_CACHE_TIMEOUT)

    return data


def is_not_found(error):
    """Return whether a request error is a 404 response."""
    response = getattr(error, "response", None)
    return response is not None and response.status_code == requests.codes.not_found


def request_failed(name, error):
    """Log a failed request and return no scores, which are not cached."""
    # not caching means the next page view retries
    logger.warning("Rotten Tomatoes request failed for %s: %s", name, error)
    return {}


def search(query, rt_type):
    """Return the search results of the given type from rottentomatoes.com."""
    html = services.api_request(
        "rottentomatoes",
        "GET",
        f"{base_url}/search",
        params={"search": query},
        headers=headers,
        response_format="text",
    )

    results = []
    for section in SEARCH_SECTION_RE.finditer(html):
        if get_attribute(section["attrs"], "type") != rt_type:
            continue

        for row in SEARCH_ROW_RE.finditer(section["body"]):
            link = SEARCH_TITLE_RE.search(row["body"])
            if not link:
                continue

            year = get_attribute(row["attrs"], "release-year") or get_attribute(
                row["attrs"], "start-year"
            )
            results.append(
                {
                    "title": unescape(link["title"]).strip(),
                    "url": link["url"],
                    "year": year or None,
                },
            )

    return results


def best_match(candidates, titles, year):
    """Return the search result that best matches the titles, if good enough.

    Results released more than MAX_YEAR_DIFFERENCE years apart are discarded,
    the rest are ranked by title similarity and must reach
    RT_TITLE_MATCH_THRESHOLD to be accepted.
    """
    best = None
    best_rank = None

    for candidate in candidates:
        if (
            year
            and candidate["year"]
            and abs(int(year) - int(candidate["year"])) > MAX_YEAR_DIFFERENCE
        ):
            continue

        similarity = max(
            title_similarity(title, candidate["title"]) for title in titles
        )
        if similarity < settings.RT_TITLE_MATCH_THRESHOLD:
            continue

        # prefer the same year among equally similar titles
        rank = (similarity, candidate["year"] == year)
        if best_rank is None or rank > best_rank:
            best = {
                **candidate,
                "similarity": similarity,
                "exact": any(
                    normalize_title(title) == normalize_title(candidate["title"])
                    for title in titles
                ),
            }
            best_rank = rank

    if best:
        logger.debug(
            "Rotten Tomatoes matched %s to %s (%s) with similarity %.2f",
            titles[0],
            best["title"],
            best["year"],
            best["similarity"],
        )
    elif candidates:
        logger.info(
            "Rotten Tomatoes: no result matched %s (%s) well enough, best was %s",
            titles[0],
            year,
            candidates[0]["title"],
        )

    return best


def title_scores(match):
    """Return the scores from a matched search result's page."""
    page = page_scores(match["url"])
    if page is None:
        return {}

    return {
        "title": match["title"],
        "url": match["url"],
        "year": match["year"],
        "exact": match["exact"],
        **page,
    }


def page_scores(url):
    """Return the Tomatometer and Popcornmeter from a rottentomatoes.com page.

    Returns None when the page has no scorecard.
    """
    html = services.api_request(
        "rottentomatoes",
        "GET",
        url,
        headers=headers,
        response_format="text",
    )

    scorecard = SCORECARD_RE.search(html)
    if not scorecard:
        logger.warning("Rotten Tomatoes: no scorecard found on %s", url)
        return None

    try:
        scorecard = json.loads(scorecard["json"])
    except json.JSONDecodeError:
        logger.warning("Rotten Tomatoes: invalid scorecard on %s", url)
        return None

    critics = scorecard.get("criticsScore", {})
    audience = scorecard.get("audienceScore", {})
    if scorecard.get("hideAudienceScore"):
        audience = {}

    return {
        "tomatometer": get_score(critics, f"{critics.get('reviewCount', 0)} reviews"),
        "popcornmeter": get_score(audience, audience.get("bandedRatingCount")),
    }


def get_score(score, count):
    """Return a score from the scorecard in the format used by the templates."""
    value = str(score.get("score") or "").strip()
    if not value.isdigit():
        return None

    return {
        "value": value,
        "suffix": "%",
        "count": count,
        "certified": bool(score.get("certified")),
    }


def title_similarity(a, b):
    """Return the similarity between two titles as a number between 0 and 1."""
    a = normalize_title(a)
    b = normalize_title(b)

    if not a or not b:
        return 0
    if a == b:
        return 1

    ratio = difflib.SequenceMatcher(None, a, b).ratio()

    # accept a longer title with an extra qualifier, e.g. a subtitle
    shorter, longer = sorted((a, b), key=len)
    if longer.startswith(f"{shorter} "):
        ratio = max(ratio, PREFIX_SIMILARITY)

    return ratio


def normalize_title(title):
    """Return a title reduced to lowercase ASCII words for comparison."""
    title = unicodedata.normalize("NFKD", title or "")
    title = title.encode("ascii", "ignore").decode()
    title = title.lower().replace("&", " and ")
    title = re.sub(r"[^a-z0-9]+", " ", title).strip()
    return re.sub(r"^the ", "", title)


def get_attribute(attrs, name):
    """Return an attribute value from an HTML tag's attribute string.

    Movie rows use hyphenated names (release-year) and TV rows do not (startyear),
    so both spellings are accepted.
    """
    for spelling in (name, name.replace("-", "")):
        match = re.search(rf'\b{spelling}="([^"]*)"', attrs)
        if match:
            return match.group(1)
    return ""


def get_year(date):
    """Return the year from a date string like 2013-04-07, if any."""
    if date and len(date) >= 4 and date[:4].isdigit():  # noqa: PLR2004
        return date[:4]
    return None
