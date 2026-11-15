import base64
import contextlib
import hashlib
import json
import logging
import random
import secrets
import sqlite3
import time
from collections import OrderedDict, defaultdict

import requests
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError, OperationalError, connection
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_celery_beat.models import PeriodicTask
from simple_history.utils import bulk_update_with_history

import app
from app import fork_services_play_dedupe as play_dedupe
from app import helpers as app_helpers
from app.models import MediaTypes, Sources, Status
from app.providers import credentials, services, tmdb, tvdb
from app.services import grouped_anime, item_merge
from integrations import external_references, import_progress
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from integrations.imports.history_pages import HistoryPages
from integrations.matching import unique_title_match

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"
# Device-flow apps have no callback URL; Trakt still requires the field on
# the token grants, and expects this well-known out-of-band value.
TRAKT_OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
# Trakt returns 5; clamp anything unexpected before it drives a poll loop.
TRAKT_DEVICE_MIN_INTERVAL = 5
TRAKT_DEVICE_MAX_INTERVAL = 60
BULK_PAGE_SIZE = 1000
TRAKT_UNKNOWN_DATE = "1970-01-01T00:00:00.000Z"
# History is the long loop of an export import; a line every so many entries
# shows it moving without one INFO record per watch.
HISTORY_LOG_EVERY = 1000
METADATA_MEMO_SIZE = 8
METADATA_MEMO_MAX_BYTES = 128 * 1024
ITEM_MEMO_SIZE = 64
TRAKT_STAGES = ("history", "watchlist", "ratings", "notes", "comments", "collection")
MODE_STAGES = {"watchlist": ("watchlist",), "update_collection": ("collection",)}
SQLITE_WRITE_ATTEMPTS = 4


def _retry_sqlite_write(operation):
    """Retry a complete idempotent write after its transaction rolled back.

    Never replay a statement inside an enclosing transaction or the history
    entry itself: its Python staging buffers cannot be rolled back by SQLite.
    The connection's busy timeout still bounds each database attempt.
    """
    started = time.monotonic()
    for attempt in range(SQLITE_WRITE_ATTEMPTS):
        try:
            return operation()
        except OperationalError as error:
            cause = error.__cause__
            code = getattr(cause, "sqlite_errorcode", None)
            locked = (
                code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                if isinstance(code, int)
                else str(error).lower() in {
                    "database is locked", "database table is locked",
                }
            )
            if (
                connection.vendor != "sqlite"
                or connection.in_atomic_block
                or not locked
                or attempt == SQLITE_WRITE_ATTEMPTS - 1
                or time.monotonic() - started >= 1
            ):
                raise
            logger.warning("Trakt SQLite write contention; retrying transaction (%s/%s)",
                           attempt + 1, SQLITE_WRITE_ATTEMPTS - 1)
            time.sleep(random.uniform(0.025, 0.05) * 2**attempt)  # noqa: S311 -- retry jitter
    msg = "SQLite write attempts must be positive"
    raise ValueError(msg)


@contextlib.contextmanager
def _phase(name, username):
    """Log the start, duration and outcome of one stage of an import.

    Stages after the history loop write nothing per entry, so without these a
    long run looks identical to a stuck one in the logs.
    """
    started = time.monotonic()
    logger.info("Trakt import stage started: %s (user %s)", name, username)
    try:
        yield
    except BaseException:
        logger.warning(
            "Trakt import stage failed: %s after %.1fs (user %s)",
            name,
            time.monotonic() - started,
            username,
        )
        raise
    logger.info(
        "Trakt import stage finished: %s in %.1fs (user %s)",
        name,
        time.monotonic() - started,
        username,
    )

def _parse_watched_at(watched_at: str):
    if watched_at == TRAKT_UNKNOWN_DATE:
        return None
    return parse_datetime(watched_at)


def new_pkce_pair():
    """Return a fresh PKCE (S256) ``(code_verifier, code_challenge)`` pair.

    Trakt apps made without a client secret authorize with PKCE instead.
    """
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return code_verifier, code_challenge


def default_client_secret():
    """Return the client secret that belongs to the client ID now in use.

    A PKCE app has none. When the client ID is someone's own but the only
    secret on hand comes from another source (say the instance's), that secret
    belongs to a different app and would be rejected, so it is not used.
    """
    secret_source = credentials.source_of("trakt", "client_secret")
    if secret_source != credentials.source_of("trakt", "client_id"):
        return ""
    return credentials.get("trakt", "client_secret")


