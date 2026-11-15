"""Safe, instance-wide caching for approved provider artwork URLs."""

import base64
import binascii
import hashlib
import json
import logging
import tempfile
import time
from contextlib import suppress
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.signing import BadSignature, Signer
from django.http import FileResponse, HttpResponse, HttpResponseNotFound
from django.urls import reverse

from app import local_covers
from app.models.application_settings import ApplicationSettings

logger = logging.getLogger(__name__)

SETTING_CACHE_KEY = "application_settings:image_caching_enabled"
SETTING_CACHE_SECONDS = 300
CACHE_MAX_AGE_SECONDS = 86400
CACHE_STALE_AFTER_SECONDS = 30 * 24 * 60 * 60
MAX_IMAGE_BYTES = 10 * 1024 * 1024
REQUEST_TIMEOUT = (5, 15)
MAX_REDIRECTS = 3
HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300
BYTES_PER_UNIT = 1024
SIGNER_SALT = "floppy.image-cache"

# Exact host matching is intentional. Wildcards are limited to suffixes whose
# parent domain is itself a provider-owned image CDN.
APPROVED_IMAGE_HOSTS = frozenset(
    {
        "image.tmdb.org",
        "artworks.thetvdb.com",
        "cdn.myanimelist.net",
        "images.igdb.com",
        "covers.openlibrary.org",
        "assets.hardcover.app",
        "comicvine.gamespot.com",
        "cf.geekdo-images.com",
        "coverartarchive.org",
        "upload.wikimedia.org",
        "static.pocketcasts.com",
        "media.kitsu.app",
        "s4.anilist.co",
        "cdn.cloudflare.steamstatic.com",
        "cdn2.steamgriddb.com",
        "cdn.thegamesdb.net",
        "thumbnails.libretro.com",
        # YouTube thumbnails. img.youtube.com redirects to i.ytimg.com, and
        # each redirect hop is checked against this list.
        "i.ytimg.com",
        "img.youtube.com",
        "yt3.ggpht.com",
        "yt3.googleusercontent.com",
        # CLZ Comics covers: its catalogue, and the user's own uploads.
        "clzcomics.r.sizr.io",
        "comic-clzimages.s3.amazonaws.com",
    },
)
APPROVED_IMAGE_HOST_SUFFIXES = (
    ".mzstatic.com",
    ".comics.org",
    ".mangabaka.org",
    ".screenscraper.fr",
)


def cache_root():
    """Return the persistent derived-data directory for cached images."""
    return Path(settings.FLOPPY_DATA_DIR) / "image-cache"


# A page asks once per image card; a few seconds of per-process memo spares the
# shared cache a round trip each time, and a toggle reaches every worker within it.
_LOCAL_SETTING_SECONDS = 5
_local_setting = (0.0, False)


def _setting_value():
    """Read the toggle, creating the singleton lazily for old installs."""
    global _local_setting  # noqa: PLW0603
    expires_at, local = _local_setting
    now = time.monotonic()
    if now < expires_at:
        return local

    enabled = cache.get(SETTING_CACHE_KEY)
    if not isinstance(enabled, bool):
        setting, _ = ApplicationSettings.objects.get_or_create(pk=1)
        enabled = bool(setting.image_caching_enabled)
        cache.set(SETTING_CACHE_KEY, enabled, SETTING_CACHE_SECONDS)
    _local_setting = (now + _LOCAL_SETTING_SECONDS, enabled)
    return enabled


def _forget_local_setting():
    global _local_setting  # noqa: PLW0603
    _local_setting = (0.0, False)


def is_enabled():
    """Return whether new image proxy URLs should be generated."""
    return _setting_value()


def set_enabled(enabled):
    """Persist the instance-wide toggle and invalidate the shared setting cache."""
    setting, _ = ApplicationSettings.objects.get_or_create(pk=1)
    setting.image_caching_enabled = bool(enabled)
    setting.save(update_fields=["image_caching_enabled"])
    cache.set(SETTING_CACHE_KEY, bool(enabled), SETTING_CACHE_SECONDS)
    _forget_local_setting()
    return bool(enabled)


