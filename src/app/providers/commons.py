"""Direct Commons thumbnails and source-reported credits for Stage works."""

from html import unescape
from urllib.parse import urlsplit

import requests
from django.core.cache import cache
from django.utils import timezone
from django.utils.html import strip_tags

from app.models import Sources
from app.providers import services

BASE_URL = "https://commons.wikimedia.org/w/api.php"
SCHEMA = "stage-artwork-1"
IMAGE_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
SOURCE_HOSTS = {"commons.wikimedia.org"} | {
    f"{language}.wikipedia.org" for language in ("en", "fr", "de", "es", "it", "nl")
}


def safe_url(value, hosts):
    """Allow HTTPS URLs on expected hosts without embedded credentials."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if isinstance(hosts, str):
        hosts = {hosts}
    return parsed.scheme == "https" and parsed.netloc in hosts and not parsed.fragment


def credit_text(value):
    """Keep source text escaped by templates, never render provider HTML."""
    return unescape(strip_tags(value)).strip() if isinstance(value, str) else ""


def from_file(page, work_id, *, source_host, article_url=""):
    """Read a thumbnail and credits without interpreting its reuse conditions."""
    info = next(iter(page.get("imageinfo", [])), {})
    image = info.get("thumburl", "")
    source = info.get("descriptionurl", "")
    metadata = info.get("extmetadata", {})
    rights = {
        key: credit_text(value.get("value", ""))
        for key, value in metadata.items()
        if isinstance(value, dict)
    }
    if (
        not safe_url(image, IMAGE_HOSTS)
        or not safe_url(source, source_host)
        or info.get("mime") not in {"image/jpeg", "image/png", "image/webp"}
        or "badfile" in info
        or rights.get("DeletionReason")
    ):
        return {}
    license_url = rights.get("LicenseUrl", "")
    return {
        "schema": SCHEMA,
        "work_id": work_id,
        "image": image,
        "source_url": source,
        "article_url": article_url,
        "title": credit_text(page.get("title", "")).removeprefix("File:")
        or "Source image",
        "artist": rights.get("Artist", ""),
        "credit": rights.get("Credit", ""),
        "attribution": rights.get("Attribution", ""),
        "permission": rights.get("Permission", ""),
        "license": rights.get("LicenseShortName") or "Source information",
        "license_url": license_url
        if safe_url(license_url, SOURCE_HOSTS | {"creativecommons.org"})
        else source,
        "notices": " ".join(
            rights.get(key, "")
            for key in ("UsageTerms", "Restrictions", "LicenseNotices")
        ).strip(),
        "retrieved_at": timezone.now().isoformat(),
    }


def restored_artwork(artwork, work_id, image):
    """Accept only the current export shape; credits are not authenticated proof."""
    if not isinstance(artwork, dict) or artwork.get("schema") != SCHEMA:
        return {}
    if (
        not all(isinstance(value, str) for value in artwork.values())
        or artwork.get("work_id") != work_id
        or artwork.get("image") != image
        or not artwork.get("title", "").strip()
        or not artwork.get("license", "").strip()
        or not safe_url(image, IMAGE_HOSTS)
        or not safe_url(artwork.get("source_url"), SOURCE_HOSTS)
        or not safe_url(
            artwork.get("license_url"), SOURCE_HOSTS | {"creativecommons.org"}
        )
        or (
            artwork.get("article_url")
            and not safe_url(artwork["article_url"], SOURCE_HOSTS)
        )
    ):
        return {}
    return artwork.copy()


def cache_key(work_id):
    """Keep development cache entries outside the first-release format."""
    return f"commons_{SCHEMA}_{work_id}"


def artworks(works):
    """Fetch up to five direct filenames per work in batches of fifty, once."""
    result, pending = {}, {}
    for work in works:
        identifier = work.get("artwork_work_id", work["media_id"])
        filenames = list(dict.fromkeys(work.get("artwork_candidates", [])))[:5]
        signature = [work.get("work_revision"), filenames]
        cached = cache.get(cache_key(identifier))
        if cached is not None and cached.get("signature") == signature:
            result[identifier] = cached["artwork"]
        elif filenames:
            pending[identifier] = (signature, filenames)
        else:
            result[identifier] = {}
    filenames = list(
        dict.fromkeys(
            filename
            for _signature, candidates in pending.values()
            for filename in candidates
        )
    )
    files = {}
    for offset in range(0, len(filenames), 50):
        files.update(fetch_files(filenames[offset : offset + 50]))
    for identifier, (signature, candidates) in pending.items():
        selected = select_file(files, candidates, identifier)
        result[identifier] = selected
        if selected is not None:
            cache.set(
                cache_key(identifier),
                {"signature": signature, "artwork": selected},
                3600,
            )
    return result


def select_file(files, candidates, identifier):
    """Use the first usable direct candidate without ranking or extra requests."""
    unavailable = False
    for filename in candidates:
        page = files.get(filename)
        if page is None:
            unavailable = True
            continue
        try:
            selected = from_file(page, identifier, source_host="commons.wikimedia.org")
        except (TypeError, ValueError, AttributeError, IndexError):
            unavailable = True
            continue
        if selected:
            return selected
    return None if unavailable else {}


def fetch_files(filenames):
    """Resolve filename aliases from the same response; do not retry failed batches."""
    if cache.get("commons_retry_after"):
        return dict.fromkeys(filenames)
    try:
        response = services.api_request(
            Sources.WIKIDATA.value,
            "GET",
            BASE_URL,
            params={
                "action": "query",
                "format": "json",
                "redirects": 1,
                "titles": "|".join(f"File:{filename}" for filename in filenames),
                "prop": "imageinfo",
                "iiprop": "url|mime|extmetadata|badfile",
                "iilimit": 1,
                "iiurlwidth": 300,
                "iiextmetadatalanguage": "en",
            },
            headers={"User-Agent": "Yamtrack (https://github.com/FuzzyGrim/Yamtrack)"},
            timeout=8,
        )
        query = response["query"]
        pages = {page["title"]: page for page in query["pages"].values()}
        aliases = {
            entry["from"]: entry["to"]
            for field in ("normalized", "redirects")
            for entry in query.get(field, [])
        }
        result = {}
        for filename in filenames:
            title, seen = f"File:{filename}", set()
            while title in aliases and title not in seen:
                seen.add(title)
                title = aliases[title]
            result[filename] = None if title in seen else pages.get(title)
    except requests.RequestException as error:
        response = getattr(error, "response", None)
        if response is not None and response.status_code in {429, 503}:
            cache.set("commons_retry_after", "limited", 60)
        return dict.fromkeys(filenames)
    except (KeyError, TypeError, ValueError, AttributeError):
        return dict.fromkeys(filenames)
    else:
        return result


def require_available(metadata):
    """Keep explicit sync from overwriting saved images during an outage."""
    if metadata.get("artwork_unavailable"):
        raise services.ProviderAPIError(
            Sources.WIKIDATA.value,
            ValueError(),
            "Artwork provider unavailable. Please retry sync later",
        )
