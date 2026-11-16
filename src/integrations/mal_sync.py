"""OAuth connection handling and status pushing for MyAnimeList sync.

MyAnimeList's OAuth2 implementation only supports the "plain" PKCE transform,
so the code_challenge sent to /authorize is the same string as the
code_verifier sent to /token (see
https://myanimelist.net/apiconfig/references/authorization). Because the
redirect URI must match exactly what's registered on MAL, a shared client
can't work the way MAL_API does for read-only search - each user or instance
needs their own MyAnimeList API application, brought through the same
Settings > Metadata credential system as everything else (see
app.providers.credentials, slug "mal").
"""

import logging
import secrets
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import requests
from django.apps import apps
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from app import cache_safety
from app.models.choices import MediaTypes, Sources, Status
from app.providers import credentials, services
from integrations.imports.helpers import decrypt, encrypt
from integrations.models import MALAccount

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"  # noqa: S105 (URL, not a secret)
API_BASE_URL = "https://api.myanimelist.net/v2"

# Refresh slightly before the real expiry so a push never races an expiring token.
EXPIRY_LEEWAY = timedelta(minutes=2)

# Triggers for one grouped show inside this window collapse into one push.
GROUPED_SYNC_DEBOUNCE_SECONDS = 5

ANIME_STATUS_TO_MAL = {
    Status.COMPLETED.value: "completed",
    Status.IN_PROGRESS.value: "watching",
    Status.PLANNING.value: "plan_to_watch",
    Status.PAUSED.value: "on_hold",
    Status.DROPPED.value: "dropped",
}

MANGA_STATUS_TO_MAL = {
    Status.COMPLETED.value: "completed",
    Status.IN_PROGRESS.value: "reading",
    Status.PLANNING.value: "plan_to_read",
    Status.PAUSED.value: "on_hold",
    Status.DROPPED.value: "dropped",
}

# The field MAL accepts on a write differs from the field it reports back on a
# read (e.g. "num_watched_episodes" in, "num_episodes_watched" out).
PROGRESS_RESPONSE_FIELDS = {
    MediaTypes.ANIME.value: ("num_watched_episodes", "num_episodes_watched"),
    MediaTypes.MANGA.value: ("num_chapters_read", "num_chapters_read"),
}

MAL_STATUS_TO_FLOPPY = {
    MediaTypes.ANIME.value: {value: key for key, value in ANIME_STATUS_TO_MAL.items()},
    MediaTypes.MANGA.value: {value: key for key, value in MANGA_STATUS_TO_MAL.items()},
}

ZERO_PROGRESS_STATUS_PULLS = {
    Status.PLANNING.value,
    Status.PAUSED.value,
    Status.DROPPED.value,
}
PREVIEW_FIELD_SORT_ORDER = {
    "Status": 0,
    "Episodes watched": 1,
    "Chapters read": 1,
    "Score": 2,
}

# Namespace used on ExternalReference to remember grouped anime the user has
# chosen to stop seeing mapping issues for (see mal_mapping_ignore in views.py).
MAPPING_IGNORE_NAMESPACE = "grouped_anime_ignore"


class MALAuthError(Exception):
    """Raised when MyAnimeList rejects an OAuth request or credentials are missing."""


class MALNotConfiguredError(MALAuthError):
    """Raised when this instance or user has no complete MAL client ID and secret.

    A setup problem, not a rejection: MAL never saw the request, so the
    connection isn't broken and adding the credentials back is enough.
    """


class MALSyncMismatchError(Exception):
    """Raised when MAL returns success but its response shows the change wasn't applied.

    Seen for some new list adds (HTTP 200, but the item never shows up on the
    user's MAL list) - MAL doesn't surface a clean error for this, so the only
    way to detect it is to check the echoed list_status against what was sent.
    """


def client_credentials(user):
    """Return the MAL client ID and secret for OAuth, taken from one source.

    Both must belong to the same MAL app, so a personal secret is never
    paired with the shared or instance client ID, or the other way round.
    """
    return credentials.get_together("mal", ("client_id", "client_secret"), user=user)


def client_id(user):
    """Return the MAL client ID that goes with this user's client secret."""
    return client_credentials(user)[0]


def client_secret(user):
    """Return the MAL client secret for this user's OAuth app."""
    return client_credentials(user)[1]


def is_sync_configured(user):
    """Return whether this user has a complete MAL client ID and secret pair."""
    return bool(client_id(user) and client_secret(user))


def generate_code_verifier():
    """Return a PKCE code_verifier (also the code_challenge - see module docstring)."""
    return secrets.token_urlsafe(64)