def invalidate_setting_cache():
    """Invalidate the cached toggle after external/database changes."""
    cache.delete(SETTING_CACHE_KEY)
    _forget_local_setting()


def _is_private_or_local_hostname(hostname):
    """Reject IP literals and names commonly used for local/private services."""
    if not hostname:
        return True
    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(
        (".localhost", ".local", ".internal", ".lan"),
    ):
        return True
    try:
        address = ip_address(lowered)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def is_approved_url(url):
    """Return whether a URL is safe to proxy as provider artwork."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    try:
        has_credentials_or_port = (
            parsed.username or parsed.password or parsed.port
        )
    except ValueError:
        return False
    if has_credentials_or_port:
        return False
    hostname = parsed.hostname
    if _is_private_or_local_hostname(hostname):
        return False
    hostname = hostname.lower().rstrip(".")
    return hostname in APPROVED_IMAGE_HOSTS or any(
        hostname.endswith(suffix) for suffix in APPROVED_IMAGE_HOST_SUFFIXES
    )


def _token_for_url(url):
    payload = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii")
    return Signer(salt=SIGNER_SALT).sign(payload)


def _url_for_token(token):
    try:
        payload = Signer(salt=SIGNER_SALT).unsign(token)
        return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
    except (BadSignature, binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _cache_key(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _paths(url):
    key = _cache_key(url)
    root = cache_root()
    return root / f"{key}.data", root / f"{key}.json"


def _requires_proxy(url):
    """Return whether an image can only be fetched with credentials Floppy holds.

    ScreenScraper media needs the developer credentials, which must never reach
    a browser, so these URLs go through the proxy whatever the caching toggle.
    """
    from app.game_artwork.screenscraper import is_screenscraper_url

    return is_screenscraper_url(url)


def rewrite_image_url(url, request=None):
    """Rewrite an approved image URL to Floppy when caching is enabled."""
    if local_covers.is_local(url):
        return request.build_absolute_uri(url) if request is not None else url
    if not is_approved_url(url) or not (is_enabled() or _requires_proxy(url)):
        return url
    path = reverse("image_cache", kwargs={"token": _token_for_url(url)})
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def rewrite_payload_images(payload, request=None):
    """Recursively rewrite image-like response fields without changing shape."""
    image_keys = {
        "image",
        "card_image",
        "image_url",
        "poster",
        "backdrop",
        "logo",
        "artwork",
    }
    if isinstance(payload, list):
        return [rewrite_payload_images(value, request=request) for value in payload]
    if isinstance(payload, tuple):
        return tuple(rewrite_payload_images(value, request=request) for value in payload)
    if isinstance(payload, dict):
        return {
            key: (
                rewrite_image_url(value, request=request)
                if key in image_keys and isinstance(value, str)
                else rewrite_payload_images(value, request=request)
            )
            for key, value in payload.items()
        }
    return payload


def _remove_temp(path):
    with suppress(FileNotFoundError):
        Path(path).unlink()


def _authenticated(url):
    """Attach provider credentials to the fetch URL, never to a stored one."""
    from app.game_artwork.screenscraper import authenticate

    return authenticate(url)


def _open_image_response(url):
    """Open an approved image URL, following approved redirects.

    Returns ``(response, content_type)`` for a usable image, or ``(None, None)``;
    the caller closes the response.
    """
    current_url = url
    response = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not is_approved_url(current_url):
                return None, None
            response = requests.get(
                _authenticated(current_url),
                headers={"Accept": "image/*"},
                timeout=REQUEST_TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                response = None
                if not location:
                    return None, None
                current_url = urljoin(current_url, location)
                continue
            break
        else:
            return None, None

        if response is None or not HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES:
            return None, None
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/") or content_type == "image/svg+xml":
            return None, None
        try:
            content_length = int(response.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length > MAX_IMAGE_BYTES:
            return None, None
    except BaseException:
        if response is not None:
            response.close()
        raise
    return response, content_type


def _fetch_to_disk(url):
    """Fetch one approved URL and atomically publish validated image files."""
    response = None
    temporary_data = None
    try:
        response, content_type = _open_image_response(url)
        if response is None:
            return False

        root = cache_root()
        root.mkdir(parents=True, exist_ok=True)
        data_path, metadata_path = _paths(url)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=root,
            prefix=f".{data_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as data_file:
            temporary_data = data_file.name
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return False
                data_file.write(chunk)

        # Imported here, not at module scope: Pillow is a C extension that
        # every process importing this module would otherwise carry resident,
        # while only this download path ever decodes an image.
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(temporary_data) as image:
                image.verify()
        except (OSError, UnidentifiedImageError):
            return False

        content_hash = hashlib.sha256(Path(temporary_data).read_bytes()).hexdigest()
        metadata = {"content_type": content_type, "etag": f'"{content_hash}"'}
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=f".{metadata_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as metadata_file:
            temporary_metadata = metadata_file.name
            json.dump(metadata, metadata_file, separators=(",", ":"))

        Path(temporary_data).replace(data_path)
        Path(temporary_metadata).replace(metadata_path)
        temporary_data = None
    except (OSError, requests.RequestException, ValueError):
        logger.debug("Unable to cache provider image %s", url, exc_info=True)
        return False
    else:
        return True
    finally:
        if response is not None:
            response.close()
        if temporary_data:
            _remove_temp(temporary_data)
        if "temporary_metadata" in locals():
            _remove_temp(temporary_metadata)


def _metadata(data_path, metadata_path):
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(metadata, dict):
        return None
    return metadata


def _touch(paths):
    for path in paths:
        try:
            path.touch()
        except OSError:
            continue


def cover_bytes(url, *, fetch=True):
    """Return the bytes of an approved provider image, or None.

    Server-side code that needs the pixels (the tier board export) uses this
    instead of the browser, because covers are not always same-origin. A copy
    already in the cache is used as is. Otherwise, with ``fetch=True``, the
    image is downloaded into memory only: nothing is written to the cache, so
    the instance's image caching setting keeps meaning what it says.
    """
    if local_covers.is_local(url):
        path = local_covers.path_for(url.removeprefix(local_covers.COVER_URL_PREFIX))
        with suppress(OSError):
            return path.read_bytes() if path else None
        return None
    if not is_approved_url(url):
        return None
    data_path, metadata_path = _paths(url)
    if data_path.is_file() and _metadata(data_path, metadata_path) is not None:
        _touch((data_path, metadata_path))
        with suppress(OSError):
            return data_path.read_bytes()
    if not fetch:
        return None
    try:
        response, _ = _open_image_response(url)
        if response is None:
            return None
        try:
            body = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                body.extend(chunk)
                if len(body) > MAX_IMAGE_BYTES:
                    return None
        finally:
            response.close()
    except (OSError, requests.RequestException, ValueError):
        logger.debug("Unable to fetch cover image %s", url, exc_info=True)
        return None
    return bytes(body)


def serve_cached_image(token, request):
    """Return a cached image, fetching it only when the feature is enabled."""
    url = _url_for_token(token)
    if not url or not is_approved_url(url):
        return HttpResponseNotFound()
    data_path, metadata_path = _paths(url)
    metadata = _metadata(data_path, metadata_path)
    if not data_path.is_file() or metadata is None:
        if not (is_enabled() or _requires_proxy(url)) or not _fetch_to_disk(url):
            return HttpResponseNotFound()
        metadata = _metadata(data_path, metadata_path)
        if metadata is None:
            return HttpResponseNotFound()

    _touch((data_path, metadata_path))
    etag = metadata.get("etag")
    if etag and request.headers.get("If-None-Match") == etag:
        response = HttpResponse(status=304)
    else:
        response = FileResponse(
            data_path.open("rb"),
            content_type=metadata.get("content_type", "application/octet-stream"),
        )
        response["Content-Length"] = str(data_path.stat().st_size)
    if etag:
        response["ETag"] = etag
    response["Cache-Control"] = f"public, max-age={CACHE_MAX_AGE_SECONDS}"
    response["X-Content-Type-Options"] = "nosniff"
    return response


# --- Stored covers ------------------------------------------------------------
#
# Audiobookshelf and Plex covers come from the user's own server through
# Floppy's proxy views. Keeping the last good copy here means a slow or
# offline server no longer turns every poster into the placeholder (#1307),
# and a fresh copy is served without calling the server at all. The files sit
# beside the provider cache, so the same stale cleanup and "Clear cache" cover
# them.
STORED_COVER_FRESH_SECONDS = 24 * 60 * 60


def _stored_paths(key):
    digest = hashlib.sha256(f"stored:{key}".encode()).hexdigest()
    root = cache_root()
    return root / f"{digest}.data", root / f"{digest}.json"


def load_stored_cover(key):
    """Return ``(body, content_type, is_fresh)`` for a stored cover, or None."""
    data_path, metadata_path = _stored_paths(key)
    metadata = _metadata(data_path, metadata_path)
    if metadata is None or not metadata.get("content_type"):
        return None
    try:
        body = data_path.read_bytes()
    except OSError:
        return None
    _touch((data_path, metadata_path))
    fetched_at = metadata.get("fetched_at") or 0
    is_fresh = time.time() - fetched_at < STORED_COVER_FRESH_SECONDS
    return body, metadata["content_type"], is_fresh


def store_cover(key, body, content_type):
    """Keep a validated cover on disk; failing to store it is never fatal."""
    data_path, metadata_path = _stored_paths(key)
    root = data_path.parent
    temporary_paths = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=root,
            prefix=f".{data_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as data_file:
            temporary_paths.append(data_file.name)
            data_file.write(body)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=f".{metadata_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as metadata_file:
            temporary_paths.append(metadata_file.name)
            json.dump(
                {"content_type": content_type, "fetched_at": time.time()},
                metadata_file,
                separators=(",", ":"),
            )
        Path(temporary_paths[0]).replace(data_path)
        Path(temporary_paths[1]).replace(metadata_path)
        temporary_paths = []
    except OSError:
        logger.debug("Unable to store cover %s", key, exc_info=True)
    finally:
        for path in temporary_paths:
            _remove_temp(path)


def stored_cover_response(stored):
    """Return an HTTP response serving a cover from ``load_stored_cover``."""
    body, content_type, _is_fresh = stored
    response = HttpResponse(body, content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def cache_stats():
    """Return the number of cached images and total bytes on disk."""
    root = cache_root()
    count = 0
    total_bytes = 0
    try:
        for path in root.iterdir():
            if path.is_file() and path.suffix in {".data", ".json"}:
                total_bytes += path.stat().st_size
                if path.suffix == ".data":
                    count += 1
    except OSError:
        pass
    return {"count": count, "bytes": total_bytes}


def clear_cache():
    """Delete all published cached images and metadata."""
    root = cache_root()
    removed_count = 0
    removed_bytes = 0
    try:
        paths = [
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix in {".data", ".json"}
        ]
    except OSError:
        return {"removed_count": 0, "removed_bytes": 0}
    for path in paths:
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        removed_bytes += size
        if path.suffix == ".data":
            removed_count += 1
    return {"removed_count": removed_count, "removed_bytes": removed_bytes}


def cleanup_stale_images():
    """Remove image entries that have not been accessed for thirty days."""
    root = cache_root()
    cutoff = __import__("time").time() - CACHE_STALE_AFTER_SECONDS
    removed_count = 0
    removed_bytes = 0
    try:
        data_paths = list(root.glob("*.data"))
    except OSError:
        return {"removed_count": 0, "removed_bytes": 0}
    for data_path in data_paths:
        try:
            if data_path.stat().st_mtime > cutoff:
                continue
            metadata_path = data_path.with_suffix(".json")
            paths = [data_path, metadata_path]
            entry_bytes = 0
            for path in paths:
                try:
                    entry_bytes += path.stat().st_size
                    path.unlink()
                except FileNotFoundError:
                    continue
            removed_count += 1
            removed_bytes += entry_bytes
        except OSError:
            continue
    return {"removed_count": removed_count, "removed_bytes": removed_bytes}


def format_bytes(value):
    """Format a disk byte count for the settings page."""
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < BYTES_PER_UNIT or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= BYTES_PER_UNIT
    return "0 B"
