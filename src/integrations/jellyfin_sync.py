"""Push Floppy watched/unwatched state to a Jellyfin server."""

import logging
from collections import defaultdict

from app.models import Episode, Movie, Sources, Status
from integrations.imports.helpers import MediaImportError, decrypt_or_raise
from integrations.jellyfin_client import (
    JellyfinAuthError,
    JellyfinClient,
    JellyfinClientError,
)

logger = logging.getLogger(__name__)

JELLYFIN_PUSH_INTERVAL_MINUTES = 60
JELLYFIN_PUSH_TASK_NAME = "Push Watched State to Jellyfin"
MAX_UNMATCHED_TITLES_LOGGED = 20

_PROVIDER_FIELD_ORDER = ("Tmdb", "Imdb", "Tvdb")

_SOURCE_TO_JELLYFIN_PROVIDER = {
    Sources.TMDB.value: "Tmdb",
    Sources.TVDB.value: "Tvdb",
    Sources.IMDB.value: "Imdb",
}


def format_jellyfin_push_message(push_counts: dict, warning_text: str = "") -> str:
    """Format a human-readable result message for a Jellyfin push sync run."""
    parts = []

    marked_played = push_counts.get("marked_played", 0)
    if marked_played:
        parts.append(f"marked {marked_played} watched")

    marked_unplayed = push_counts.get("marked_unplayed", 0)
    if marked_unplayed:
        parts.append(f"marked {marked_unplayed} unwatched")

    skipped_no_match = push_counts.get("skipped_no_match", 0)
    if skipped_no_match:
        parts.append(f"{skipped_no_match} had no Jellyfin match")

    skipped_unsupported = push_counts.get("skipped_unsupported_source", 0)
    if skipped_unsupported:
        parts.append(f"{skipped_unsupported} use an unsupported metadata source")

    if not parts:
        message = "Nothing to sync. Jellyfin already matched Floppy's watched state."
    else:
        message = "Jellyfin sync: " + ", ".join(parts) + "."

    if warning_text:
        message = f"{message}\n{warning_text}"

    return message


def _provider_keys(provider_ids: dict) -> set[tuple[str, str]]:
    """Return (provider, value) pairs present on a Jellyfin item."""
    keys = set()
    for provider in _PROVIDER_FIELD_ORDER:
        value = provider_ids.get(provider)
        if value:
            keys.add((provider, str(value)))
    return keys