def handle_oauth_callback(
    request,
    redirect_uri=None,
    client_id=None,
    client_secret=None,
    code_verifier=None,
):
    """View for getting the Trakt OAuth2 token.

    An app without a client secret proves itself with the PKCE ``code_verifier``
    that matches the challenge sent when authorizing. Pass ``client_secret=""``
    for such an app so no other secret is substituted.
    """
    code = request.GET["code"]

    url = "https://api.trakt.tv/oauth/token"

    if not redirect_uri:
        redirect_uri = app_helpers.build_absolute_app_url(
            request,
            reverse("import_trakt_private"),
        )
    if not client_id:
        client_id = credentials.get("trakt", "client_id")
    if client_secret is None:
        client_secret = default_client_secret()

    params = {
        "client_id": client_id,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        params["client_secret"] = client_secret
    if code_verifier:
        params["code_verifier"] = code_verifier

    try:
        token_response = app.providers.services.api_request(
            "TRAKT",
            "POST",
            url,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    return {
        "access_token": token_response["access_token"],
        "refresh_token": token_response["refresh_token"],
        "redirect_uri": redirect_uri,
        "username": get_username_from_oauth(
            token_response["access_token"],
            client_id=client_id,
        ),
    }


def get_username_from_oauth(access_token, client_id=None):
    """View for getting the Trakt OAuth2 username."""
    url = "https://api.trakt.tv/users/me"

    if not client_id:
        client_id = credentials.get("trakt", "client_id")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Floppy/{settings.VERSION}",
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
        "Authorization": f"Bearer {access_token}",
    }

    try:
        request = app.providers.services.api_request(
            "TRAKT",
            "GET",
            url,
            headers=headers,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    return request["username"]


def _refresh_redirect_uri():
    """Return the redirect URI to send with a refresh grant.

    Falls back to the out-of-band value when this instance has no callback URL
    Trakt would accept - either because nothing is configured (no request in a
    Celery worker) or because it is plain HTTP on a non-loopback host, which is
    also how the connection was made in the first place.
    """
    # Runs in a Celery worker, whose ROOT_URLCONF is empty and whose
    # INSTALLED_APPS lack allauth, so neither the default urlconf nor
    # ``config.urls`` can be resolved there. ``integrations.urls`` holds the
    # route, mounted at the root, and imports fine in every process.
    redirect_uri = app_helpers.build_absolute_app_url(
        None,
        reverse("import_trakt_private", urlconf="integrations.urls"),
    )
    if not app_helpers.supports_oauth_redirect(redirect_uri):
        return TRAKT_OOB_REDIRECT_URI
    return redirect_uri


def request_device_code(client_id=None):
    """Start Trakt's device authorization flow and return the new codes."""
    if not client_id:
        client_id = credentials.get("trakt", "client_id")

    try:
        response = app.providers.services.api_request(
            "TRAKT",
            "POST",
            f"{TRAKT_API_BASE_URL}/oauth/device/code",
            params={"client_id": client_id},
        )
    except (services.ProviderAPIError, requests.RequestException) as error:
        logger.warning("Trakt device code request failed: %s", error)
        msg = (
            "Could not start Trakt authorization. "
            "Check your Trakt client ID (and client secret, if your app has one)."
        )
        raise MediaImportError(msg) from error

    interval = response.get("interval") or TRAKT_DEVICE_MIN_INTERVAL
    response["interval"] = min(
        max(int(interval), TRAKT_DEVICE_MIN_INTERVAL),
        TRAKT_DEVICE_MAX_INTERVAL,
    )
    return response


def poll_device_token(device_code, client_id=None, client_secret=None):
    """Poll Trakt once for a device authorization result.

    Returns the same shape as `handle_oauth_callback` on success, or None while
    the user has not finished authorizing yet. Terminal outcomes raise.

    This deliberately bypasses `services.api_request`: that helper raises on
    every non-2xx and sleeps in-thread on 429/5xx, but here the status code is
    the protocol - 400 means "keep waiting" and 418 means "denied" - and the
    caller is a single HTMX poll that must answer promptly.
    """
    if not client_id:
        client_id = credentials.get("trakt", "client_id")
    if client_secret is None:
        client_secret = default_client_secret()

    try:
        response = services.resilient_request(
            "POST",
            f"{TRAKT_API_BASE_URL}/oauth/device/token",
            json={
                "code": device_code,
                "client_id": client_id,
                **({"client_secret": client_secret} if client_secret else {}),
            },
            timeout=settings.REQUEST_TIMEOUT,
        )
    except requests.RequestException as error:
        logger.warning("Trakt device token poll failed: %s", error)
        msg = "Could not reach Trakt. Try again."
        raise MediaImportError(msg) from error

    status_code = response.status_code

    if status_code == requests.codes.ok:
        payload = response.json()
        access_token = payload["access_token"]
        return {
            "access_token": access_token,
            "refresh_token": payload["refresh_token"],
            "redirect_uri": TRAKT_OOB_REDIRECT_URI,
            "username": get_username_from_oauth(access_token, client_id=client_id),
        }

    # Pending, or polling faster than Trakt likes. The caller already waits the
    # interval Trakt handed out, so both simply mean "not yet".
    if status_code == requests.codes.bad_request:
        return None
    if status_code == requests.codes.too_many_requests:
        logger.warning("Trakt asked us to slow down device token polling")
        return None

    terminal_errors = {
        requests.codes.not_found: (
            "This Trakt authorization request is no longer valid. Start again."
        ),
        requests.codes.conflict: "This Trakt authorization code was already used.",
        requests.codes.gone: "The Trakt authorization code expired. Start again.",
        requests.codes.im_a_teapot: "Trakt authorization was denied.",
    }
    msg = terminal_errors.get(status_code)
    if msg is None:
        logger.warning("Trakt device token poll returned status %s", status_code)
        msg = "Trakt authorization failed."
    raise MediaImportError(msg)


def get_access_token(encrypted_refresh_token, redirect_uri=None):
    """Get access token from encrypted refresh token.

    Trakt checks the redirect URI on a refresh, so send the one the connection
    was authorized with. Connections saved before it was stored (``None``) fall
    back to the best guess from this instance's configuration.
    """
    url = "https://api.trakt.tv/oauth/token"

    decrypted_token = helpers.decrypt_or_raise(encrypted_refresh_token)

    params = {
        "client_id": credentials.get("trakt", "client_id"),
        "refresh_token": decrypted_token,
        "grant_type": "refresh_token",
        "redirect_uri": redirect_uri or _refresh_redirect_uri(),
    }
    # Apps made without a client secret (PKCE) refresh with the client ID alone.
    if client_secret := default_client_secret():
        params["client_secret"] = client_secret

    try:
        request = app.providers.services.api_request(
            "TRAKT",
            "POST",
            url,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        if error.status_code == requests.codes.bad_request:
            raise MediaImportError(_refresh_rejected_message(error)) from error
        raise

    # refresh tokens are one time use only
    update_refresh_token(encrypted_refresh_token, request["refresh_token"])
    return request["access_token"]


def _refresh_rejected_message(error):
    """Explain a 400 from the token endpoint, naming the OAuth error Trakt sent."""
    try:
        payload = error.response.json()
        reason = payload["error"] if isinstance(payload, dict) else None
    except (AttributeError, KeyError, ValueError):
        reason = None
    if not isinstance(reason, str):
        reason = None
    logger.warning("Trakt token refresh rejected (error=%s)", reason)
    detail = f" ({reason})" if reason else ""
    return (
        f"Trakt rejected the token refresh{detail}. Check that the Redirect URI "
        "on your Trakt app matches the one Floppy shows, then reconnect Trakt."
    )


def update_refresh_token(old_token, new_token):
    """Update the refresh token in periodic tasks."""
    periodic_task = PeriodicTask.objects.filter(
        task="Import from Trakt",
        kwargs__contains=f'"token": "{old_token}"',
    ).first()

    if periodic_task:
        task_kwargs = json.loads(periodic_task.kwargs)
        task_kwargs["token"] = helpers.encrypt(new_token)
        periodic_task.kwargs = json.dumps(task_kwargs)
        periodic_task.save()


def importer(token, user, mode, username, redirect_uri=None):
    """Import the user's data from Trakt.

    Can import using either OAuth (token provided) or public username.
    When using OAuth, username should be the authenticated user's username.
    When using public import, username is the Trakt username and token should be None.

    Args:
        token (str, optional): Encrypted OAuth2 refresh token if using OAuth else None
        user: Django user object to import data for
        mode (str): Import mode ("new" or "overwrite")
        username (str): Trakt username to import from
        redirect_uri (str, optional): Redirect URI the connection was authorized
            with, sent again when the refresh token is exchanged
    """
    trakt_importer = TraktImporter(
        username,
        user,
        mode,
        refresh_token=token,
        redirect_uri=redirect_uri,
    )
    return trakt_importer.import_data()


class TraktMetadataResolverMixin:
    """Shared TMDB metadata/Item resolution logic for Trakt-sourced importers.

    Requires the including class to maintain a ``self.warnings`` list.
    """

    # Entries kept per import. File exports replay a whole library in date
    # order, so shows and seasons interleave and a tiny memo refetches them.
    metadata_memo_size = METADATA_MEMO_SIZE

    def _get_tmdb_id(self, entry_data, media_type):
        """Extract TMDB ID from entry data, falling back to a title search."""
        # A fresh source decision for each entry observes corrections made
        # while this import runs, even when callers reuse their input dict.
        self._last_external_reference = None
        reference = self._get_trakt_reference(entry_data, media_type)
        self._last_external_reference = (entry_data, media_type, reference)
        if reference and reference.review_status == external_references.ExternalReferenceReviewStatus.IGNORED.value:
            return None
        target = external_references.reference_target(reference)
        target_type = (
            MediaTypes.TV.value
            if media_type == MediaTypes.SEASON.value
            else media_type
        )
        if target and target.media_type == target_type:
            return str(target.media_id)
        if (
            "ids" in entry_data
            and "tmdb" in entry_data["ids"]
            and entry_data["ids"]["tmdb"]
        ):
            return str(entry_data["ids"]["tmdb"])

        fallback_id = self._search_tmdb_id_by_title(media_type, entry_data)
        if fallback_id:
            return fallback_id

        self.warnings.append(
            f"{entry_data['title']}: No {Sources.TMDB.label} ID found.",
        )
        identity = external_references.trakt_identity(entry_data)
        if identity and getattr(self, "external_reference_integration", None):
            _retry_sqlite_write(lambda: external_references.save_observation(
                self.user,
                "trakt",
                self._trakt_reference_source_account(),
                *identity,
                media_type,
                metadata=entry_data,
                needs_review=True,
            ))
        return None

    def _trakt_reference_source_account(self):
        """Return the stable source-account scope for this Trakt import."""
        return str(getattr(self, "username", "")).strip().casefold()

    def _get_trakt_reference(self, entry_data, media_type):
        """Look up a saved Trakt decision when this importer supports it."""
        previous = getattr(self, "_last_external_reference", None)
        if previous and previous[0] is entry_data and previous[1] == media_type:
            return previous[2]
        if not getattr(self, "external_reference_integration", None):
            return None
        identity = external_references.trakt_identity(entry_data)
        if not identity:
            return None
        reference = external_references.lookup_reference(
            self.user,
            "trakt",
            self._trakt_reference_source_account(),
            *identity,
            media_type,
        )
        if reference is None and media_type == MediaTypes.SEASON.value:
            reference = external_references.lookup_reference(
                self.user,
                "trakt",
                self._trakt_reference_source_account(),
                *identity,
                MediaTypes.TV.value,
                include_show_for_episode=False,
            )
        return reference

    def _remember_trakt_reference(self, entry_data, media_type, item):
        """Record the source identity after its destination Item exists."""
        if not getattr(self, "external_reference_integration", None):
            return
        identity = external_references.trakt_identity(entry_data)
        if identity:
            _retry_sqlite_write(lambda: external_references.save_observation(
                self.user,
                "trakt",
                self._trakt_reference_source_account(),
                *identity,
                media_type,
                matched_item=item,
                metadata=entry_data,
            ))

    def _search_tmdb_id_by_title(self, media_type, entry_data):
        """Best-effort TMDB title search when Trakt has no tmdb id (#965).

        Trakt's own id cross-reference for a show/movie can be missing even
        though the title is findable on TMDB directly.
        """
        title = entry_data.get("title")
        if not title:
            return None

        # Every episode of one show repeats this search; ask TMDB once.
        memo_key = (media_type, title, entry_data.get("year"))
        if not hasattr(self, "_title_search_memo"):
            self._title_search_memo = {}
        if memo_key not in self._title_search_memo:
            self._title_search_memo[memo_key] = self._title_search_uncached(
                media_type,
                title,
                entry_data,
            )
        return self._title_search_memo[memo_key]

    def _title_search_uncached(self, media_type, title, entry_data):
        """Run the TMDB title search behind ``_search_tmdb_id_by_title``."""
        try:
            results = services.search(
                media_type,
                title,
                1,
                source=Sources.TMDB.value,
            ).get("results", [])
        except services.ProviderAPIError:
            return None

        result = unique_title_match(
            results,
            title,
            year=entry_data.get("year"),
        )
        return str(result.get("media_id") or result.get("id")) if result else None

    def _get_metadata(self, media_type, tmdb_id, title, season_number=None):
        """Resolve an import snapshot without repeating warm or absent lookups."""
        coordinate = season_number
        if isinstance(coordinate, str):
            with contextlib.suppress(ValueError):
                coordinate = int(coordinate)
        key = (media_type, str(tmdb_id), coordinate, settings.TMDB_LANG)
        if not hasattr(self, "_metadata_memo"):
            self._metadata_memo = OrderedDict()
            self._missing_metadata = set()
        if key in self._missing_metadata:
            return None
        if key in self._metadata_memo:
            self._metadata_memo.move_to_end(key)
            return self._metadata_memo[key]
        try:
            kwargs = {}
            if season_number is not None:
                kwargs["season_numbers"] = [season_number]

            metadata = services.get_media_metadata(
                media_type,
                tmdb_id,
                Sources.TMDB.value,
                **kwargs,
            )
        except services.ProviderAPIError as error:
            if error.status_code == requests.codes.not_found:
                if getattr(error, "confirmed_absent", False):
                    # One key/warning per absent coordinate for this import,
                    # even if Redis is unavailable or its short TTL expires.
                    self._missing_metadata.add(key)
                if media_type == MediaTypes.SEASON.value:
                    title = f"{title} S{season_number}"
                self.warnings.append(
                    f"{title}: not found in {Sources.TMDB.label} with ID {tmdb_id}.",
                )
                return None
            if error.status_code == requests.codes.unauthorized:
                msg = f"Invalid {Sources.TMDB.label} API key."
                raise MediaImportError(msg) from error
            raise
        if metadata is None:
            return None
        # Keep at most eight small DTOs. Very large show/season payloads
        # must not turn an import-local memo into a second history buffer.
        encoded_size = 0
        try:
            for chunk in DjangoJSONEncoder().iterencode(metadata):
                encoded_size += len(chunk.encode("utf-8"))
                if encoded_size > METADATA_MEMO_MAX_BYTES:
                    break
            else:
                self._metadata_memo[key] = metadata
                if len(self._metadata_memo) > self.metadata_memo_size:
                    self._metadata_memo.popitem(last=False)
        except (TypeError, ValueError):
            pass  # Non-JSON metadata remains usable, just not memoized.
        return metadata

    def _anime_bucket_for_show(self, tmdb_id, tv_metadata):
        """Return the library bucket for a show, or None to leave it in TV.

        Returns the sentinel string "skip" when the show's Anime home is a flat
        MAL row: this importer only resolves TMDB identities and cannot write to
        one, so importing it as TV would track the same show in both libraries.
        """
        route = self.anime_router.route_for_show(tv_metadata, tmdb_id=tmdb_id)
        if route == "grouped":
            return MediaTypes.ANIME.value
        if route == "flat":
            return "skip"
        return None

    def _get_or_create_item(
        self,
        media_type,
        tmdb_id,
        metadata,
        season_number=None,
        episode_number=None,
        library_media_type=None,
    ):
        """Get or create an item in the database.

        The same season/episode can legitimately exist under more than one
        ``library_media_type`` bucket (e.g. grouped anime stored on TV rows, or
        episodes auto-created when a season is marked complete). A plain
        ``get_or_create`` keyed only on the identity fields therefore risks
        ``MultipleObjectsReturned``. Reuse any existing matching item instead of
        failing or creating a divergent duplicate, preferring the bucket this
        importer would normally use.
        """
        item_kwargs = {
            "media_id": tmdb_id,
            "source": Sources.TMDB.value,
            "media_type": media_type,
        }

        if season_number is not None:
            item_kwargs["season_number"] = season_number

        if episode_number is not None:
            item_kwargs["episode_number"] = episode_number

        # Trakt resolves everything through TMDB, whose metadata never carries a
        # bucket, so the caller passes the show's anime route down to the season
        # and episode rows.
        desired_bucket = (
            library_media_type
            or metadata.get("library_media_type")
            or media_type
        )
        memo_key = (
            media_type, str(tmdb_id), season_number, episode_number,
            desired_bucket, getattr(self.user, "tv_metadata_source_default", ""),
        )
        if not hasattr(self, "_item_memo"):
            self._item_memo = OrderedDict()
        if memo_key in self._item_memo:
            self._item_memo.move_to_end(memo_key)
            return self._item_memo[memo_key]

        existing = list(app.models.Item.objects.filter(**item_kwargs))
        if existing:
            item = next(
                (item for item in existing if item.library_media_type == desired_bucket),
                existing[0],
            )
        else:
            item = self._find_preferred_provider_item(
                media_type, tmdb_id, season_number, desired_bucket,
            )
            if item is None:
                def create_item():
                    # get_or_create rolls back creation and signal writes on
                    # failure, and observes another importer's committed Item.
                    return app.models.Item.objects.get_or_create(
                        **item_kwargs,
                        library_media_type=desired_bucket,
                        defaults={
                            **app.models.Item.title_fields_from_metadata(metadata),
                            "image": metadata["image"],
                        },
                    )[0]
                item = _retry_sqlite_write(create_item)
        # Play deduplication adopts the first known runtime. Keep rereading an
        # unknown movie/episode so independent enrichment can still supply it.
        if (
            media_type not in (MediaTypes.MOVIE.value, MediaTypes.EPISODE.value)
            or getattr(item, "runtime_minutes", None)
        ):
            self._item_memo[memo_key] = item
            if len(self._item_memo) > ITEM_MEMO_SIZE:
                self._item_memo.popitem(last=False)
        return item

    def _adopt_provider_tv(self, tmdb_id, title):
        """Treat a show the user tracks on its TVDB Item as already existing.

        ``existing_media`` is keyed by each row's own provider, so a
        TVDB-preferring user's show looked untracked by its TMDB id. It was
        queued again on the same TVDB Item (see ``_find_preferred_provider_item``)
        and the save failed on the one-row-per-user-and-item constraint.
        """
        shows = self.existing_media[MediaTypes.TV.value][Sources.TMDB.value]
        if (
            tmdb_id in shows
            or tmdb_id in self.provider_tv_checked
            or getattr(self.user, "tv_metadata_source_default", "")
            != Sources.TVDB.value
            or not tvdb.enabled()
        ):
            return
        self.provider_tv_checked.add(tmdb_id)

        # The TVDB id comes from cached TMDB metadata, so load it first.
        if self._get_metadata(MediaTypes.TV.value, tmdb_id, title) is None:
            return
        tvdb_id = tmdb.resolve_tvdb_id_for_tmdb_show(tmdb_id)
        if not tvdb_id:
            return
        tv_obj = (
            app.models.TV.objects.filter(
                user=self.user,
                item__source=Sources.TVDB.value,
                item__media_id=str(tvdb_id),
            )
            .select_related("item")
            .first()
        )
        if tv_obj is None:
            return

        shows[tmdb_id] = tv_obj
        if self.mode == "overwrite":
            self.to_delete[MediaTypes.TV.value][Sources.TMDB.value].add(tmdb_id)
            self.to_delete[MediaTypes.TV.value][Sources.TVDB.value].add(str(tvdb_id))

    def _find_preferred_provider_item(
        self,
        media_type,
        tmdb_id,
        season_number,
        desired_bucket,
    ):
        """Reuse an existing TVDB item instead of creating a duplicate TMDB one.

        This importer only ever resolves shows/seasons via TMDB, so a
        TVDB-preferring user who already tracks a show gets a second,
        independent ``Item`` tree for it on every import unless we look for
        their existing TVDB item first (#620). Scoped to users who actually
        prefer TVDB, since the lookup costs a TMDB->TVDB id resolution call.
        """
        if getattr(self.user, "tv_metadata_source_default", "") != Sources.TVDB.value:
            return None
        if not tvdb.enabled():
            return None

        return item_merge.find_tvdb_counterpart(
            tmdb_id,
            media_type,
            season_number=season_number,
            library_media_type=desired_bucket,
        )

    def _get_episode_image(self, episode_number, season_metadata):
        """Extract episode image URL from season metadata."""
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == episode_number:
                if episode.get("still_path"):
                    return f"https://image.tmdb.org/t/p/w500{episode['still_path']}"
                break
        return settings.IMG_NONE


class TraktImporter(TraktMetadataResolverMixin):
    """Class to handle importing user data from Trakt."""

    def __init__(self, username, user, mode, refresh_token=None, redirect_uri=None):
        """Initialize the importer with user details and mode.

        Args:
            username (str): Trakt username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
            refresh_token (str, optional): Encrypted OAuth2 refresh token if
                using OAuth, None for public import
            redirect_uri (str, optional): Redirect URI used when authorizing
        """
        self.username = username
        self.user = user
        self.external_reference_integration = "trakt"
        # Watchlist only and Collection only run a single stage; the watchlist
        # stage then behaves like "new" so it never touches an existing entry.
        self.full_import = mode not in MODE_STAGES
        self.stages = MODE_STAGES.get(mode, TRAKT_STAGES)
        self.mode = "new" if mode == "watchlist" else mode
        self.refresh_token = refresh_token
        self.redirect_uri = redirect_uri
        self.is_oauth_import = bool(refresh_token)
        user_identifier = "me" if self.is_oauth_import else username
        self.user_base_url = f"{TRAKT_API_BASE_URL}/users/{user_identifier}"
        self.warnings = []

        # One anime router for the whole run. Rows are buffered and flushed at
        # the end, so a database lookup alone cannot see a home opened earlier
        # in this same import.
        self.anime_router = grouped_anime.AnimeRouteResolver(user)
        self.skipped_flat_anime = set()

        # Track existing media to handle "new" mode correctly
        self.existing_media = helpers.get_existing_media(user)
        self.existing_children = helpers.get_existing_children(user)

        # Track media the user explicitly deleted, so it isn't recreated
        self.deleted_media = helpers.get_deleted_media(user)

        # Track previously imported episode plays so rerunning the same sync
        # does not create duplicate episode history rows.
        self.existing_episode_watch_keys = self._get_existing_episode_watch_keys()

        # Track existing play timestamps so a play already recorded (e.g. via
        # Plex webhook or a Plex history import) isn't duplicated by a nearby
        # Trakt-imported play for the same item. See fork_services_play_dedupe.
        self.existing_episode_play_times = play_dedupe.existing_episode_play_times(
            user,
            source=Sources.TMDB.value,
        )
        self.existing_movie_play_times = play_dedupe.existing_movie_play_times(
            user,
            source=Sources.TMDB.value,
        )
        if mode == "overwrite":
            # Overwrite deletes a show or movie and rebuilds it from Trakt, so
            # a stored play is about to go with its row. Skipping the Trakt
            # entry as a duplicate would lose that play for good.
            self.existing_episode_play_times = play_dedupe.PlayTimes()
            self.existing_movie_play_times = play_dedupe.PlayTimes()

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # TMDB ids already checked for a show tracked on a TVDB Item.
        self.provider_tv_checked = set()

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Track media instances being created
        self.media_instances = defaultdict(lambda: defaultdict(list))

        # Track existing DB objects promoted to COMPLETED during import
        self.completed_seasons = []
        self.completed_tvs = []

        # Track TMDB IDs the user has dropped on Trakt (hidden/progress_watched)
        self.dropped_tmdb_ids: set = set()
        self.dropped_tvs: list = []

        # Track TV/Season rows newly created in this run, so completion/dropped
        # status derived from Trakt history is only applied to rows Floppy is
        # seeing for the first time (or on an explicit overwrite re-sync),
        # never silently onto a show/season the user is already tracking
        # locally with a status they set themselves.
        self.tv_created_this_run: set = set()
        self.season_created_this_run: set = set()

        # watched_at of the history entry that triggered a full-show
        # completion, keyed by tv_key. Used so the episode/season fan-out
        # in TV._completed() dates fabricated rows from that real watch
        # event instead of falling back to "now".
        self.tv_completion_dates: dict = {}

        logger.info(
            "Initialized Trakt importer for user %s with mode %s",
            username,
            mode,
        )

    def _get_existing_episode_watch_keys(self):
        """Return exact episode play keys already stored for this user."""
        if self.mode != "new":
            return set()

        return set(
            app.models.Episode.objects.filter(
                related_season__user=self.user,
                # Scoped to the source this importer resolves against: an
                # episode tracked from another provider can share a media_id
                # with a TMDB one, and matching it would skip a real play.
                item__source=Sources.TMDB.value,
                end_date__isnull=False,
            ).values_list(
                "item__media_id",
                "item__season_number",
                "item__episode_number",
                "end_date",
            ),
        )

    def _raise_for_user_error(self, error):
        """Translate a Trakt HTTP error about the user into a MediaImportError."""
        if error.response.status_code == requests.codes.not_found:
            msg = (
                f"User slug {self.username} not found. "
                "User slug can be found in your Trakt profile URL."
            )
            raise MediaImportError(msg) from error

        if error.response.status_code == requests.codes.unauthorized:
            msg = "This account is set to private, use OAuth import instead."
            raise MediaImportError(msg) from error

    def _validate_username(self):
        """Fail fast on a bad slug.

        Trakt answers /history and /watchlist for an unknown user with an empty
        200, so without this check the import runs to completion looking like a
        no-op, or dies minutes later on the first endpoint that does 404.
        """
        try:
            self._make_api_request(self.user_base_url)
        except requests.exceptions.HTTPError as error:
            self._raise_for_user_error(error)
            raise

    def import_data(self):
        """Import all user data from Trakt."""
        # File exports share this parser, but retain their existing persistence
        # contract. Durable recovery here is scoped to the Trakt API importer.
        if type(self) is TraktImporter:
            from integrations.imports.durable import run_import

            return run_import(self)
        self._validate_username()
        if self.full_import:
            self.process_dropped()
        for name in self.stages:
            with _phase(name, self.username):
                getattr(self, f"process_{name}")()

        with _phase("save media", self.username):
            helpers.cleanup_existing_media(self.to_delete, self.user)
            helpers.bulk_create_media(self.bulk_media, self.user)

            if self.completed_seasons:
                bulk_update_with_history(
                    self.completed_seasons,
                    app.models.Season,
                    fields=["status"],
                    default_change_reason=f"Trakt import ({self.mode})",
                )
            if self.completed_tvs:
                bulk_update_with_history(
                    self.completed_tvs,
                    app.models.TV,
                    fields=["status"],
                    default_change_reason=f"Trakt import ({self.mode})",
                )
            if self.dropped_tvs:
                bulk_update_with_history(
                    self.dropped_tvs,
                    app.models.TV,
                    fields=["status"],
                    default_change_reason=f"Trakt import ({self.mode})",
                )

        # Neither bulk_create_media() nor bulk_update_with_history() call
        # TV.save(), so the season/episode cascade it normally fires for a
        # COMPLETED/DROPPED show never runs for Trakt-imported shows. A show
        # created fresh this run has its final status baked directly into
        # the bulk_create call (never touching completed_tvs/dropped_tvs
        # below), so collect every TV row this run touched, not just the
        # ones re-flushed above, and run the same cascade helpers TV.save()
        # would use.
        touched_tvs = {
            tv.pk: tv
            for tv in (
                *self.bulk_media[MediaTypes.TV.value],
                *self.completed_tvs,
                *self.dropped_tvs,
            )
            if tv.pk
        }
        with _phase("finish shows", self.username):
            for i, tv_obj in enumerate(touched_tvs.values(), start=1):
                import_progress.report(i, len(touched_tvs), "Trakt: finishing shows")
                if tv_obj.status == Status.COMPLETED.value:
                    pending_date = self.tv_completion_dates.get(f"{tv_obj.item.media_id}")
                    if pending_date is not None:
                        tv_obj._pending_end_date = pending_date
                    try:
                        tv_obj._completed()
                    except (
                        services.ProviderAPIError,
                        requests.exceptions.RequestException,
                        KeyError,
                        TypeError,
                        ValueError,
                    ) as error:
                        logger.warning(
                            "Skipping completion fan-out due to missing metadata"
                            " for %s: %s",
                            tv_obj.item.media_id,
                            error,
                        )
                elif tv_obj.status == Status.DROPPED.value:
                    tv_obj._mark_in_progress_seasons_as_dropped()

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))

        return imported_counts, deduplicated_messages

    def _make_api_request(self, url):
        """Make a request to the Trakt API with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"Floppy/{settings.VERSION}",
            "trakt-api-version": "2",
            "trakt-api-key": credentials.get("trakt", "client_id"),
        }
        if self.refresh_token:
            try:
                # already made api_request before, so access_token is set
                headers["Authorization"] = f"Bearer {self.access_token}"
            except AttributeError:
                self.access_token = get_access_token(
                    self.refresh_token,
                    self.redirect_uri,
                )
                headers["Authorization"] = f"Bearer {self.access_token}"
        return services.api_request(
            "TRAKT",
            "GET",
            url,
            headers=headers,
        )

    def _get_paginated_data(self, endpoint, item_type="items"):
        """Get paginated data from Trakt API."""
        page = 1
        all_data = HistoryPages() if item_type == "history entries" else []

        with contextlib.ExitStack() as cleanup:
            if isinstance(all_data, HistoryPages):
                cleanup.callback(all_data.close)

            while True:
                url = f"{endpoint}?page={page}&limit={BULK_PAGE_SIZE}"

                try:
                    page_data = self._make_api_request(url)
                except requests.exceptions.HTTPError as error:
                    self._raise_for_user_error(error)

                    if error.response.status_code == requests.codes.method_not_allowed:
                        logger.warning(
                            "Trakt endpoint %s returned 405 (not available for this account), skipping.",
                            endpoint,
                        )
                        return []
                    raise

                if not page_data:
                    # We've reached the end of the data
                    break

                all_data.extend(page_data)
                page += 1
                import_progress.report(
                    len(all_data),
                    total=None,
                    label=f"Trakt: gathering {item_type}…",
                )
                logger.info(
                    "Retrieved page %s of %s for user %s (%s items)",
                    page - 1,
                    item_type,
                    self.username,
                    len(page_data),
                )

            logger.info(
                "Retrieved %s total %s for user %s",
                len(all_data),
                item_type,
                self.username,
            )
            cleanup.pop_all()
            return all_data

    def process_history(self):
        """Process watch history from Trakt."""
        logger.info("Importing watch history for user %s", self.username)
        history_endpoint = f"{self.user_base_url}/history"
        with contextlib.ExitStack() as cleanup:
            full_history = self._get_paginated_data(history_endpoint, "history entries")
            if isinstance(full_history, HistoryPages):
                cleanup.callback(full_history.close)

            # Some private profiles can return empty user history with OAuth.
            # Fallback to the authenticated sync endpoint in that case.
            if self.is_oauth_import and not full_history:
                fallback_endpoint = f"{TRAKT_API_BASE_URL}/sync/history"
                logger.warning(
                    "Empty Trakt history for OAuth user %s at %s. Trying %s",
                    self.username,
                    history_endpoint,
                    fallback_endpoint,
                )
                try:
                    full_history = self._get_paginated_data(
                        fallback_endpoint,
                        "history entries",
                    )
                    if isinstance(full_history, HistoryPages):
                        cleanup.callback(full_history.close)
                except Exception:
                    logger.exception(
                        "Fallback Trakt history endpoint failed for user %s",
                        self.username,
                    )

            # Process in chronological order (oldest first)
            total = len(full_history)
            for i, entry in enumerate(reversed(full_history), start=1):
                import_progress.report(i, total, "Trakt: watch history")
                if i % HISTORY_LOG_EVERY == 0:
                    logger.info(
                        "Trakt history progress: %s of %s entries (user %s)",
                        i,
                        total,
                        self.username,
                    )
                watched_at = entry["watched_at"]
                try:
                    if entry["type"] == "movie":
                        logger.debug(
                            "Processing movie %s watched at %s",
                            entry["movie"]["title"],
                            watched_at,
                        )
                        self.process_watched_movie(entry)
                    elif entry["type"] == "episode":
                        logger.debug(
                            "Processing episode %s S%sE%s watched at %s",
                            entry["show"]["title"],
                            entry["episode"]["season"],
                            entry["episode"]["number"],
                            watched_at,
                        )
                        self.process_watched_episode(entry)
                except MediaImportError:
                    # Fatal, importer-level problems (auth, etc.) must still abort.
                    raise
                except DatabaseError as error:
                    # Failed persistence is not a malformed source entry. Do
                    # not report a successful import with missing watches.
                    msg = "Trakt history database write failed. Retry the import after background writes finish."
                    raise MediaImportError(msg) from error
                except Exception as e:
                    # A single malformed/unexpected entry should not abort the whole
                    # import; record it as a warning and continue with the rest.
                    logger.exception("Skipping Trakt history entry")
                    source_data = entry.get("show") or entry.get("movie") or {}
                    title = source_data.get("title", "Unknown title")
                    self.warnings.append(
                        f"{title}: skipped a watch entry due to an unexpected error ({e}).",
                    )

    def process_watched_movie(self, entry):
        """Process a single movie watch event."""
        movie = entry["movie"]
        tmdb_id = self._get_tmdb_id(movie, MediaTypes.MOVIE.value)
        if not tmdb_id:
            return

        watched_at = entry["watched_at"]
        watched_at_dt = _parse_watched_at(watched_at)

        # Check if we should process this movie based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
            deleted_media=self.deleted_media,
            # Like episodes, each movie watch is judged by the play dedupe
            # below, so an older Trakt watch of a tracked movie is kept (#1423).
            # An undated watch cannot be deduped, so a tracked movie keeps
            # skipping it or every re-import would add another row.
            skip_existing=watched_at_dt is None,
        ):
            return

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, movie["title"])
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        self._remember_trakt_reference(movie, MediaTypes.MOVIE.value, item)

        self.existing_movie_play_times.record_runtime(tmdb_id, item.runtime_minutes)
        if self.existing_movie_play_times.is_duplicate(tmdb_id, watched_at_dt):
            logger.debug(
                "Skipping Trakt movie watch for %s at %s: duplicate of an existing play",
                movie["title"],
                watched_at,
            )
            return

        key = f"{tmdb_id}"

        movie_obj = app.models.Movie(
            item=item,
            user=self.user,
            end_date=watched_at_dt,
            status=Status.COMPLETED.value,
            progress=1,
        )
        if watched_at_dt is not None:
            movie_obj._history_date = watched_at_dt

        self.media_instances[MediaTypes.MOVIE.value][key].append(movie_obj)
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_obj)
        self.existing_movie_play_times.add(tmdb_id, watched_at_dt)

    def process_watched_episode(self, entry):
        """Process a single episode watch event."""
        show = entry["show"]
        tmdb_id = self._get_tmdb_id(show, MediaTypes.TV.value)
        if not tmdb_id:
            return

        if tmdb_id in self.deleted_media[MediaTypes.TV.value][Sources.TMDB.value]:
            logger.debug(
                "Skipping watch history for deleted TV show: %s (deleted locally)",
                tmdb_id,
            )
            return

        # Extract episode data
        season_number = entry["episode"]["season"]
        episode_number = entry["episode"]["number"]
        reference = self._get_trakt_reference(show, MediaTypes.TV.value)
        season_number, episode_number = external_references.map_episode_coordinates(
            reference,
            season_number,
            episode_number,
        )
        watched_at = entry["watched_at"]
        watched_at_dt = _parse_watched_at(watched_at)
        episode_watch_key = (tmdb_id, season_number, episode_number, watched_at_dt)

        if episode_watch_key in self.existing_episode_watch_keys:
            logger.debug(
                "Skipping existing episode watch for %s S%sE%s at %s",
                show["title"],
                season_number,
                episode_number,
                watched_at,
            )
            return

        episode_key = (tmdb_id, season_number, episode_number)
        if self.existing_episode_play_times.is_duplicate(episode_key, watched_at_dt):
            logger.debug(
                "Skipping Trakt episode watch for %s S%sE%s at %s: "
                "duplicate of an existing play",
                show["title"],
                season_number,
                episode_number,
                watched_at,
            )
            return

        tv_exists = (
            tmdb_id in self.existing_media[MediaTypes.TV.value][Sources.TMDB.value]
        )
        if self.mode == "overwrite" and tv_exists:
            self.to_delete[MediaTypes.TV.value][Sources.TMDB.value].add(tmdb_id)

        # Get TV metadata
        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, show["title"])
        if not tv_metadata:
            return
        self._adopt_provider_tv(tmdb_id, show["title"])

        # Get Season metadata
        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            show["title"],
            season_number,
        )
        if not season_metadata:
            return

        # Validate episode number exists in TMDB
        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )

        if not episode_exists:
            item_identifier = f"{show['title']} S{int(season_number):02d}E{int(episode_number):02d}"
            self.warnings.append(
                f"{item_identifier}: not found in {Sources.TMDB.label} "
                f"with ID {tmdb_id}.",
            )
            return

        episode_image = self._get_episode_image(episode_number, season_metadata)
        matched_episode = next(
            (
                ep
                for ep in season_metadata["episodes"]
                if ep["episode_number"] == episode_number
            ),
            None,
        )

        anime_bucket = self._anime_bucket_for_show(tmdb_id, tv_metadata)
        if anime_bucket == "skip":
            if tmdb_id not in self.skipped_flat_anime:
                self.skipped_flat_anime.add(tmdb_id)
                self.warnings.append(
                    f"{tv_metadata['title']}: tracked as anime on "
                    f"{Sources.MAL.label}; skipped so it is not also imported "
                    "into TV",
                )
            return

        # Create or get TV show
        tv_item = self._get_or_create_item(
            MediaTypes.TV.value,
            tmdb_id,
            tv_metadata,
            library_media_type=anime_bucket,
        )
        self._remember_trakt_reference(show, MediaTypes.TV.value, tv_item)
        tv_key = f"{tmdb_id}"

        if tv_key not in self.media_instances[MediaTypes.TV.value]:
            tv_obj = self.existing_media[MediaTypes.TV.value][Sources.TMDB.value].get(
                tmdb_id,
            )
            tv_marked_for_deletion = (
                tmdb_id in self.to_delete[MediaTypes.TV.value][Sources.TMDB.value]
            )
            if tv_obj is None or tv_marked_for_deletion:
                status = (
                    Status.DROPPED.value
                    if tmdb_id in self.dropped_tmdb_ids
                    else Status.IN_PROGRESS.value
                )
                tv_obj = app.models.TV(
                    item=tv_item,
                    user=self.user,
                    status=status,
                )
                if watched_at_dt is not None:
                    tv_obj._history_date = watched_at_dt
                self.bulk_media[MediaTypes.TV.value].append(tv_obj)
                self.tv_created_this_run.add(tv_key)
            elif (
                self.mode == "overwrite"
                and tmdb_id in self.dropped_tmdb_ids
                and tv_obj.status != Status.DROPPED.value
            ):
                tv_obj.status = Status.DROPPED.value
                self.dropped_tvs.append(tv_obj)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        else:
            tv_obj = self.media_instances[MediaTypes.TV.value][tv_key][0]

        # Create or get Season
        season_item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
            library_media_type=anime_bucket,
        )

        season_key = f"{tmdb_id}:{season_number}"
        if season_key not in self.media_instances[MediaTypes.SEASON.value]:
            tv_marked_for_deletion = (
                tmdb_id in self.to_delete[MediaTypes.TV.value][Sources.TMDB.value]
            )
            season_obj = None
            if not tv_marked_for_deletion:
                season_obj = app.models.Season.objects.filter(
                    user=self.user,
                    item=season_item,
                ).first()
            if season_obj is None:
                season_obj = app.models.Season(
                    item=season_item,
                    user=self.user,
                    related_tv=tv_obj,
                    # History for a new season of a show the user dropped or
                    # paused is recorded without reopening the show.
                    status=(
                        tv_obj.status
                        if tv_obj.status in app.models.USER_HELD_STATUSES
                        else Status.IN_PROGRESS.value
                    ),
                )
                if watched_at_dt is not None:
                    season_obj._history_date = watched_at_dt
                self.bulk_media[MediaTypes.SEASON.value].append(season_obj)
                self.season_created_this_run.add(season_key)
            self.media_instances[MediaTypes.SEASON.value][season_key] = [season_obj]
        else:
            season_obj = self.media_instances[MediaTypes.SEASON.value][season_key][0]

        # Create Episode item and object
        episode_metadata = {
            **app.models.Item.title_fields_from_episode_metadata(
                matched_episode,
                fallback_title=tv_metadata["title"],
            ),
            "image": episode_image,
        }
        episode_item = self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
            library_media_type=anime_bucket,
        )
        self._remember_trakt_reference(
            entry.get("episode") or {},
            MediaTypes.EPISODE.value,
            episode_item,
        )

        ep_key = f"{tmdb_id}:{season_number}:{episode_number}"

        episode_obj = app.models.Episode(
            item=episode_item,
            related_season=season_obj,
            end_date=watched_at_dt,
        )
        if watched_at_dt is not None:
            episode_obj._history_date = watched_at_dt
        self.media_instances[MediaTypes.EPISODE.value][ep_key].append(episode_obj)
        self.bulk_media[MediaTypes.EPISODE.value].append(episode_obj)
        self.existing_episode_watch_keys.add(episode_watch_key)
        self.existing_episode_play_times.add(
            episode_key,
            watched_at_dt,
            episode_item.runtime_minutes,
        )

        # Update status if this is the last episode, but only for rows Floppy
        # just created (or an explicit overwrite re-sync) — never clobber the
        # status of a show/season the user is already tracking locally.
        if self.mode == "overwrite" or season_key in self.season_created_this_run:
            self._update_completion_status(
                season_obj,
                tv_obj,
                season_number,
                episode_number,
                season_metadata,
                tv_metadata,
                tv_key,
                watched_at_dt,
            )

    def _update_completion_status(
        self,
        season_obj,
        tv_obj,
        season_number,
        episode_number,
        season_metadata,
        tv_metadata,
        tv_key,
        watched_at_dt,
    ):
        """Update completion status for season and TV show if applicable."""
        if episode_number == helpers.season_last_episode_number(season_metadata):
            season_obj.status = Status.COMPLETED.value
            if season_obj.pk:
                self.completed_seasons.append(season_obj)

            last_season = tv_metadata.get("last_episode_season")
            # Cascading full-show completion fabricates every missing
            # season/episode via TV._completed(). That's safe for an
            # explicit overwrite re-sync (already reconciling a show Floppy
            # tracks against its current Trakt state, per #985). For a show
            # created fresh this run, it's only safe when the finale we just
            # saw is in season 1 — i.e. the show has no earlier season the
            # fan-out could fabricate. Otherwise, seeing one finale watch
            # (e.g. a rewatch, or a partial history export) would wrongly
            # backfill every earlier season/episode as watched (issue #1178).
            tv_eligible = self.mode == "overwrite" or (
                tv_key in self.tv_created_this_run and season_number == 1
            )
            if last_season and last_season == season_number and tv_eligible:
                tv_obj.status = Status.COMPLETED.value
                if tv_obj.pk:
                    self.completed_tvs.append(tv_obj)
                if watched_at_dt is not None:
                    self.tv_completion_dates[tv_key] = watched_at_dt

    def process_watchlist(self):
        """Process watchlist from Trakt."""
        logger.info("Importing watchlist for user %s", self.username)
        watchlist_endpoint = f"{self.user_base_url}/watchlist"
        watchlist_data = self._get_paginated_data(watchlist_endpoint, "watchlist items")

        total = len(watchlist_data)
        for i, entry in enumerate(watchlist_data, start=1):
            import_progress.report(i, total, "Trakt: watchlist")
            try:
                self._process_generic_entry(
                    entry,
                    "watchlist",
                    {"status": Status.PLANNING.value},
                )
            except MediaImportError:
                # Fatal, importer-level problems (auth, etc.) must still abort.
                raise
            except Exception as e:
                msg = f"Error processing watchlist entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_ratings(self):
        """Process ratings from Trakt."""
        logger.info("Importing ratings for user %s", self.username)
        ratings_endpoint = f"{self.user_base_url}/ratings"
        ratings_data = self._get_paginated_data(ratings_endpoint, "ratings")

        total = len(ratings_data)
        for i, entry in enumerate(ratings_data, start=1):
            import_progress.report(i, total, "Trakt: ratings")
            try:
                self._process_generic_entry(
                    entry,
                    "rating",
                    # Trakt rates out of 10, which is already the internal storage
                    # scale, so the score is stored as-is. scale_score_for_storage
                    # converts *display* scores and would double a 5-scale user's.
                    {"score": entry["rating"]},
                )
            except MediaImportError:
                # Fatal, importer-level problems (auth, etc.) must still abort.
                raise
            except Exception as e:
                msg = f"Error processing rating entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_notes(self):
        """Process private notes from Trakt."""
        logger.info("Importing notes for user %s", self.username)
        notes_endpoint = f"{self.user_base_url}/notes"
        full_notes = self._get_paginated_data(notes_endpoint, "notes")

        total = len(full_notes)
        for i, entry in enumerate(full_notes, start=1):
            import_progress.report(i, total, "Trakt: notes")
            note_text = entry.get("note", {}).get("notes")
            if not note_text:
                continue
            try:
                self._process_generic_entry(entry, "note", {"notes": note_text})
            except MediaImportError:
                # Fatal, importer-level problems (auth, etc.) must still abort.
                raise
            except Exception as e:
                msg = f"Error processing note entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_comments(self):
        """Process comments from Trakt."""
        logger.info("Importing comments for user %s", self.username)
        comments_endpoint = f"{self.user_base_url}/comments"
        full_comments = self._get_paginated_data(comments_endpoint, "comments")

        total = len(full_comments)
        for i, entry in enumerate(full_comments, start=1):
            import_progress.report(i, total, "Trakt: comments")
            try:
                self._process_generic_entry(
                    entry,
                    "comment",
                    {"notes": entry["comment"]["comment"]},
                )
            except MediaImportError:
                # Fatal, importer-level problems (auth, etc.) must still abort.
                raise
            except Exception as e:
                msg = f"Error processing comment entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_collection(self):
        """Import owned-copy (collection) data from Trakt."""
        # Imported lazily to avoid a circular import: trakt_collection imports
        # TraktMetadataResolverMixin from this module.
        from integrations.imports import trakt_collection

        logger.info("Importing collection for user %s", self.username)

        movies_endpoint = f"{self.user_base_url}/collection/movies"
        collected_movies = self._get_paginated_data(movies_endpoint, "collected movies")
        movies_total = len(collected_movies)
        for i, entry in enumerate(collected_movies, start=1):
            import_progress.report(i, movies_total, "Trakt: collection (movies)")
            try:
                self._process_collected_movie(entry, trakt_collection)
            except Exception as e:
                logger.exception("Skipping Trakt collection movie entry")
                title = entry.get("movie", {}).get("title", "Unknown title")
                self.warnings.append(
                    f"{title}: skipped a collection entry due to an unexpected "
                    f"error ({e}).",
                )

        shows_endpoint = f"{self.user_base_url}/collection/shows"
        collected_shows = self._get_paginated_data(shows_endpoint, "collected shows")
        shows_total = len(collected_shows)
        for i, entry in enumerate(collected_shows, start=1):
            import_progress.report(i, shows_total, "Trakt: collection (shows)")
            try:
                self._process_collected_show(entry, trakt_collection)
            except Exception as e:
                logger.exception("Skipping Trakt collection show entry")
                title = entry.get("show", {}).get("title", "Unknown title")
                self.warnings.append(
                    f"{title}: skipped a collection entry due to an unexpected "
                    f"error ({e}).",
                )

    def _process_collected_movie(self, entry, trakt_collection):
        """Process a single Trakt collection movie entry."""
        movie = entry["movie"]
        tmdb_id = self._get_tmdb_id(movie, MediaTypes.MOVIE.value)
        if not tmdb_id:
            return

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, movie["title"])
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        self._remember_trakt_reference(movie, MediaTypes.MOVIE.value, item)
        trakt_collection.upsert_collection_entry(
            self.user,
            item,
            mode=self.mode,
            collected_at=_parse_watched_at(entry.get("collected_at", "")),
            metadata=entry.get("metadata"),
        )

    def _process_collected_show(self, entry, trakt_collection):
        """Process a single Trakt collection show entry, including its episodes."""
        show = entry["show"]
        tmdb_id = self._get_tmdb_id(show, MediaTypes.TV.value)
        if not tmdb_id:
            return

        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, show["title"])
        if not tv_metadata:
            return

        anime_bucket = self._anime_bucket_for_show(tmdb_id, tv_metadata)
        if anime_bucket == "skip":
            return

        tv_item = self._get_or_create_item(
            MediaTypes.TV.value,
            tmdb_id,
            tv_metadata,
            library_media_type=anime_bucket,
        )
        self._remember_trakt_reference(show, MediaTypes.TV.value, tv_item)
        show_reference = self._get_trakt_reference(show, MediaTypes.TV.value)

        # Rolled-up season/show entries keep the newest historical date.
        show_collected_at = _parse_watched_at(entry.get("last_collected_at", ""))

        for season_entry in entry.get("seasons", []):
            season_collected_at = None
            season_number = season_entry["number"]
            mapped_season, _ = external_references.map_episode_coordinates(
                show_reference,
                season_number,
                1,
            )
            if show_reference and show_reference.episode_mapping:
                season_number = mapped_season
            season_metadata = self._get_metadata(
                MediaTypes.SEASON.value,
                tmdb_id,
                show["title"],
                season_number,
            )
            if not season_metadata:
                continue
            season_item = self._get_or_create_item(
                MediaTypes.SEASON.value,
                tmdb_id,
                season_metadata,
                season_number,
                library_media_type=anime_bucket,
            )

            for episode_entry in season_entry.get("episodes", []):
                episode_number = episode_entry["number"]
                season_number, episode_number = (
                    external_references.map_episode_coordinates(
                        show_reference,
                        season_entry["number"],
                        episode_number,
                    )
                )
                episode_exists = any(
                    ep["episode_number"] == episode_number
                    for ep in season_metadata["episodes"]
                )
                if not episode_exists:
                    self.warnings.append(
                        f"{show['title']} S{int(season_number):02d}E{int(episode_number):02d}: not "
                        f"found in {Sources.TMDB.label} with ID {tmdb_id}.",
                    )
                    continue

                episode_image = self._get_episode_image(
                    episode_number,
                    season_metadata,
                )
                episode_metadata = {
                    "title": tv_metadata["title"],
                    "original_title": tv_metadata.get("original_title"),
                    "localized_title": tv_metadata.get("localized_title"),
                    "image": episode_image,
                }
                episode_item = self._get_or_create_item(
                    MediaTypes.EPISODE.value,
                    tmdb_id,
                    episode_metadata,
                    season_number,
                    episode_number,
                    library_media_type=anime_bucket,
                )
                self._remember_trakt_reference(
                    episode_entry,
                    MediaTypes.EPISODE.value,
                    episode_item,
                )
                episode_collected_at = _parse_watched_at(
                    episode_entry.get("collected_at", ""),
                )
                if episode_collected_at:
                    if not season_collected_at or (
                        episode_collected_at > season_collected_at
                    ):
                        season_collected_at = episode_collected_at
                    if not show_collected_at or (
                        episode_collected_at > show_collected_at
                    ):
                        show_collected_at = episode_collected_at
                trakt_collection.upsert_collection_entry(
                    self.user,
                    episode_item,
                    mode=self.mode,
                    collected_at=episode_collected_at,
                    metadata=episode_entry.get("metadata"),
                )

            trakt_collection.apply_season_and_show_rollup(
                self.user,
                self.mode,
                tv_item,
                season_item,
                tv_metadata,
                season_metadata,
                collected_at=season_collected_at,
                show_collected_at=show_collected_at,
            )

    def _supports_hidden_sections(self):
        """Whether the hidden/progress sections are reachable for this import.

        Overridden by the export importer, which reads them from files.
        """
        return bool(self.refresh_token)

    def process_dropped(self):
        """Collect TMDB IDs of shows the user has dropped (hidden from progress).

        Trakt represents dropped shows as hidden items in the progress_watched
        section.  Requires OAuth — public imports skip this silently.
        """
        if not self._supports_hidden_sections():
            return
        logger.info("Importing dropped shows for user %s", self.username)
        for section in ("progress_watched", "progress_watched_reset"):
            endpoint = f"{self.user_base_url}/hidden/{section}"
            hidden_data = self._get_paginated_data(endpoint)
            for entry in hidden_data:
                if entry.get("type") != "show":
                    continue
                tmdb_id = self._get_tmdb_id(entry["show"], MediaTypes.TV.value)
                if tmdb_id:
                    self.dropped_tmdb_ids.add(tmdb_id)

    def _process_generic_entry(self, entry, entry_type, attribute_updates):
        """Process a generic entry (watchlist, rating, or comment)."""
        if entry["type"] == "movie":
            logger.debug(
                "Processing movie %s for %s",
                entry["movie"]["title"],
                entry_type,
            )
            # Only an explicit Completed status implies a watch. Ratings and
            # comments carry no status, so they must not fabricate progress.
            if attribute_updates.get("status") == Status.COMPLETED.value:
                attribute_updates["progress"] = 1

            self._process_media_item(
                entry,
                entry["movie"],
                MediaTypes.MOVIE.value,
                app.models.Movie,
                attribute_updates,
                entry_type=entry_type,
            )
        elif entry["type"] == "show":
            logger.debug(
                "Processing show %s for %s",
                entry["show"]["title"],
                entry_type,
            )
            self._process_media_item(
                entry,
                entry["show"],
                MediaTypes.TV.value,
                app.models.TV,
                attribute_updates,
                entry_type=entry_type,
            )
        elif entry["type"] == "season":
            logger.debug(
                "Processing season %s S%s for %s",
                entry["show"]["title"],
                entry["season"]["number"],
                entry_type,
            )
            self._process_media_item(
                entry,
                entry["show"],
                MediaTypes.SEASON.value,
                app.models.Season,
                attribute_updates,
                entry["season"]["number"],
                entry_type=entry_type,
            )
        elif entry["type"] == "episode":
            logger.debug(
                "Processing episode %s S%sE%s for %s",
                entry["show"]["title"],
                entry["episode"]["season"],
                entry["episode"]["number"],
                entry_type,
            )
            self._process_episode_attribute(
                entry["show"],
                entry["episode"],
                attribute_updates,
            )

    def _process_episode_attribute(self, show_data, episode_data, attribute_updates):
        """Apply attribute updates (e.g. score) to existing Episode instances."""
        tmdb_id = self._get_tmdb_id(show_data, MediaTypes.TV.value)
        if not tmdb_id:
            return

        score = attribute_updates.get("score")
        if score is None:
            return

        season_number = episode_data["season"]
        episode_number = episode_data["number"]
        reference = self._get_trakt_reference(show_data, MediaTypes.TV.value)
        season_number, episode_number = external_references.map_episode_coordinates(
            reference,
            season_number,
            episode_number,
        )

        season_obj = app.models.Season.objects.filter(
            item__media_id=str(tmdb_id),
            item__source=Sources.TMDB.value,
            item__season_number=season_number,
            user=self.user,
        ).first()

        # Fall back to in-memory season created earlier in this same import run
        # (process_history builds objects into bulk_media before bulk_create_media
        # commits them, so the DB lookup above finds nothing on a first import).
        if not season_obj:
            season_key = f"{tmdb_id}:{season_number}"
            season_list = self.media_instances[MediaTypes.SEASON.value].get(season_key)
            if season_list:
                season_obj = season_list[0]

        if not season_obj:
            return

        # Trakt scores are already on the 0-10 storage scale; converting them
        # with scale_score_for_storage doubled them for 5-scale users.
        scaled_score = score

        # Try persisted episodes first (season_obj must have a pk to filter by it)
        if season_obj.pk:
            episodes = app.models.Episode.objects.filter(
                related_season=season_obj,
                item__episode_number=episode_number,
            )
            if episodes.exists():
                episodes.exclude(score=scaled_score).update(
                    score=scaled_score,
                    scored_at=timezone.now(),
                )
                return

        # Fall back to in-memory episode objects from this same import run
        ep_key = f"{tmdb_id}:{season_number}:{episode_number}"
        for ep_obj in self.media_instances[MediaTypes.EPISODE.value].get(ep_key, []):
            ep_obj.score = scaled_score

    def _process_media_item(
        self,
        entry,
        media_data,
        media_type,
        model_class,
        defaults,
        season_number=None,
        *,
        entry_type=None,
    ):
        """Process media items for watchlist, ratings, and comments."""
        tmdb_id = self._get_tmdb_id(media_data, media_type)
        if not tmdb_id:
            return

        parent_type = (
            MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
        )
        if (
            parent_type == MediaTypes.TV.value
            and tmdb_id not in self.deleted_media[parent_type][Sources.TMDB.value]
        ):
            self._adopt_provider_tv(tmdb_id, media_data["title"])
        should_process = helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            parent_type,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
            deleted_media=self.deleted_media,
        )
        if (
            not should_process
            and self.mode == "new"
            and media_type == MediaTypes.SEASON.value
        ):
            # The show already existing shouldn't block a season it doesn't
            # have yet - check this row's own granularity instead.
            child_key = (tmdb_id, season_number)
            should_process = (
                child_key
                not in self.existing_children[MediaTypes.SEASON.value][
                    Sources.TMDB.value
                ]
            )
        if not should_process:
            return

        metadata = self._get_metadata(
            media_type,
            tmdb_id,
            media_data["title"],
            season_number,
        )
        if not metadata:
            return

        updated_at = parse_datetime(
            entry.get("listed_at")
            or entry.get("rated_at")
            or (entry.get("comment") or entry.get("note") or {}).get("updated_at")
            # An entry may carry no date at all (e.g. a WeTrakr export row).
            or "",
        )

        # A rating, a comment, or a note says nothing about whether the user
        # tracks the media. When it isn't already tracked from history or the
        # watchlist, store the score without a status rather than inventing one.
        rates_without_tracking = entry_type in {"rating", "comment", "note"}

        if media_type == MediaTypes.SEASON.value:
            tv_obj = self._get_tv_obj(
                tmdb_id,
                media_data,
                updated_at,
                status=None if rates_without_tracking else Status.IN_PROGRESS.value,
            )
            if not tv_obj:
                return
            defaults["related_tv"] = tv_obj

        key = f"{tmdb_id}"
        if media_type == MediaTypes.SEASON.value:
            key = f"{key}:{season_number}"

        item = self._get_or_create_item(media_type, tmdb_id, metadata, season_number)
        self._remember_trakt_reference(media_data, media_type, item)

        if key in self.media_instances[media_type]:
            self._update_instance(media_type, key, defaults)
        else:
            if rates_without_tracking:
                defaults.setdefault("status", None)
            media_obj = model_class(
                item=item,
                user=self.user,
                **defaults,
            )
            if updated_at is not None:
                media_obj._history_date = updated_at
            self.bulk_media[media_type].append(media_obj)
            self.media_instances[media_type][key] = [media_obj]

    def _get_tv_obj(
        self,
        tmdb_id,
        media_data,
        updated_at,
        status=Status.IN_PROGRESS.value,
    ):
        """Get or create a TV object for the given season."""
        tv_metadata = self._get_metadata(
            MediaTypes.TV.value,
            tmdb_id,
            media_data["title"],
        )
        if not tv_metadata:
            return None

        tv_item = self._get_or_create_item(
            MediaTypes.TV.value,
            tmdb_id,
            tv_metadata,
        )

        tv_key = f"{tmdb_id}"

        # Create or get the TV object
        existing_tv = None
        if (
            tv_key not in self.media_instances[MediaTypes.TV.value]
            and tmdb_id not in self.to_delete[MediaTypes.TV.value][Sources.TMDB.value]
        ):
            # A season row for a show the user already tracks belongs to that
            # show; creating a second, In progress TV row would override the
            # status the user chose.
            existing_tv = self.existing_media[MediaTypes.TV.value][
                Sources.TMDB.value
            ].get(tmdb_id)
        if tv_key in self.media_instances[MediaTypes.TV.value]:
            tv_obj = self.media_instances[MediaTypes.TV.value][tv_key][0]
        elif existing_tv is not None:
            tv_obj = existing_tv
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        else:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=status,
            )
            if updated_at is not None:
                tv_obj._history_date = updated_at
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        return tv_obj

    def _update_instance(self, media_type, key, defaults):
        """Update the instance with new attributes."""
        for media_obj in self.media_instances[media_type][key]:
            for attr, value in defaults.items():
                setattr(media_obj, attr, value)