def exchange_code_for_tokens(user, code, code_verifier, redirect_uri):
    """Exchange an authorization code for a fresh access/refresh token pair."""
    data = {
        "client_id": client_id(user),
        "client_secret": client_secret(user),
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    return _request_token(user, data)


def _refresh_tokens(mal_account):
    """Use the stored refresh token to obtain a new token pair."""
    data = {
        "client_id": client_id(mal_account.user),
        "client_secret": client_secret(mal_account.user),
        "refresh_token": decrypt(mal_account.refresh_token),
        "grant_type": "refresh_token",
    }
    return _request_token(mal_account.user, data)


def _request_token(user, data):
    """POST to the MAL token endpoint and normalize auth failures.

    api_request() lets requests.exceptions.HTTPError propagate (after handling
    retries itself) rather than wrapping it - every caller normalizes its own
    errors, the same way app.providers.mal does for reads.
    """
    if not is_sync_configured(user):
        msg = (
            "MyAnimeList sync isn't configured. Add your own MyAnimeList Client "
            "ID and Client secret under Settings > Metadata."
        )
        raise MALNotConfiguredError(msg)

    try:
        return services.api_request(Sources.MAL.value, "POST", TOKEN_URL, data=data)
    except requests.exceptions.HTTPError as error:
        status_code = getattr(error.response, "status_code", None)
        if status_code in {requests.codes.unauthorized, requests.codes.bad_request}:
            msg = "MyAnimeList rejected the request. Please reconnect your account."
            raise MALAuthError(msg) from error
        raise services.ProviderAPIError(Sources.MAL.value, error) from error


def _store_tokens(mal_account, token_response):
    """Persist a fresh token pair on the account (refresh tokens are one-time use)."""
    mal_account.access_token = encrypt(token_response["access_token"])
    mal_account.refresh_token = encrypt(token_response["refresh_token"])
    mal_account.token_expires_at = timezone.now() + timedelta(
        seconds=token_response["expires_in"],
    )
    mal_account.save(
        update_fields=[
            "access_token",
            "refresh_token",
            "token_expires_at",
            "updated_at",
        ],
    )


def _adopt_stored_tokens(target, source):
    """Copy the durable token pair onto the caller's possibly-stale instance."""
    target.access_token = source.access_token
    target.refresh_token = source.refresh_token
    target.token_expires_at = source.token_expires_at


# How long a refresh may hold the lock, and how long another worker waits
# for it. A refresh can sit through api_request's rate-limit and transient
# retries, so this is generous.
TOKEN_REFRESH_LOCK_SECONDS = 300


def get_valid_access_token(mal_account):
    """Return a usable (decrypted) access token, refreshing it first if needed.

    MAL refresh tokens work once, so two workers refreshing together would
    leave the second with a rejected token and a broken connection. The
    cache lock serialises them (select_for_update alone is a no-op on
    SQLite); the waiter then rereads the account and uses the new token.
    The token request itself runs outside the database transaction so a slow
    provider response cannot hold SQLite's write lock.
    """
    if timezone.now() >= mal_account.token_expires_at - EXPIRY_LEEWAY:
        lock_key = f"mal_token_refresh:{mal_account.pk}"
        deadline = time.monotonic() + TOKEN_REFRESH_LOCK_SECONDS
        while not cache_safety.acquire_lock(
            lock_key,
            timeout=TOKEN_REFRESH_LOCK_SECONDS,
            on_error=cache_safety.ON_ERROR_PROCEED,
        ):
            if time.monotonic() >= deadline:
                # Refreshing without the lock could spend a refresh token the
                # holder is still using; retry later instead.
                raise services.ProviderAPIError(
                    Sources.MAL.value,
                    requests.RequestException("MyAnimeList token refresh in progress"),
                )
            time.sleep(0.5)
        try:
            with transaction.atomic():
                current = MALAccount.objects.select_for_update().select_related(
                    "user",
                ).get(pk=mal_account.pk)
                needs_refresh = (
                    timezone.now() >= current.token_expires_at - EXPIRY_LEEWAY
                )
                spent_refresh_token = current.refresh_token if needs_refresh else None
                if not needs_refresh:
                    _adopt_stored_tokens(mal_account, current)

            if spent_refresh_token is not None:
                # The token POST can spend the full provider retry budget. Run
                # it outside the transaction: SQLite has one writer, and
                # holding it here starves every other write until the network
                # answers. The cache lock still serialises refreshes; the
                # compare below covers a refresh that slipped past it.
                token_response = _refresh_tokens(current)
                with transaction.atomic():
                    current = MALAccount.objects.select_for_update().select_related(
                        "user",
                    ).get(pk=mal_account.pk)
                    if current.refresh_token == spent_refresh_token:
                        _store_tokens(current, token_response)
                    _adopt_stored_tokens(mal_account, current)
        finally:
            cache_safety.release_lock(lock_key)

    return decrypt(mal_account.access_token)


def fetch_username(user, access_token):
    """Look up the display name for the account behind an access token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = services.api_request(
            Sources.MAL.value,
            "GET",
            f"{API_BASE_URL}/users/@me",
            params={"fields": "name"},
            headers=headers,
        )
    # A timeout surfaces as ProviderAPIError. The name is cosmetic, so neither
    # may stop a reconnect from marking the account healthy.
    except (requests.exceptions.HTTPError, services.ProviderAPIError):
        logger.warning("Could not fetch the MyAnimeList username after connecting.")
        return ""
    return response.get("name", "")


def connect_account(user, code, code_verifier, redirect_uri):
    """Exchange an auth code and create or refresh the user's MAL connection."""
    token_response = exchange_code_for_tokens(user, code, code_verifier, redirect_uri)

    mal_account, _ = MALAccount.objects.get_or_create(
        user=user,
        defaults={
            "access_token": "",
            "refresh_token": "",
            "token_expires_at": timezone.now(),
        },
    )
    _store_tokens(mal_account, token_response)

    mal_account.mal_username = (
        fetch_username(user, decrypt(mal_account.access_token))
        or mal_account.mal_username
    )
    mal_account.sync_enabled = True
    mal_account.connection_broken = False
    mal_account.last_error_message = ""
    mal_account.save(
        update_fields=[
            "mal_username",
            "sync_enabled",
            "connection_broken",
            "last_error_message",
            "updated_at",
        ],
    )
    return mal_account


def mal_score(score):
    """Return a Floppy score above 0 as a MAL score: whole, halves up, 1 at the least.

    MAL reads 0 as "no score", so a real rating below 0.5 still sends 1.
    A Floppy score of 0 has no MAL equivalent; status_payload never sends it.
    """
    rounded = int(Decimal(str(score)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return max(rounded, 1)


def status_payload(media, mal_account=None):
    """Return the exact fields that Floppy will push for a media entry."""
    media_type = media.item.media_type
    is_anime = media_type == MediaTypes.ANIME.value
    status_map = ANIME_STATUS_TO_MAL if is_anime else MANGA_STATUS_TO_MAL
    progress_field = "num_watched_episodes" if is_anime else "num_chapters_read"

    data = {
        "status": status_map[media.status],
        progress_field: media.progress,
    }
    sync_ratings = mal_account is None or mal_account.sync_ratings_enabled
    # MAL has no rating of 0 (it means "no score"), so a Floppy 0 is left
    # out rather than raised to 1 or sent as a clear.
    if media.score and sync_ratings:
        data["score"] = mal_score(media.score)
    return data


def _resolve_mal_from_provider_link(provider, provider_media_id, season_number, episode_number):
    """Resolve an episode through Floppy's own exact migration/correction mapping.

    ItemProviderLink is written by Floppy itself (auto-migrating a completed
    flat MAL anime, or a manual match correction) with an exact season and
    episode offset - unlike AniBridge's external mapping data, it cannot be
    ambiguous for shows with recap or alternate-numbering episodes, so it is
    tried first.

    Only season-scoped links count. A season-less link is a show-level
    identity (this MAL entry belongs to that TMDB show), not an episode
    alignment: applying it to every season maps S2E1 onto MAL episode 1.
    """
    from app.models import ItemProviderLink

    if not provider_media_id or season_number is None or episode_number is None:
        return None, None

    link = (
        ItemProviderLink.objects.filter(
            provider=provider,
            provider_media_type=MediaTypes.TV.value,
            provider_media_id=str(provider_media_id),
            season_number=season_number,
            item__source=Sources.MAL.value,
            item__media_type=MediaTypes.ANIME.value,
        )
        .select_related("item")
        .first()
    )
    if link is None:
        return None, None

    mapped_episode = episode_number - int(link.episode_offset or 0)
    if mapped_episode < 1:
        return None, None
    return str(link.item.media_id), mapped_episode


_PER_ITEM_PUSH_SUPPRESSED = ContextVar("mal_per_item_push_suppressed", default=False)


@contextmanager
def suppress_per_item_push():
    """Skip per-item MAL pushes while importing history.

    An import replays plays through the webhook handlers, which save rows one
    by one; each save would otherwise queue its own push. A full sync sends
    the imported state afterwards.
    """
    token = _PER_ITEM_PUSH_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _PER_ITEM_PUSH_SUPPRESSED.reset(token)


def per_item_sync_active(user_id):
    """Return whether saves should queue a per-item push for this user."""
    if _PER_ITEM_PUSH_SUPPRESSED.get():
        return False
    return MALAccount.objects.filter(
        user_id=user_id,
        sync_enabled=True,
        per_item_sync_enabled=True,
        connection_broken=False,
    ).exists()


# How long a removed-rating flag waits for its grouped push. It must outlast
# sync_mal_status's retries (about 15 minutes of backoff); holding it longer
# is harmless, since a cour rated again in the meantime is never cleared and
# a successful push removes the flag.
GROUPED_CLEAR_FLAG_SECONDS = 24 * 60 * 60


def grouped_clear_score_key(show_id):
    """Return the cache key flagging that a grouped show's rating was removed."""
    return f"mal_grouped_clear_score:{show_id}"


def queue_grouped_sync(user_id, item, clear_scope=None):
    """Queue current grouped progress after commit for connected, opted-in users.

    `clear_scope` flags a removed rating: "show", or the season number whose
    rating went. It's kept apart from the debounce window, so a later trigger
    in the same window can't drop it.
    """
    from app.models import TV
    from integrations.tasks import sync_mal_status

    if item is None or item.library_media_type != MediaTypes.ANIME.value:
        return
    if not per_item_sync_active(user_id):
        return
    shows = TV.objects.filter(user_id=user_id, item__library_media_type="anime")
    if item.media_type == MediaTypes.TV.value:
        shows = shows.filter(item=item)
    elif item.media_type in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}:
        shows = shows.filter(
            seasons__item__source=item.source,
            seasons__item__media_id=item.media_id,
            seasons__item__season_number=item.season_number,
            seasons__order_archived=False,
        )
    else:
        return
    def send(show_id):
        # A watch fires several triggers (watch state, episode, season). The
        # first one reserves the window; the push runs after it closes, so it
        # reads every change that committed inside it and is sent only once.
        if clear_scope is not None:
            clear_key = grouped_clear_score_key(show_id)
            try:
                scopes = set(cache.get(clear_key) or ()) | {clear_scope}
                cache.set(clear_key, list(scopes), timeout=GROUPED_CLEAR_FLAG_SECONDS)
            # The cache is down; the rating stays on MAL, nothing else fails.
            except Exception as error:
                logger.warning(
                    "mal_grouped_clear_flag_failed show_id=%s error=%s", show_id, error,
                )
        key = f"mal_grouped_sync:{show_id}"
        if not cache_safety.acquire_lock(
            key,
            timeout=GROUPED_SYNC_DEBOUNCE_SECONDS,
            on_error=cache_safety.ON_ERROR_PROCEED,
        ):
            return
        try:
            sync_mal_status.apply_async(
                kwargs={"media_type": "tv", "media_id": show_id},
                countdown=GROUPED_SYNC_DEBOUNCE_SECONDS + 1,
            )
        # The broker is down; the save already committed, so never raise.
        except Exception as error:
            cache_safety.release_lock(key)
            logger.warning(
                "mal_grouped_sync_enqueue_failed show_id=%s error=%s", show_id, error,
            )

    for show_id in shows.values_list("pk", flat=True).distinct():
        transaction.on_commit(lambda show_id=show_id: send(show_id))


def ignored_mapping_item_ids(user):
    """Return the TV item ids the user has chosen to ignore mapping issues for."""
    from integrations.models import ExternalReference, ExternalReferenceReviewStatus

    return set(
        ExternalReference.objects.filter(
            user=user,
            integration="mal_sync",
            external_namespace=MAPPING_IGNORE_NAMESPACE,
            review_status=ExternalReferenceReviewStatus.IGNORED.value,
        ).values_list("matched_item_id", flat=True)
    )


def ignored_mappings(user):
    """Return {item_id, title} for every anime ignored for MAL mapping issues."""
    from integrations.models import ExternalReference, ExternalReferenceReviewStatus

    references = (
        ExternalReference.objects.filter(
            user=user,
            integration="mal_sync",
            external_namespace=MAPPING_IGNORE_NAMESPACE,
            review_status=ExternalReferenceReviewStatus.IGNORED.value,
        )
        .select_related("matched_item")
        .order_by("matched_item__title")
    )
    return [
        {"item_id": reference.matched_item_id, "title": reference.matched_item.title}
        for reference in references
        if reference.matched_item is not None
    ]


def set_mapping_ignored(user, item_id, *, ignored):
    """Ignore or restore a grouped anime's MAL mapping issues.

    Returns True if the item was found and the change was applied.
    """
    from app.models import Item
    from integrations.models import ExternalReference, ExternalReferenceReviewStatus

    lookup = {
        "user": user,
        "integration": "mal_sync",
        "source_account": "",
        "external_namespace": MAPPING_IGNORE_NAMESPACE,
        "external_identity": str(item_id),
        "media_type": MediaTypes.TV.value,
    }
    if not ignored:
        ExternalReference.objects.filter(**lookup).delete()
        return True

    item = Item.objects.filter(
        pk=item_id, library_media_type=MediaTypes.ANIME.value,
    ).first()
    if item is None:
        return False
    ExternalReference.objects.update_or_create(
        **lookup,
        defaults={
            "matched_item": item,
            "review_status": ExternalReferenceReviewStatus.IGNORED.value,
        },
    )
    return True


def grouped_sync_entries(user, tv=None, mapping_issues=None, progress_callback=None):
    """Project grouped anime watches into transient MAL entries, one per cour."""
    from app.models import TV, Anime, Episode, Item, Season, WatchState
    from integrations.models import ExternalReference
    from integrations.webhooks import anime_mappings

    # A statusless show isn't tracked (see Media.status), so like a statusless
    # flat entry it never syncs, even when it still has watched episodes.
    shows = TV.objects.filter(
        user=user, item__library_media_type=MediaTypes.ANIME.value,
        status__isnull=False,
    )
    if tv is not None:
        shows = shows.filter(pk=tv.pk)
    shows = list(shows.select_related("item", "active_episode_order"))
    if not shows:
        return []

    mapping_data = anime_mappings.fetch_mapping_data()
    manual_mappings = {
        reference.external_identity: reference.episode_mapping
        for reference in ExternalReference.objects.filter(
            user=user,
            integration="mal_sync",
            external_namespace="grouped_anime_episode",
            review_status="corrected",
        )
    }
    ignored_ids = ignored_mapping_item_ids(user)
    entries = {}
    metadata_by_id = {}

    def mal_metadata(mal_id):
        """Return the MAL entry's metadata, or None when MAL no longer has it."""
        if mal_id not in metadata_by_id:
            try:
                with credentials.current_user_scope(user):
                    metadata_by_id[mal_id] = services.get_media_metadata(
                        "anime", mal_id, Sources.MAL.value,
                    )
            except services.ProviderAPIError as error:
                # A deleted or merged entry is a bad mapping for that
                # episode, not a reason to fail every other show.
                if error.status_code != requests.codes.not_found:
                    raise
                metadata_by_id[mal_id] = None
        return metadata_by_id[mal_id]

    for show_index, show in enumerate(shows, start=1):
        if progress_callback:
            progress_callback(show_index, len(shows), show.item.title)
        if show.item_id in ignored_ids:
            continue
        unmapped = []
        unmapped_episodes = []
        season_counts = {}
        watches = Episode.objects.filter(
            related_season__related_tv=show,
            related_season__order_archived=False,
        ).select_related("item", "related_season")
        states = WatchState.objects.filter(
            user=user,
            item__media_type=MediaTypes.EPISODE.value,
            item__library_media_type=MediaTypes.ANIME.value,
            item__source=show.tracking_source,
            item__media_id=show.tracking_media_id,
        ).select_related("item")
        coordinates = {
            state.item_id: (state.item, state.watched, show.score)
            for state in states
        }
        for watch in watches:
            if watch.item_id is None:
                continue
            previous = coordinates.get(watch.item_id)
            # A play left in progress isn't a watch; an earlier finished play
            # or a watched state still counts.
            finished = Episode.is_watch(watch)
            watched = finished or bool(previous and previous[1])
            score = watch.related_season.score
            coordinates[watch.item_id] = (
                watch.item,
                watched,
                score if score is not None else show.score,
            )

        has_history = bool(coordinates)
        if not has_history and show.status == Status.PLANNING.value:
            # A Planning show with nothing watched still belongs on MAL: find
            # its entry through the first episode of its first season. Other
            # statuses without history stay skipped, since their empty
            # progress would push "plan to watch" over the real MAL entry.
            first_season = min(
                Season.objects.filter(
                    related_tv=show,
                    order_archived=False,
                    item__season_number__gte=1,
                ).values_list("item__season_number", flat=True),
                default=1,
            )
            coordinates = {
                None: (
                    Item(
                        source=show.tracking_source,
                        media_id=show.tracking_media_id,
                        media_type=MediaTypes.EPISODE.value,
                        season_number=first_season,
                        episode_number=1,
                        title=show.item.title,
                    ),
                    False,
                    show.score,
                ),
            }
        mapped_any = False
        show_mal_ids = set()

        for item, watched, score in coordinates.values():
            if item.season_number is None or item.episode_number is None:
                unmapped.append(item.title)
                continue
            season_count = season_counts.setdefault(
                item.season_number,
                {"total": 0, "unmapped": []},
            )
            season_count["total"] += 1
            identity = f"{show.item_id}:{item.season_number}:{item.episode_number}"
            manual = manual_mappings.get(identity, {})
            mal_id = manual.get("mal_id")
            episode_number = manual.get("episode")
            if not mal_id or not episode_number:
                mal_id, episode_number = _resolve_mal_from_provider_link(
                    item.source,
                    item.media_id,
                    item.season_number,
                    item.episode_number,
                )
                # One link can cover only the first cour of a split season.
                # An episode past that entry's end belongs to a later cour,
                # which only AniBridge knows.
                link_metadata = mal_id and mal_metadata(str(mal_id))
                total = link_metadata and link_metadata.get("max_progress")
                if not link_metadata or (total and episode_number > total):
                    mal_id, episode_number = None, None
            if not mal_id or not episode_number:
                mal_id, episode_number = anime_mappings.get_mal_id_from_series(
                    mapping_data,
                    item.source,
                    item.media_id,
                    item.season_number,
                    item.episode_number,
                )
            if (
                not mal_id
                or not episode_number
                or episode_number < 1
                or mal_metadata(str(mal_id)) is None
            ):
                if not has_history:
                    # Not a tracked episode, so there is nothing to fix.
                    continue
                unmapped.append(f"S{item.season_number:02}E{item.episode_number:02}")
                unmapped_episodes.append({
                    "season": item.season_number,
                    "episode": item.episode_number,
                })
                season_count["unmapped"].append(item.episode_number)
                continue
            mal_id = str(mal_id)
            mapped_any = True
            if mal_id not in entries:
                metadata = mal_metadata(mal_id)
                entries[mal_id] = (
                    Anime(
                        user=user,
                        item=Item(
                            media_id=mal_id,
                            source=Sources.MAL.value,
                            media_type=MediaTypes.ANIME.value,
                            title=metadata["title"],
                        ),
                        score=score,
                        status=show.status,
                        progress=0,
                    ),
                    set(),
                    metadata.get("max_progress"),
                    show.status == Status.PLANNING.value and show.progress == 0,
                )
                entries[mal_id][0].grouped_tv = show
                entries[mal_id][0].grouped_seasons = set()
            # Another show mapped to the same entry adds its watches, but the
            # entry's seasons and rating stay with the show that made it:
            # its season numbers mean nothing on grouped_tv, and rating it
            # again would replace grouped_tv's rating with its own.
            if entries[mal_id][0].grouped_tv is show:
                entries[mal_id][0].grouped_seasons.add(item.season_number)
                show_mal_ids.add(mal_id)
            _, watched_numbers, _, _ = entries[mal_id]
            if watched:
                watched_numbers.add(episode_number)

        # A season split across cours feeds several entries; a rating pulled
        # for one cour may only land on seasons that feed that cour alone.
        season_feeds = Counter(
            season
            for mal_id in show_mal_ids
            for season in entries[mal_id][0].grouped_seasons
        )
        # The entry's rating: its lowest-numbered rated season, else the
        # show's - never whichever episode happened to create it.
        season_rows = dict(
            Season.objects.filter(
                related_tv=show, order_archived=False,
            ).values_list("item__season_number", "score"),
        )
        season_scores = {
            season: score for season, score in season_rows.items() if score is not None
        }
        for mal_id in show_mal_ids:
            entry = entries[mal_id][0]
            # Only a season with its own row can hold a rating; history made
            # of watch states alone has none.
            entry.grouped_exclusive_seasons = {
                season
                for season in entry.grouped_seasons
                if season_feeds[season] == 1 and season in season_rows
            }
            # A show feeding this entry alone can carry its rating without
            # passing it on to a sibling cour.
            entry.grouped_rates_show = len(show_mal_ids) == 1
            entry.score = next(
                (
                    season_scores[season]
                    for season in sorted(entry.grouped_seasons)
                    if season in season_scores
                ),
                show.score,
            )

        if mapping_issues is not None and (unmapped or not mapped_any):
            mapping_issues.append({
                "title": show.item.title,
                "media_type": "Anime",
                "mal_id": "",
                "item_id": show.item_id,
                "episodes": unmapped_episodes,
                "seasons": [
                    {
                        "season": season,
                        "episodes": sorted(values["unmapped"]),
                        "all_unmapped": bool(values["unmapped"])
                        and len(values["unmapped"]) == values["total"],
                    }
                    for season, values in sorted(season_counts.items())
                    if values["unmapped"]
                ],
                "outcome": "skipped",
                "reason": (
                    "No reliable MAL episode mapping for: " + ", ".join(sorted(set(unmapped)))
                    if unmapped else "No episode history available to resolve a MAL mapping."
                ),
            })

    result = []
    for media, watched_numbers, total, reset_progress in entries.values():
        media.progress = 0 if reset_progress else len(watched_numbers)
        if total:
            # An overlapping mapping can produce more distinct episode
            # numbers than the MAL entry actually has. MAL doesn't reject an
            # out-of-range count, it just silently keeps the old value,
            # which then surfaces as push_status()'s MALSyncMismatchError -
            # clamp before it ever reaches MAL instead.
            media.progress = min(media.progress, total)
        if total and media.progress >= total:
            media.status = Status.COMPLETED.value
        elif media.status not in {Status.DROPPED.value, Status.PAUSED.value}:
            media.status = (
                Status.IN_PROGRESS.value if media.progress else Status.PLANNING.value
            )
        result.append(media)
    return result


def manual_episode_mappings(user):
    """Return user-created grouped-anime MAL mappings, one row per season and MAL entry."""
    from integrations.models import ExternalReference, ExternalReferenceReviewStatus

    groups = {}
    references = ExternalReference.objects.filter(
        user=user,
        integration="mal_sync",
        external_namespace="grouped_anime_episode",
        review_status=ExternalReferenceReviewStatus.CORRECTED.value,
    ).order_by("metadata__series_title", "metadata__season_number", "metadata__episode_number")
    for reference in references:
        key = (
            reference.metadata.get("series_title", ""),
            reference.metadata.get("season_number"),
            reference.episode_mapping.get("mal_id"),
        )
        group = groups.setdefault(key, {
            "key": ":".join(str(part) for part in key),
            "title": key[0],
            "season": key[1],
            "mal_id": key[2],
            "reference_ids": [],
            "episodes": [],
            "mal_episodes": [],
        })
        group["reference_ids"].append(reference.pk)
        group["episodes"].append(reference.metadata.get("episode_number"))
        group["mal_episodes"].append(reference.episode_mapping.get("episode"))
    return list(groups.values())


def _included_statuses(mal_account):
    """Return the statuses the account's full-sync filters include."""
    included_statuses = set()
    if mal_account is None or mal_account.sync_filter_completed:
        included_statuses.add(Status.COMPLETED.value)
    if mal_account is None or mal_account.sync_filter_in_progress:
        included_statuses.add(Status.IN_PROGRESS.value)
    if mal_account is None or mal_account.sync_filter_dropped:
        included_statuses.add(Status.DROPPED.value)
    if mal_account is not None and mal_account.sync_filter_planning:
        included_statuses.add(Status.PLANNING.value)
    if mal_account is not None and mal_account.sync_filter_paused:
        included_statuses.add(Status.PAUSED.value)
    return included_statuses


def full_sync_entries(
    user, mal_account=None, mapping_issues=None, progress_callback=None,
):
    """Return every MAL-backed anime/manga entry eligible for a full sync.

    Applies the account's sync filters (which statuses to include, and
    whether to require a score) when a MALAccount is given. The automatic
    per-item push in integrations.tasks._mal_sync.sync_mal_status bypasses
    these filters and always pushes whatever a saved entry's status is.
    """
    Anime = apps.get_model(app_label="app", model_name="anime")  # noqa: N806
    Manga = apps.get_model(app_label="app", model_name="manga")  # noqa: N806

    included_statuses = _included_statuses(mal_account)
    filters = {
        "user": user,
        "item__source": Sources.MAL.value,
        "status__isnull": False,
    }
    entries = [
        *Anime.objects.filter(**filters).select_related("item"),
        *Manga.objects.filter(**filters).select_related("item"),
        *grouped_sync_entries(
            user,
            mapping_issues=mapping_issues,
            progress_callback=progress_callback,
        ),
    ]
    # A rewatch is its own completed row (see integrations.imports.mal), so the
    # same MAL id can legitimately have several rows at once. Pushing an
    # arbitrary one risks reporting a lower progress than the show's best
    # recorded watch; always prefer the highest.
    by_key = {}
    for media in entries:
        key = (media.item.media_type, media.item.media_id)
        current = by_key.get(key)
        if current is None or (media.progress or 0) > (current.progress or 0):
            by_key[key] = media
    # Filter after choosing the row that speaks for the MAL entry, as the
    # per-item push and the pulls do: filtering first would let a rewatch
    # row push over a completed watch whose status is filtered out.
    entries = [
        media for media in by_key.values() if media.status in included_statuses
    ]
    if mal_account is not None and mal_account.sync_filter_rated_only:
        entries = [media for media in entries if media.score is not None]
    return entries


def _fetch_list_statuses(media_type, mal_account):
    """Return MAL list statuses keyed by media ID for one media type."""
    access_token = get_valid_access_token(mal_account)
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"fields": "list_status", "limit": 1000, "nsfw": "true"}
    url = f"{API_BASE_URL}/users/@me/{media_type}list"
    statuses = {}

    while url:
        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                url,
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            status_code = getattr(error.response, "status_code", None)
            if status_code in {
                requests.codes.unauthorized,
                requests.codes.forbidden,
            }:
                msg = "MyAnimeList rejected the request. Please reconnect your account."
                raise MALAuthError(msg) from error
            raise services.ProviderAPIError(Sources.MAL.value, error) from error

        for entry in response.get("data", []):
            statuses[str(entry["node"]["id"])] = entry.get("list_status", {})
        url = response.get("paging", {}).get("next")
        params = None

    return statuses


def _local_pull_updates(media_type, media, current, mal_account):
    """Return local field updates that should be adopted from MAL before pushing.

    Progress (and the status that follows it) is only pulled when the account
    allows it, and never for a grouped show: its progress is its episode
    history, which can't be raised without inventing watches. The preview and
    the full sync both use this, so the preview shows only what a sync applies.
    """
    if not current:
        return {}

    updates = {}
    _sent_field, response_field = PROGRESS_RESPONSE_FIELDS[media_type]
    remote_progress = current.get(response_field)
    mapped_status = MAL_STATUS_TO_FLOPPY[media_type].get(current.get("status"))
    local_progress = media.progress or 0
    pull_progress = (
        mal_account.pull_higher_progress_enabled
        and getattr(media, "grouped_tv", None) is None
    )
    if (
        pull_progress
        and isinstance(remote_progress, int)
        and remote_progress > local_progress
    ):
        updates["progress"] = remote_progress
        if mapped_status == Status.PLANNING.value:
            if media.status != Status.IN_PROGRESS.value:
                updates["status"] = Status.IN_PROGRESS.value
        elif mapped_status and mapped_status != media.status:
            updates["status"] = mapped_status
    elif (
        pull_progress
        and local_progress == 0
        and mapped_status in ZERO_PROGRESS_STATUS_PULLS
        and mapped_status != media.status
    ):
        updates["status"] = mapped_status

    # A grouped cour's rating only lands on seasons that feed it alone, or on
    # a show with no other cour (_set_grouped_rating), so without either
    # there's nothing to offer.
    rating_target = (
        getattr(media, "grouped_tv", None) is None
        or getattr(media, "grouped_exclusive_seasons", None)
        or getattr(media, "grouped_rates_show", False)
    )
    if mal_account.pull_ratings_enabled and media.score is None and rating_target:
        remote_score = current.get("score")
        if isinstance(remote_score, int) and remote_score > 0:
            updates["score"] = remote_score
    return updates


def _set_grouped_rating(media, score):
    """Rate the seasons that feed only this cour; return whether any were set.

    The show's rating is every unrated cour's fallback, so a rating that
    belongs to one MAL entry goes on that entry's own seasons instead. A
    show that feeds no other entry and has no season row to rate takes it.
    """
    from app.models import Season

    exclusive_seasons = getattr(media, "grouped_exclusive_seasons", ())
    seasons = Season.objects.filter(
        related_tv=media.grouped_tv,
        order_archived=False,
        item__season_number__in=exclusive_seasons,
    ).exclude(score=score)
    changed = False
    for season in seasons:
        season.score = score
        season.save(update_fields=["score"])
        changed = True
    show = media.grouped_tv
    if (
        not exclusive_seasons
        and getattr(media, "grouped_rates_show", False)
        and show.score != score
    ):
        show.score = score
        show.save(update_fields=["score"])
        changed = True
    media.score = score
    return changed


def pull_grouped_rating(media, current, mal_account):
    """Adopt MAL's rating for a grouped cour that has none, as the preview offers.

    `pull_higher_mal_progress` only covers flat rows; a grouped entry is a
    transient per-cour projection, so its rating lands on the seasons that
    feed that cour alone - never on the show, which would pass it on to
    sibling cours with different MAL ratings.
    """
    if getattr(media, "grouped_tv", None) is None:
        return
    updates = _local_pull_updates(media.item.media_type, media, current, mal_account)
    if "score" in updates:
        _set_grouped_rating(media, updates["score"])


def pull_higher_mal_progress(user, mal_account, remote_statuses=None, only=None):
    """Adopt MAL's progress locally wherever it is ahead of Floppy's own record.

    Full sync only ever pushed Floppy's state outward, so a higher count
    already on MyAnimeList (e.g. logged through another client) was silently
    overwritten by Floppy's lower value on the next run. Only flat, saved
    Anime/Manga rows are corrected here - grouped anime progress is derived
    from episode history and can't be raised without fabricating watches, so
    it is left to the existing mapping/episode tooling instead.

    Also adopts a MAL rating when Floppy has none recorded and
    mal_account.pull_ratings_enabled is on - never overwrites an existing
    Floppy rating, since that one is user-entered and MAL's isn't
    authoritative over it.

    `only` limits the corrections to those `entry_key` values (None means all).

    Returns the list of corrected rows for reporting.
    """
    from app.models import Anime, Manga

    if remote_statuses is None:
        remote_statuses = {
            media_type: _fetch_list_statuses(media_type, mal_account)
            for media_type in (MediaTypes.ANIME.value, MediaTypes.MANGA.value)
        }

    # The same filters as full_sync_entries, so a sync never changes an
    # entry locally that it neither pushes nor shows in the preview.
    included_statuses = _included_statuses(mal_account)
    corrected = []
    for media_type, model in (
        (MediaTypes.ANIME.value, Anime),
        (MediaTypes.MANGA.value, Manga),
    ):
        remote = remote_statuses.get(media_type) or {}
        rows = model.objects.filter(
            user=user,
            item__source=Sources.MAL.value,
            status__isnull=False,
        ).select_related("item")
        # A rewatch is its own row; only the row full sync would push speaks
        # for the MAL entry, so an in-progress rewatch is never overwritten.
        best_rows = {}
        for media in rows:
            current_best = best_rows.get(media.item_id)
            if current_best is None or (media.progress or 0) > (
                current_best.progress or 0
            ):
                best_rows[media.item_id] = media
        for media in best_rows.values():
            if only is not None and entry_key(media) not in only:
                continue
            if media.status not in included_statuses or (
                mal_account.sync_filter_rated_only and media.score is None
            ):
                continue
            current = remote.get(str(media.item.media_id))
            updates = _local_pull_updates(media_type, media, current, mal_account)
            if updates:
                for field, value in updates.items():
                    setattr(media, field, value)
                update_fields = list(updates)
                media.save(update_fields=update_fields)
                corrected.append(media)
    return corrected


def reconcile_stale_full_sync(mal_account):
    """Mark a full sync FAILED if its heartbeat went quiet (e.g. a container restart).

    Without this, a queued/running row left behind by a killed worker blocks
    both the "already in progress" guard on starting a new sync and the retry
    task's reclaim window, with nothing left to ever move it out of that
    state. Uses a conditional update to avoid a race with another request
    reconciling (or a real task completing) at the same time.
    """
    if not mal_account.full_sync_is_stale:
        return mal_account

    from integrations.models import STALE_FULL_SYNC_AGE, MALFullSyncStatus

    stale_before = timezone.now() - STALE_FULL_SYNC_AGE
    updated = type(mal_account).objects.filter(
        pk=mal_account.pk,
        full_sync_status__in=[MALFullSyncStatus.QUEUED, MALFullSyncStatus.RUNNING],
        updated_at__lt=stale_before,
    ).update(
        full_sync_status=MALFullSyncStatus.FAILED,
        full_sync_completed_at=timezone.now(),
        last_error_message=(
            "The last MyAnimeList sync did not finish (the app may have "
            "restarted mid-sync). Start it again."
        )[:500],
        last_failed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    if updated:
        mal_account.refresh_from_db()
    return mal_account


def retryable_failures(mal_account):
    """Return the failed report rows "Retry failed" can act on.

    A sync that stopped before its entries (lists or mappings failed to
    load) records a single row with no MAL entry; only a new full sync
    helps there.
    """
    return [
        result for result in mal_account.full_sync_results
        if result.get("outcome") == "failed" and result.get("mal_id")
    ]


def full_sync_report(mal_account):
    """Return the persisted report used by both page loads and live polling."""
    return {
        "status": mal_account.full_sync_status,
        "status_label": mal_account.get_full_sync_status_display(),
        "is_active": mal_account.full_sync_is_active,
        "total": mal_account.full_sync_total,
        "processed": mal_account.full_sync_processed,
        "succeeded": mal_account.full_sync_succeeded,
        "failed": mal_account.full_sync_failed,
        "retryable": len(retryable_failures(mal_account)),
        "results": mal_account.full_sync_results,
        "mapping_issues": [
            result for result in mal_account.full_sync_results
            if result.get("outcome") == "skipped"
        ],
        "started_at": mal_account.full_sync_started_at,
        "completed_at": mal_account.full_sync_completed_at,
    }


def entry_key(media):
    """Return the key a preview row and a selective full sync share for an entry."""
    return f"{media.item.media_type}:{media.item.media_id}"


def _detail_link(media):
    """Return `media_url` input for a sync entry's page (its grouped show when it has one).

    The URL itself is built by the web request: this runs in a Celery worker,
    whose URLconf is empty, so `reverse()` can't resolve pages here.
    """
    show = getattr(media, "grouped_tv", None)
    if show is not None:
        return {
            "media_type": MediaTypes.TV.value,
            "route_media_type": MediaTypes.ANIME.value,
            "source": show.item.source,
            "media_id": show.item.media_id,
            "title": show.item.title,
        }
    return {
        "media_type": media.item.media_type,
        "source": media.item.source,
        "media_id": media.item.media_id,
        "title": media.item.title,
    }


def pull_mal_entry(media, current, list_error=None):
    """Overwrite Floppy's values for one entry with its MyAnimeList list status.

    Raises list_error, when loading the MyAnimeList list failed, and
    MALSyncMismatchError when the entry isn't on the list. A grouped cour
    only takes the score, on the seasons that feed it alone: its progress is
    its episode history, and one MAL cour's status isn't the show's - saving
    Completed on the show would mark every season watched, inventing watches.
    """
    if list_error is not None:
        raise list_error
    if not current:
        msg = "Not on your MyAnimeList list, so there was nothing to pull."
        raise MALSyncMismatchError(msg)
    media_type = media.item.media_type
    remote_score = current.get("score")
    values = {
        "status": MAL_STATUS_TO_FLOPPY[media_type].get(current.get("status")),
        "score": remote_score if isinstance(remote_score, int) and remote_score > 0 else None,
    }
    if values["status"] is None:
        del values["status"]
    if getattr(media, "grouped_tv", None) is not None:
        _set_grouped_rating(media, values["score"])
        return
    remote_progress = current.get(PROGRESS_RESPONSE_FIELDS[media_type][1])
    if isinstance(remote_progress, int):
        values["progress"] = remote_progress
    for field, value in values.items():
        setattr(media, field, value)
    media.save(update_fields=list(values))


def preview_full_sync(user, mal_account, mapping_issues=None, progress_callback=None):
    """Return field-level changes a full sync would make without writing to MAL."""
    if progress_callback:
        progress_callback(5, "Loading your MyAnimeList anime list")
    remote_statuses = {
        MediaTypes.ANIME.value: _fetch_list_statuses(MediaTypes.ANIME.value, mal_account),
    }
    if progress_callback:
        progress_callback(20, "Loading your MyAnimeList manga list")
    remote_statuses[MediaTypes.MANGA.value] = _fetch_list_statuses(
        MediaTypes.MANGA.value, mal_account,
    )
    field_labels = {
        "status": "Status",
        "num_watched_episodes": "Episodes watched",
        "num_chapters_read": "Chapters read",
        "score": "Score",
    }
    preview = []

    def grouped_progress(current, total, title):
        if progress_callback:
            progress_callback(
                30 + round(current / max(total, 1) * 55),
                f"Checking anime mappings: {title}",
            )

    entries = full_sync_entries(
        user,
        mal_account,
        mapping_issues=mapping_issues,
        progress_callback=grouped_progress,
    )
    if progress_callback:
        progress_callback(90, "Comparing local and MyAnimeList entries")
    for media in entries:
        media_type = media.item.media_type
        desired = status_payload(media, mal_account)
        current = remote_statuses[media_type].get(str(media.item.media_id))
        changes = []
        local_updates = _local_pull_updates(media_type, media, current, mal_account)
        for field, new_value in local_updates.items():
            changes.append(
                {
                    "target": "floppy",
                    "target_label": "Floppy",
                    "field": (
                        field_labels[PROGRESS_RESPONSE_FIELDS[media_type][0]]
                        if field == "progress" else field_labels[field]
                    ),
                    "from": getattr(media, field),
                    "to": new_value,
                }
            )
            if field == "status":
                status_map = (
                    ANIME_STATUS_TO_MAL
                    if media_type == MediaTypes.ANIME.value
                    else MANGA_STATUS_TO_MAL
                )
                desired["status"] = status_map[new_value]
            elif field == "progress":
                desired[PROGRESS_RESPONSE_FIELDS[media_type][0]] = new_value
            elif field == "score" and mal_account.sync_ratings_enabled:
                desired["score"] = mal_score(new_value)
        for field, new_value in desired.items():
            remote_field = (
                PROGRESS_RESPONSE_FIELDS[media_type][1]
                if field == PROGRESS_RESPONSE_FIELDS[media_type][0]
                else field
            )
            old_value = current.get(remote_field) if current is not None else None
            if old_value != new_value:
                changes.append(
                    {
                        "target": "mal",
                        "target_label": "MyAnimeList",
                        "field": field_labels[field],
                        "from": old_value,
                        "to": new_value,
                    }
                )
        changes.sort(
            key=lambda change: (
                change["target"] != "floppy",
                PREVIEW_FIELD_SORT_ORDER.get(change["field"], 99),
                change["field"],
            )
        )

        if changes:
            preview.append(
                {
                    "key": entry_key(media),
                    "title": media.item.title,
                    "link": _detail_link(media),
                    "media_type": media_type.title(),
                    "mal_id": str(media.item.media_id),
                    "not_on_list": current is None,
                    "changes": changes,
                }
            )

    preview.sort(
        key=lambda entry: (
            entry["changes"][0]["target"] != "floppy",
            entry["title"].casefold(),
        )
    )
    if progress_callback:
        progress_callback(100, "Preview ready")
    return preview


def push_status(media, mal_account, clear_score=False):
    """Push a MAL-backed Anime/Manga entry's status, progress and score to MAL.

    Args:
        media: A saved Anime or Manga instance whose item.source is MAL.
        mal_account: The user's MALAccount to push through.
        clear_score: The rating was just removed in Floppy; send 0, which
            clears it on MAL. An entry that was simply never rated in Floppy
            leaves MAL's rating alone.
    """
    media_type = media.item.media_type
    data = status_payload(media, mal_account)
    if clear_score and media.score is None and mal_account.sync_ratings_enabled:
        data["score"] = 0

    access_token = get_valid_access_token(mal_account)
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{API_BASE_URL}/{media_type}/{media.item.media_id}/my_list_status"

    try:
        response = services.api_request(
            Sources.MAL.value,
            "PUT",
            url,
            data=data,
            headers=headers,
        )
    except requests.exceptions.HTTPError as error:
        status_code = getattr(error.response, "status_code", None)
        if status_code in {requests.codes.unauthorized, requests.codes.forbidden}:
            msg = "MyAnimeList rejected the request. Please reconnect your account."
            raise MALAuthError(msg) from error
        raise services.ProviderAPIError(Sources.MAL.value, error) from error

    sent_field, response_field = PROGRESS_RESPONSE_FIELDS[media_type]
    mismatches = [
        field
        for field, value in data.items()
        if response.get(response_field if field == sent_field else field) != value
    ]
    if mismatches:
        msg = (
            f"MyAnimeList accepted the update for {media.item.title} (MAL ID "
            f"{media.item.media_id}) but its response shows it wasn't applied "
            f"({', '.join(mismatches)})."
        )
        raise MALSyncMismatchError(msg)

    logger.info(
        "Synced %s (MAL ID %s) to MyAnimeList for user %s",
        media.item.title,
        media.item.media_id,
        mal_account.user,
    )