class JellyfinPushSyncService:
    """Push the user's watched/unwatched Movie and Episode state to Jellyfin."""

    def __init__(self, user, account):
        """Store the extra keyword arguments this form needs."""
        self.user = user
        self.account = account
        self.counts = defaultdict(int)
        self.warnings: list[str] = []
        self.unmatched_titles: set[str] = set()

    def sync(self) -> tuple[dict[str, int], str]:
        """Push watched state and return counts plus a warning message."""
        if not self.account or not self.account.is_connected:
            msg = "Jellyfin is not connected for this user."
            raise MediaImportError(msg)

        self.counts = defaultdict(int)
        self.warnings = []
        self.unmatched_titles = set()
        client = self._build_client()

        movie_index, episode_index = self._build_indexes(client)

        if self.account.push_watched_enabled or self.account.push_unwatched_enabled:
            self._push_movies(client, movie_index)
            self._push_episodes(client, episode_index)

        self._log_unmatched_titles()
        return dict(self.counts), "\n".join(dict.fromkeys(self.warnings))

    def _build_client(self) -> JellyfinClient:
        api_key = decrypt_or_raise(self.account.api_key)
        client = JellyfinClient(
            self.account.base_url, api_key, self.account.jellyfin_user_id or None
        )

        if not self.account.jellyfin_user_id:
            try:
                current_user = client.get_current_user()
            except (JellyfinAuthError, JellyfinClientError) as exc:
                msg = f"Could not connect to Jellyfin: {exc}"
                raise MediaImportError(msg) from exc
            if not current_user or not current_user.get("Id"):
                msg = "Could not resolve a Jellyfin user for this API key."
                raise MediaImportError(
                    msg,
                )
            client.user_id = current_user["Id"]
            self.account.jellyfin_user_id = current_user["Id"]
            self.account.jellyfin_username = current_user.get("Name", "")
            self.account.save(update_fields=["jellyfin_user_id", "jellyfin_username"])

        return client

    def _build_indexes(self, client: JellyfinClient):
        """Build lookup indexes of Jellyfin library items keyed by provider id.

        Jellyfin Episode items carry episode-level provider ids (e.g. the TVDB
        episode id), while Floppy matches on the series-level id, so episodes
        are keyed by their parent Series item's provider ids instead.
        """
        movie_index: dict[tuple[str, str], dict] = {}
        episode_index: dict[tuple[str, str, int, int], dict] = {}
        series_provider_keys: dict[str, set[tuple[str, str]]] = {}
        episodes: list[dict] = []
        total_items = 0
        movie_count = 0

        try:
            for jf_item in client.iter_library_items():
                total_items += 1
                provider_ids = jf_item.get("ProviderIds") or {}
                item_type = jf_item.get("Type")

                if item_type == "Movie":
                    movie_count += 1
                    for key in _provider_keys(provider_ids):
                        movie_index[key] = jf_item
                elif item_type == "Series":
                    series_id = jf_item.get("Id")
                    if series_id:
                        series_provider_keys[series_id] = _provider_keys(provider_ids)
                elif item_type == "Episode":
                    episodes.append(jf_item)
        except (JellyfinAuthError, JellyfinClientError) as exc:
            msg = f"Could not read Jellyfin library: {exc}"
            raise MediaImportError(msg) from exc

        indexed_episode_count = 0
        for jf_item in episodes:
            season_number = jf_item.get("ParentIndexNumber")
            episode_number = jf_item.get("IndexNumber")
            if season_number is None or episode_number is None:
                continue
            series_keys = series_provider_keys.get(jf_item.get("SeriesId"))
            if not series_keys:
                continue
            indexed_episode_count += 1
            for provider, value in series_keys:
                episode_index[(provider, value, season_number, episode_number)] = (
                    jf_item
                )

        logger.info(
            "Indexed %d Jellyfin movies, %d episodes (%d series) for push sync",
            movie_count,
            indexed_episode_count,
            len(series_provider_keys),
        )
        if total_items == 0:
            self.warnings.append(
                "Jellyfin returned no library items. Check that the API key "
                "and user have library access.",
            )

        return movie_index, episode_index

    def _lookup(self, index: dict, keys) -> dict | None:
        for key in keys:
            match = index.get(key)
            if match:
                return match
        return None

    def _record_no_match(self, item, *, title: str | None = None) -> None:
        """Record a missing item and retain a bounded diagnostic sample."""
        self.counts["skipped_no_match"] += 1
        provider = _SOURCE_TO_JELLYFIN_PROVIDER[item.source]
        title = title or item.title or "Untitled"
        self.unmatched_titles.add(f"{title} [{provider}:{item.media_id}]")

    def _log_unmatched_titles(self) -> None:
        """Log a small, stable sample of distinct titles without flooding logs."""
        if not self.unmatched_titles:
            return

        titles = sorted(self.unmatched_titles)
        sample = titles[:MAX_UNMATCHED_TITLES_LOGGED]
        remainder = len(titles) - len(sample)
        suffix = f" (+{remainder} more)" if remainder else ""
        logger.info(
            "Jellyfin push sync found no matches for %d distinct titles; "
            "showing %d: %s%s",
            len(titles),
            len(sample),
            ", ".join(sample),
            suffix,
        )

    def _push_movies(self, client: JellyfinClient, movie_index: dict) -> None:
        movies = Movie.objects.filter(user=self.user).select_related("item")
        for movie in movies:
            item = movie.item
            provider = _SOURCE_TO_JELLYFIN_PROVIDER.get(item.source)
            if not provider:
                self.counts["skipped_unsupported_source"] += 1
                continue

            keys = [(provider, item.media_id)]
            jf_item = self._lookup(movie_index, keys)
            if not jf_item:
                self._record_no_match(item)
                continue

            self._apply_state(
                client,
                jf_item,
                watched=movie.status == Status.COMPLETED.value,
            )

    def _push_episodes(self, client: JellyfinClient, episode_index: dict) -> None:
        episodes = Episode.objects.filter(
            related_season__user=self.user,
        ).select_related("item", "related_season__related_tv__item")

        # One decision per episode, not per play: an open rewatch must not
        # unmark an episode an earlier finished play watched.
        by_item = {}
        for episode in episodes:
            first, watched = by_item.get(episode.item_id, (episode, False))
            by_item[episode.item_id] = (first, watched or Episode.is_watch(episode))

        for episode, watched in by_item.values():
            item = episode.item
            if not item:
                continue
            provider = _SOURCE_TO_JELLYFIN_PROVIDER.get(item.source)
            if not provider:
                self.counts["skipped_unsupported_source"] += 1
                continue
            if item.season_number is None or item.episode_number is None:
                continue

            keys = [(provider, item.media_id, item.season_number, item.episode_number)]
            jf_item = self._lookup(episode_index, keys)
            if not jf_item:
                self._record_no_match(
                    item,
                    title=episode.related_season.related_tv.item.title,
                )
                continue

            self._apply_state(client, jf_item, watched=watched)

    def _apply_state(
        self, client: JellyfinClient, jf_item: dict, *, watched: bool
    ) -> None:
        currently_played = bool((jf_item.get("UserData") or {}).get("Played"))
        jellyfin_id = jf_item.get("Id")
        if not jellyfin_id:
            return

        if watched and self.account.push_watched_enabled and not currently_played:
            client.mark_played(jellyfin_id)
            self.counts["marked_played"] += 1
        elif not watched and self.account.push_unwatched_enabled and currently_played:
            client.mark_unplayed(jellyfin_id)
            self.counts["marked_unplayed"] += 1
        else:
            self.counts["skipped_already_synced"] += 1
