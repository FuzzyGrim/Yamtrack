import logging

from django.apps import apps
from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models, transaction
from django.utils import timezone
from model_utils import FieldTracker
from model_utils.fields import MonitorField
from requests import RequestException
from simple_history.models import HistoricalRecords

import app
from app import providers
from app.models.choices import MediaTypes, Sources, Status
from app.models.item import Item
from app.models.manager import MediaManager
from app.services.sync_lock import serialized_state_write
from integrations.import_scope import ImportScopedManager, ImportScopedQuerySet

logger = logging.getLogger(__name__)


class ScoreMonitorField(MonitorField):
    """When ``score`` was last set, changed or cleared (issue #1280).

    Unlike a plain MonitorField it stays null until the entry has a score, and
    a row inserted with a score (``save()`` or ``bulk_create``) is stamped.
    ``queryset.update(score=...)`` bypasses it, so those callers set
    ``scored_at`` themselves.
    """

    def pre_save(self, model_instance, add):
        """Stamp a scored insert or a score change; keep a timestamp already set."""
        if add:
            # An insert keeps a caller-supplied timestamp (a copied rating).
            if (
                model_instance.score is not None
                and getattr(model_instance, self.attname) is None
            ):
                setattr(model_instance, self.attname, timezone.now())
            self._save_initial(model_instance.__class__, model_instance)
            return models.DateTimeField.pre_save(self, model_instance, add)
        if not hasattr(model_instance, self.monitor_attname):
            # The score was deferred when the row was loaded, so there is no
            # initial value to compare against: leave the timestamp alone.
            return models.DateTimeField.pre_save(self, model_instance, add)
        return super().pre_save(model_instance, add)

# Sentinel values on Item.runtime_minutes: 999998 means "aired but runtime
# unknown", 999999 means "runtime completely unknown / failed lookup"
# (see app.models.episode_runtimes.EXCLUDED_RUNTIME_SENTINELS).
RUNTIME_UNKNOWN_AIRED = 999998
RUNTIME_UNKNOWN_FAILED = 999999


class OwnerEndDateHistory(HistoricalRecords):
    """History table indexed for the History page's per-user end_date query.

    Only music and podcasts read their history table directly by
    ``history_user`` and ``end_date``; other media types keep the default.
    """

    def get_meta_options(self, model):
        """Add the (history_user, end_date) index for music and podcasts."""
        meta = super().get_meta_options(model)
        if model.__name__ in ("Music", "Podcast"):
            meta["indexes"] = (
                *meta.get("indexes", ()),
                models.Index(
                    fields=["history_user", "end_date"],
                    name=f"app_hist{model.__name__.lower()}_user_end_idx",
                ),
            )
        return meta


class Media(models.Model):
    """Abstract model for all media types."""

    history = OwnerEndDateHistory(
        cascade_delete_history=True,
        inherit=True,
        excluded_fields=[
            "item",
            "progressed_at",
            "scored_at",
            "user",
            "related_tv",
            "created_at",
            "updated_at",
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    # Moves on any change to the row (status, score, progress, dates, notes, ...)
    # so API clients can ask for just what changed (`updated_since`). Bulk
    # writers are stamped by ImportScopedQuerySet; list and tag membership by
    # the signals in app.library_changes.
    updated_at = models.DateTimeField(auto_now=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    import_run = models.ForeignKey(
        "integrations.ImportRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The import run that created or last touched this row, if any.",
    )
    score = models.DecimalField(
        null=True,
        blank=True,
        max_digits=3,
        decimal_places=1,
        validators=[
            DecimalValidator(3, 1),
            MinValueValidator(0),
            MaxValueValidator(10),
        ],
    )
    scored_at = ScoreMonitorField(monitor="score", null=True, blank=True)
    progress = models.PositiveIntegerField(default=0)
    progressed_at = MonitorField(monitor="progress")
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.COMPLETED.value,
        # Null means the user has data about this media (typically an imported
        # rating) without tracking it. Statusless media is kept out of the media
        # lists and only surfaces under the "No Status" filter.
        null=True,
        blank=True,
    )
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    # How this row was created: a short free-text label. Built-in writers use
    # conventional values ("plex", "jellyfin", "trakt", "manual", …); the user
    # can override it with any text (e.g. "Theatre") from the edit UI. Named
    # distinctly from `Item.source` (the metadata provider) to avoid colliding
    # with it in the generic CSV export/import column mapping (both would
    # otherwise share the header name "source").
    entry_source = models.CharField(max_length=50, blank=True, default="")
    objects = ImportScopedManager()

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["user", "item", "-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(
                fields=["user", "updated_at"],
                name="app_%(class)s_usr_upd_idx",
            ),
            # Discover's candidate queries filter user+status(+score) and order
            # by recency (end_date, progressed_at, created_at) with a LIMIT.
            models.Index(
                fields=["user", "status", "-end_date", "-progressed_at", "-created_at"],
                name="app_%(class)s_usr_act_idx",
            ),
            models.Index(
                fields=[
                    "user",
                    "status",
                    "-score",
                    "-end_date",
                    "-progressed_at",
                    "-created_at",
                ],
                name="app_%(class)s_usr_scr_idx",
            ),
            # History day/month queries filter user and (end_date in range, or
            # end_date NULL and start_date in range) without a status.
            models.Index(
                fields=["user", "end_date", "start_date"],
                name="app_%(class)s_usr_dates_idx",
            ),
        ]

    def __str__(self):
        """Return the title of the media."""
        return self.item.__str__()

    @serialized_state_write
    def save(self, *args, **kwargs):
        """Save the media instance."""
        from app.services.completion import (
            finalize_completed_entry,
            prepare_completed_entry,
        )

        if not getattr(self, "_history_user", None) and getattr(self, "user_id", None):
            self._history_user = self.user

        if self.tracker.has_changed("progress"):
            self.process_progress()

        if self.tracker.has_changed("status"):
            self.process_status()

        self._include_updated_at(kwargs)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "score" in update_fields:
            kwargs["update_fields"] = (*update_fields, "scored_at")

        planning_entries, merged_fields = prepare_completed_entry(self)
        if merged_fields and kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = tuple(
                dict.fromkeys((*kwargs["update_fields"], *merged_fields)),
            )

        if planning_entries:
            with transaction.atomic():
                super().save(*args, **kwargs)
                finalize_completed_entry(planning_entries)
        else:
            super().save(*args, **kwargs)

    @serialized_state_write
    def delete(self, *args, **kwargs):
        """Delete inside the same per-user barrier as a save.

        A cascade (a season's episodes) locks the user's sequence row once here
        instead of once per deleted row in the delete signals.
        """
        return super().delete(*args, **kwargs)

    def _include_updated_at(self, kwargs):
        """Make a partial save (``update_fields``) move ``updated_at`` too."""
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "updated_at" not in update_fields:
            kwargs["update_fields"] = (*update_fields, "updated_at")

    def _sync_relevant_fields_changed(self):
        """Return whether status, progress or score changed in this save()."""
        return (
            self.tracker.has_changed("status")
            or self.tracker.has_changed("progress")
            or self.tracker.has_changed("score")
        )

    def _score_cleared(self):
        """Return whether this save() removes a rating that was set."""
        return (
            self.score is None
            and self.tracker.has_changed("score")
            and self.tracker.previous("score") is not None
        )

    def _queue_mal_sync(self, media_type, clear_score=False):
        """Queue an async push of this entry's status to MyAnimeList, if applicable.

        No-ops for entries not sourced from MAL (e.g. AniList-backed anime or
        MangaUpdates-backed manga) and for users without an active per-item
        MAL connection. The push waits for the save to commit, so the worker
        never reads the previous state. Bulk imports bypass save() entirely, so
        this only fires for interactive edits and webhook-driven updates -
        never as a side effect of importing.
        """
        if self.item.source != Sources.MAL.value:
            return

        from integrations.mal_sync import per_item_sync_active
        from integrations.tasks import sync_mal_status

        if not per_item_sync_active(self.user_id):
            return

        def send():
            try:
                sync_mal_status.delay(
                    media_type=media_type, media_id=self.pk, clear_score=clear_score,
                )
            # The broker is down; the save already committed, so never raise.
            except Exception as error:
                logger.warning(
                    "mal_sync_enqueue_failed media_type=%s media_id=%s error=%s",
                    media_type,
                    self.pk,
                    error,
                )

        transaction.on_commit(send)

    def _get_local_max_progress(self):
        """Return locally-derived length for music, podcasts, and videos without provider calls."""
        if self.item.media_type == MediaTypes.PODCAST.value:
            return self.item.runtime_minutes

        if self.item.media_type == MediaTypes.VIDEO.value:
            length_seconds = getattr(self, "length_seconds", None)
            return length_seconds or None

        # Audiobooks track progress in minutes, so their total is the runtime.
        if (
            self.item.media_type == MediaTypes.BOOK.value
            and self.item.format == "audiobook"
        ):
            return self.item.book_max_progress

        if self.item.media_type != MediaTypes.MUSIC.value:
            return None

        track = getattr(self, "track", None)
        if track and track.duration_ms:
            return track.duration_ms // 60000

        if self.item.runtime_minutes:
            return self.item.runtime_minutes

        album_id = getattr(self, "album_id", None)
        if album_id and self.item.media_id:
            Track = apps.get_model("app", "Track")
            match = Track.objects.filter(
                album_id=album_id,
                musicbrainz_recording_id=self.item.media_id,
                duration_ms__isnull=False,
            ).first()
            if match and match.duration_ms:
                return match.duration_ms // 60000

        return None

    def process_progress(self):
        """Update fields depending on the progress of the media."""
        if self.progress < 0:
            self.progress = 0
        elif self.status == Status.IN_PROGRESS.value:
            # Music and board games are play-count based; podcasts and
            # audiobooks use local runtime data.
            if self.item.media_type in (
                MediaTypes.PODCAST.value,
                MediaTypes.MUSIC.value,
                MediaTypes.BOARDGAME.value,
                MediaTypes.VIDEO.value,
            ) or (
                self.item.media_type == MediaTypes.BOOK.value
                and self.item.format == "audiobook"
            ):
                max_progress = self._get_local_max_progress()
            else:
                try:
                    max_progress = providers.services.get_media_metadata(
                        self.item.media_type,
                        self.item.media_id,
                        self.item.source,
                    )["max_progress"]
                except (
                    providers.services.ProviderAPIError,
                    RequestException,
                    ValueError,
                ):
                    logger.warning(
                        "Unable to fetch max progress for %s (%s/%s)",
                        self.item.media_type,
                        self.item.source,
                        self.item.media_id,
                    )
                    max_progress = None

            if max_progress:
                self.progress = min(self.progress, max_progress)

                if self.progress == max_progress:
                    self.status = Status.COMPLETED.value

                    # For podcasts, don't set end_date here - it's calculated from published date + duration in import
                    # For other media types, set end_date if not already set
                    if (
                        self.item.media_type != MediaTypes.PODCAST.value
                        and not self.end_date
                    ):
                        self.end_date = timezone.now()

    def process_status(self):
        """Update fields depending on the status of the media."""
        if self.status == Status.COMPLETED.value:
            # Music and board game progress are play-count based; don't overwrite on status changes.
            if self.item.media_type in (
                MediaTypes.MUSIC.value,
                MediaTypes.BOARDGAME.value,
            ):
                max_progress = None
            # Podcasts and videos already know their length. Don't ask a provider.
            elif self.item.media_type in (
                MediaTypes.PODCAST.value,
                MediaTypes.VIDEO.value,
            ):
                max_progress = self._get_local_max_progress()
            else:
                try:
                    max_progress = providers.services.get_media_metadata(
                        self.item.media_type,
                        self.item.media_id,
                        self.item.source,
                    )["max_progress"]
                except (
                    providers.services.ProviderAPIError,
                    RequestException,
                    ValueError,
                ):
                    logger.warning(
                        "Unable to fetch max progress for %s (%s/%s)",
                        self.item.media_type,
                        self.item.source,
                        self.item.media_id,
                    )
                    max_progress = None

            if max_progress:
                self.progress = max_progress

        if self.item.media_type not in (
            MediaTypes.MUSIC.value,
            MediaTypes.PODCAST.value,
            MediaTypes.VIDEO.value,
        ):
            self.item.fetch_releases(delay=True)

    @property
    def formatted_score(self):
        """Return as int if score is 10.0 or 0.0, otherwise show decimal."""
        if self.score is not None:
            max_score = 10
            min_score = 0
            if self.score in (max_score, min_score):
                return int(self.score)
            return self.score
        return None

    @property
    def formatted_progress(self):
        """Return the progress of the media in a formatted string."""
        return str(self.progress)

    @property
    def progress_is_percentage(self):
        """Return whether formatted_progress is rendering a percentage."""
        return False

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in, or None if not well-defined."""
        return None

    @property
    def formatted_max_progress(self):
        """Return max_progress rendered in the same unit as `formatted_progress`."""
        max_progress = getattr(self, "max_progress", None)
        return "" if max_progress is None else str(max_progress)

    @property
    def formatted_aggregated_progress(self):
        """Return formatted aggregated progress string."""
        if (
            hasattr(self, "aggregated_progress")
            and self.aggregated_progress is not None
        ):
            # Format based on media type
            if hasattr(self, "item") and self.item.media_type == MediaTypes.GAME.value:
                return app.helpers.minutes_to_hhmm(self.aggregated_progress)
            return str(self.aggregated_progress)
        return str(self.progress)

    def _get_known_item_runtime_minutes(self):
        """Return a persisted runtime value without falling back to estimates."""
        runtime_minutes = getattr(self.item, "runtime_minutes", None)
        if runtime_minutes and runtime_minutes < RUNTIME_UNKNOWN_AIRED:
            return runtime_minutes

        runtime_display = getattr(self.item, "runtime", "")
        if runtime_display:
            from app.statistics import parse_runtime_to_minutes

            parsed_runtime = parse_runtime_to_minutes(runtime_display)
            if parsed_runtime and parsed_runtime < RUNTIME_UNKNOWN_AIRED:
                return parsed_runtime

        return None

    def _plays_sort_value(self):
        """Return the aggregated play/progress count used by plays-based UI."""
        aggregated_progress = getattr(self, "aggregated_progress", None)
        if aggregated_progress is not None:
            return aggregated_progress
        return self.progress or 0

    def _episode_runtime_entries(self):
        """Return {season_number: [(episode_number, runtime), ...]} for this show.

        Uses the index prefilled by prefill_episode_runtime_index when present
        (bulk pages); otherwise fetches the whole show in one query and
        memoizes it, so detail pages issue one query instead of one per season.
        """
        index = getattr(self, "_episode_runtime_index", None)
        if index is None:
            from app.models.episode_runtimes import build_episode_runtime_index

            key = (self.item.media_id, self.item.source)
            index = build_episode_runtime_index({key}).get(key, {})
            self._episode_runtime_index = index
        return index

    def _calc_total_runtime_from_items(self, total_episodes):
        """Estimate full released runtime from stored episode runtimes when possible."""
        if not total_episodes or total_episodes <= 0:
            return None

        if self.item.media_type == MediaTypes.TV.value:
            breakdown = getattr(self, "released_episode_breakdown", None) or {}
            if not breakdown:
                return None

            season_episodes = self._episode_runtime_entries()
            total_runtime = 0
            episodes_with_data = 0
            for season_num in sorted(breakdown.keys()):
                released_episode_count = breakdown[season_num]
                season_runtimes = [
                    runtime
                    for episode_number, runtime in season_episodes.get(season_num, ())
                    if episode_number is not None
                    and episode_number <= released_episode_count
                ]
                if season_runtimes:
                    total_runtime += sum(season_runtimes)
                    episodes_with_data += len(season_runtimes)

            if episodes_with_data > 0:
                if episodes_with_data == total_episodes:
                    return total_runtime
                missing_eps = total_episodes - episodes_with_data
                avg_runtime = total_runtime / episodes_with_data
                return total_runtime + int(missing_eps * avg_runtime)
            return None

        if self.item.media_type != MediaTypes.ANIME.value:
            return None

        episode_runtimes = [
            runtime
            for season_entries in self._episode_runtime_entries().values()
            for episode_number, runtime in season_entries
            if episode_number is not None and episode_number <= total_episodes
        ]
        if not episode_runtimes:
            return None
        if len(episode_runtimes) == total_episodes:
            return sum(episode_runtimes)
        avg_runtime = sum(episode_runtimes) / len(episode_runtimes)
        return sum(episode_runtimes) + int(
            (total_episodes - len(episode_runtimes)) * avg_runtime
        )

    @property
    def total_runtime_minutes(self):
        """Return total title runtime in minutes for supported media types."""
        cached_total = getattr(self, "_total_runtime_minutes_cache", None)
        if cached_total is not None:
            return cached_total

        total_runtime = None
        media_type = getattr(self.item, "media_type", None)

        if media_type == MediaTypes.MOVIE.value:
            total_runtime = self._get_known_item_runtime_minutes()
        elif media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
            total_episodes = getattr(self, "max_progress", None)
            if total_episodes and total_episodes > 0:
                total_runtime = self._calc_total_runtime_from_items(total_episodes)
                if total_runtime is None:
                    average_runtime = self._get_known_item_runtime_minutes()
                    if average_runtime is None:
                        average_runtime = self._get_fallback_runtime_minutes()
                    if average_runtime and average_runtime < RUNTIME_UNKNOWN_FAILED:
                        total_runtime = total_episodes * average_runtime

        self._total_runtime_minutes_cache = total_runtime or 0
        return total_runtime

    @property
    def formatted_total_runtime(self):
        """Return the total runtime in a readable display format."""
        total_runtime = self.total_runtime_minutes
        return app.helpers.minutes_to_hhmm(total_runtime) if total_runtime else "--"

    @property
    def average_runtime_minutes(self):
        """Return the best runtime estimate for a single play/watch."""
        runtime_minutes = self._get_known_item_runtime_minutes()
        if runtime_minutes:
            return runtime_minutes

        total_runtime = self.total_runtime_minutes
        if not total_runtime:
            return None

        max_progress = getattr(self, "max_progress", None)
        if max_progress and max_progress > 0:
            return max(1, round(total_runtime / max_progress))

        if getattr(self.item, "media_type", None) == MediaTypes.MOVIE.value:
            return total_runtime

        return None

    @property
    def time_watched_minutes(self):
        """Return the estimated total watched time in minutes."""
        plays = self._plays_sort_value()
        if plays <= 0:
            return None

        average_runtime = self.average_runtime_minutes
        if not average_runtime:
            return None

        return plays * average_runtime

    @property
    def formatted_time_watched(self):
        """Return total watched time in a readable display format."""
        total_minutes = self.time_watched_minutes
        return app.helpers.minutes_to_hhmm(total_minutes) if total_minutes else "--"

    @property
    def episodes_left(self):
        """Return the number of episodes left to watch."""
        if not hasattr(self, "max_progress") or self.max_progress is None:
            return 0
        return max(0, self.max_progress - self.progress)

    @property
    def time_left(self):
        """Return the estimated time left to complete the show in minutes.

        For accuracy, this sums actual episode runtimes for unwatched episodes
        from the Item table, falling back to averages only when data is unavailable.
        """
        if not hasattr(self, "max_progress") or self.max_progress is None:
            return 0

        episodes_left = self.episodes_left
        if episodes_left <= 0:
            return 0

        # First, try to sum actual unwatched episode runtimes from Item table
        total_from_items = self._calc_unwatched_runtime_from_items(episodes_left)
        if total_from_items is not None:
            return total_from_items

        # Fallback: use average runtime x episodes_left
        runtime_minutes = self._get_fallback_runtime_minutes()

        # Skip shows with unrealistic runtime (999999 fallback)
        if runtime_minutes >= RUNTIME_UNKNOWN_FAILED:
            return 0

        return episodes_left * runtime_minutes

    def _calc_unwatched_runtime_from_items(self, episodes_left):
        """Sum actual runtimes for unwatched episodes from Item table.

        Returns total runtime in minutes, or None if data is unavailable.
        """
        season_number = getattr(self.item, "season_number", None)

        if self.item.media_type == MediaTypes.SEASON.value and season_number:
            # For a Season: query episodes in this season where episode_number > progress
            # Only count episodes that have actually been released (have aired)
            from events.models import released_q

            current_datetime = timezone.now()
            unwatched_episodes = (
                Item.objects.filter(
                    media_id=self.item.media_id,
                    source=self.item.source,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=season_number,
                    episode_number__gt=self.progress,
                    runtime_minutes__isnull=False,
                    release_datetime__isnull=False,  # Only count episodes with air dates
                )
                .filter(released_q("release_datetime", current_datetime))
                .exclude(
                    runtime_minutes=RUNTIME_UNKNOWN_FAILED,  # Exclude placeholder for unknown runtime
                )
                .exclude(
                    runtime_minutes=RUNTIME_UNKNOWN_AIRED,  # Exclude 999998 marker for "aired but runtime unknown"
                )
                .values_list("runtime_minutes", flat=True)
            )

            runtimes = list(unwatched_episodes)
            if runtimes:
                total = sum(runtimes)
                # If we have data for all unwatched episodes, return exact sum
                if len(runtimes) == episodes_left:
                    return total
                # Partial data: estimate missing episodes using average of known
                missing_eps = episodes_left - len(runtimes)
                avg_runtime = total / len(runtimes)
                return total + int(missing_eps * avg_runtime)

        elif self.item.media_type == MediaTypes.TV.value:
            # For TV show: need to aggregate across seasons
            # Use released_episode_breakdown if available
            breakdown = getattr(self, "released_episode_breakdown", None)
            if breakdown:
                total_runtime = 0
                episodes_with_data = 0
                remaining_progress = self.progress
                # Use prefilled index (set by prefill_episode_runtime_index) when
                # available to avoid one DB query per partially-watched season.
                episode_runtime_index = getattr(self, "_episode_runtime_index", None)

                for season_num in sorted(breakdown.keys()):
                    season_episode_count = breakdown[season_num]

                    if remaining_progress >= season_episode_count:
                        remaining_progress -= season_episode_count
                    else:
                        watched_in_season = remaining_progress
                        remaining_progress = 0

                        if episode_runtime_index is not None:
                            runtimes = [
                                rt
                                for ep_num, rt in episode_runtime_index.get(
                                    season_num, []
                                )
                                if ep_num > watched_in_season
                            ]
                        else:
                            runtimes = list(
                                Item.objects.filter(
                                    media_id=self.item.media_id,
                                    source=self.item.source,
                                    media_type=MediaTypes.EPISODE.value,
                                    season_number=season_num,
                                    episode_number__gt=watched_in_season,
                                    runtime_minutes__isnull=False,
                                )
                                .exclude(runtime_minutes=RUNTIME_UNKNOWN_FAILED)
                                .exclude(runtime_minutes=RUNTIME_UNKNOWN_AIRED)
                                .values_list("runtime_minutes", flat=True)
                            )

                        if runtimes:
                            total_runtime += sum(runtimes)
                            episodes_with_data += len(runtimes)

                if episodes_with_data > 0:
                    if episodes_with_data == episodes_left:
                        return total_runtime
                    # Partial data: estimate missing
                    missing_eps = episodes_left - episodes_with_data
                    avg_runtime = total_runtime / episodes_with_data
                    return total_runtime + int(missing_eps * avg_runtime)

        return None  # Signal to use fallback

    def _get_fallback_runtime_minutes(self):
        """Get average runtime for fallback calculation."""
        from app.models.episode_runtimes import (
            build_season_runtime_index,
            default_runtime_minutes,
        )

        runtime_minutes = None

        # First, try to get from TV show runtime
        if (
            hasattr(self, "item") and self.item.runtime_minutes
        ) and self.item.runtime_minutes < RUNTIME_UNKNOWN_FAILED:
            runtime_minutes = self.item.runtime_minutes

        if not runtime_minutes:
            # Then use the cached season metadata. The seasons are read in one
            # grouped request instead of one request for each season.
            runtime_minutes = build_season_runtime_index([self.item.media_id]).get(
                self.item.media_id,
            )

        # Use fallback values if nothing found
        if runtime_minutes is None:
            runtime_minutes = default_runtime_minutes(self.item.source)

        return runtime_minutes

    @property
    def formatted_time_left(self):
        """Return the time left in a human-readable format."""
        time_left_minutes = self.time_left
        if time_left_minutes <= 0:
            return "0m"

        hours = time_left_minutes // 60
        minutes = time_left_minutes % 60

        if hours > 0:
            if minutes > 0:
                return f"{hours}h {minutes}m"
            return f"{hours}h"
        return f"{minutes}m"

    def increase_progress(self):
        """Increase the progress of the media by one."""
        self.progress += 1
        self.save()
        logger.info("Incresed progress of %s to %s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by one."""
        self.progress -= 1
        self.save()
        logger.info("Decreased progress of %s to %s", self, self.progress)


class BasicMedia(Media):
    """Model for basic media types."""

    objects = MediaManager()


def _percentage_tracking_max_progress(media):
    """Return max_progress if this media should be quick-updated in percentage terms."""
    if media.user_id and media.user.book_comic_manga_progress_percentage:
        return getattr(media, "max_progress", None)
    return None


def _percentage_progress_text(media):
    """Return progress formatted as a percentage string, if percentage tracking applies."""
    max_progress = _percentage_tracking_max_progress(media)
    if max_progress:
        percentage = round((media.progress / max_progress) * 100, 1)
        return f"{percentage:g}%"
    return None


def _percentage_increase_progress(media):
    """Increase progress by one percentage point, or fall back to the default +1 step."""
    max_progress = _percentage_tracking_max_progress(media)
    if max_progress:
        step = max(1, round(max_progress * 0.01))
        media.progress = min(media.progress + step, max_progress)
        media.save()
        logger.info("Increased progress of %s to %s", media, media.progress)
    else:
        Media.increase_progress(media)


def _percentage_decrease_progress(media):
    """Decrease progress by one percentage point, or fall back to the default -1 step."""
    max_progress = _percentage_tracking_max_progress(media)
    if max_progress:
        step = max(1, round(max_progress * 0.01))
        media.progress = max(media.progress - step, 0)
        media.save()
        logger.info("Decreased progress of %s to %s", media, media.progress)
    else:
        Media.decrease_progress(media)


class Manga(Media):
    """Model for manga."""

    tracker = FieldTracker()

    reading_time = models.PositiveIntegerField(
        default=0,
        help_text="Minutes spent reading, added up from check-ins or entered by hand.",
    )

    @serialized_state_write
    def save(self, *args, **kwargs):
        """Save the manga instance, then queue a MyAnimeList sync if relevant."""
        should_sync = self._sync_relevant_fields_changed()
        clear_score = self._score_cleared()
        super().save(*args, **kwargs)
        if should_sync:
            self._queue_mal_sync(MediaTypes.MANGA.value, clear_score=clear_score)

    @property
    def formatted_progress(self):
        """Return progress as a percentage when percentage tracking is enabled."""
        return _percentage_progress_text(self) or str(self.progress)

    @property
    def progress_is_percentage(self):
        """Return whether formatted_progress is rendering a percentage."""
        return _percentage_progress_text(self) is not None

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in."""
        return "percentage" if self.progress_is_percentage else "chapters"

    def increase_progress(self):
        """Increase progress, respecting the percentage tracking preference."""
        _percentage_increase_progress(self)

    def decrease_progress(self):
        """Decrease progress, respecting the percentage tracking preference."""
        _percentage_decrease_progress(self)


class ActiveAnimeQuerySet(ImportScopedQuerySet):
    """Anime rows that have not been migrated into grouped series."""

    def active(self):
        """Return only rows still surfaced in the flat anime library."""
        return self.filter(migrated_to_item__isnull=True)


class ActiveAnimeManager(models.Manager):
    """Default anime manager that hides migrated legacy rows."""

    def get_queryset(self):
        """Return only active flat anime rows."""
        return ActiveAnimeQuerySet(self.model, using=self._db).active()


class Anime(Media):
    """Model for anime."""

    migrated_to_item = models.ForeignKey(
        Item,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="migrated_anime_entries",
    )
    migrated_at = models.DateTimeField(null=True, blank=True)

    tracker = FieldTracker()
    objects = ActiveAnimeManager()
    all_objects = ImportScopedManager()  # manager order is significant; objects must stay the default

    @serialized_state_write
    def save(self, *args, **kwargs):
        """Save, then auto-migrate a completed flat MAL anime to episode tracking.

        A flat MAL anime has no per-episode watch records, so completing it would
        otherwise leave its (provider-mapped) episode list showing unwatched. When
        it becomes COMPLETED we convert it into grouped TV-style tracking, which
        creates real Episode records for every watched episode.

        The conversion changes the entry's library shape and metadata provider,
        so it stays behind the same opt-in as a scrobble-driven conversion
        (``group_scrobbled_anime``). Without it, completing a MAL entry keeps
        its MAL identity and detail URL.
        """
        is_create = self._state.adding
        status_changed = self.tracker.has_changed("status")
        should_sync = self._sync_relevant_fields_changed()
        clear_score = self._score_cleared()
        super().save(*args, **kwargs)

        if should_sync:
            self._queue_mal_sync(MediaTypes.ANIME.value, clear_score=clear_score)

        became_completed = self.status == Status.COMPLETED.value and (
            status_changed or is_create
        )
        if (
            became_completed
            and self.migrated_to_item_id is None
            and self.item.source == Sources.MAL.value
            and self.item.media_type == MediaTypes.ANIME.value
            and getattr(self.user, "group_scrobbled_anime", False)
        ):
            self._auto_migrate_completed_flat_anime()

    def _auto_migrate_completed_flat_anime(self):
        """Best-effort migration of a completed flat MAL anime; never fatal."""
        try:
            self._migrate_completed_flat_anime()
        except Exception:
            logger.warning(
                "Auto-migrate crashed for MAL anime %s",
                getattr(self.item, "media_id", None),
                exc_info=True,
            )

    def _migrate_completed_flat_anime(self):
        from app.services import anime_migration, metadata_resolution
        from integrations import anime_mapping

        providers_to_try = []
        default_source = (
            metadata_resolution.metadata_default_source(
                self.user,
                MediaTypes.ANIME.value,
            )
            if self.user_id
            else None
        )
        for provider in (default_source, Sources.TVDB.value, Sources.TMDB.value):
            if (
                provider in metadata_resolution.GROUPED_ANIME_PROVIDERS
                and metadata_resolution.provider_is_enabled(provider)
                and provider not in providers_to_try
            ):
                providers_to_try.append(provider)

        for provider in providers_to_try:
            if not anime_mapping.resolve_provider_series_id(
                self.item.media_id,
                provider,
            ):
                continue
            try:
                anime_migration.migrate_flat_anime_to_grouped(
                    self.user,
                    self.item,
                    provider,
                )
            except anime_migration.AnimeMigrationError as error:
                logger.info(
                    "Auto-migrate skipped for MAL anime %s via %s: %s",
                    self.item.media_id,
                    provider,
                    error,
                )
                continue
            except Exception:
                logger.warning(
                    "Auto-migrate failed for MAL anime %s via %s",
                    self.item.media_id,
                    provider,
                    exc_info=True,
                )
                return
            else:
                logger.info(
                    "Auto-migrated completed flat MAL anime %s to grouped %s tracking",
                    self.item.media_id,
                    provider,
                )
                return

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in."""
        return "episodes"


class Movie(Media):
    """Model for movies."""

    tracker = FieldTracker()

    def watch(self, end_date, external_id=None, entry_source=""):
        """Create a play of the movie, returning (play, created)."""
        if external_id:
            existing = self.plays.filter(external_id=external_id).first()
            if existing:
                return existing, False

        if not self.plays.exists() and self.end_date:
            # lazily preserve the pre-existing watch so it isn't lost once plays start
            MoviePlay.objects.create(movie=self, end_date=self.end_date)

        play = MoviePlay.objects.create(
            movie=self,
            end_date=end_date,
            external_id=external_id or None,
            entry_source=entry_source,
        )

        update_fields = []
        if self.end_date is None or end_date > self.end_date:
            self.end_date = end_date
            self.status = Status.COMPLETED.value
            update_fields += ["end_date", "status"]

        if entry_source and self.entry_source != entry_source:
            self.entry_source = entry_source
            update_fields.append("entry_source")

        if update_fields:
            self.save(update_fields=update_fields)

        return play, True

    def unwatch(self, external_id=None):
        """Delete a play of the movie, returning the deleted play (or None)."""
        plays = self.plays.all()
        play = (
            plays.filter(external_id=external_id).first()
            if external_id
            else plays.order_by("-end_date", "-id").first()
        )

        if play is None:
            return None

        self.remove_play(play)

        return play

    def remove_play(self, play):
        """Delete one play and re-derive the tracker's end date from what is left.

        Without the re-derivation the tracker keeps the deleted play's date and
        history falls back to it, so a deleted watch reappears (#1873).
        """
        play.delete()

        latest = self.plays.order_by("-end_date", "-id").first()
        self.end_date = latest.end_date if latest else None
        self.save(update_fields=["end_date"])


class MoviePlay(models.Model):
    """A single watch (play) of a movie."""

    created_at = models.DateTimeField(auto_now_add=True)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="plays")
    end_date = models.DateTimeField(null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    entry_source = models.CharField(max_length=50, blank=True, default="")
    objects = ImportScopedManager()

    class Meta:
        """Meta options for MoviePlay."""

        ordering = ["movie", "-end_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["movie", "external_id"],
                name="app_movieplay_unique_movie_external_id",
                condition=models.Q(external_id__isnull=False) & ~models.Q(
                    external_id="",
                ),
            ),
        ]

    def __str__(self):
        """Return a description of the play."""
        return f"{self.movie} play ({self.end_date})"

    @serialized_state_write
    def save(self, *args, **kwargs):
        """Commit the play and its watched projection together."""
        return super().save(*args, **kwargs)


class Game(Media):
    """Model for games."""

    tracker = FieldTracker()

    @property
    def formatted_progress(self):
        """Return progress in hours:minutes format."""
        return app.helpers.minutes_to_hhmm(self.progress)

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in."""
        return "minutes"

    def increase_progress(self):
        """Increase the progress of the media by 30 minutes."""
        self.progress += 30
        self.save()
        logger.info("Changed playtime of %s to %s", self, self.formatted_progress)

    def decrease_progress(self):
        """Decrease the progress of the media by 30 minutes."""
        self.progress -= 30
        self.save()
        logger.info("Changed playtime of %s to %s", self, self.formatted_progress)


class BoardGame(Media):
    """Model for board games."""

    tracker = FieldTracker()

    @property
    def formatted_progress(self):
        """Return progress as play count."""
        plays = self.progress or 0
        return f"{plays} play{'s' if plays != 1 else ''}"

    @property
    def formatted_aggregated_progress(self):
        """Return aggregated progress as play count."""
        plays = getattr(self, "aggregated_progress", None)
        value = plays if plays is not None else self.progress
        return f"{value} play{'s' if value != 1 else ''}"

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in."""
        return "plays"


class Book(Media):
    """Model for books."""

    tracker = FieldTracker()

    reading_time = models.PositiveIntegerField(
        default=0,
        help_text="Minutes spent reading, added up from check-ins or entered by hand.",
    )

    @property
    def formatted_progress(self):
        """Return progress formatted by book format: time for audiobooks, pages otherwise."""
        percentage_text = _percentage_progress_text(self)
        if percentage_text:
            return percentage_text
        if getattr(self, "item", None) and self.item.format == "audiobook":
            return app.helpers.minutes_to_hhmm(self.progress)
        return str(self.progress)

    @property
    def progress_is_percentage(self):
        """Return whether formatted_progress is rendering a percentage."""
        return _percentage_progress_text(self) is not None

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in."""
        if self.progress_is_percentage:
            return "percentage"
        if getattr(self, "item", None) and self.item.format == "audiobook":
            return "minutes"
        return "pages"

    @property
    def formatted_max_progress(self):
        """Return the total as listening time for audiobooks, else a page count."""
        max_progress = getattr(self, "max_progress", None)
        if (
            max_progress is not None
            and getattr(self, "item", None)
            and self.item.format == "audiobook"
        ):
            return app.helpers.minutes_to_hhmm(max_progress)
        return super().formatted_max_progress

    def increase_progress(self):
        """Increase progress, respecting the percentage tracking preference."""
        _percentage_increase_progress(self)

    def decrease_progress(self):
        """Decrease progress, respecting the percentage tracking preference."""
        _percentage_decrease_progress(self)


class BookListeningSession(models.Model):
    """One dated stretch of listening to an audiobook, as a server reports it."""

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="listening_sessions",
    )
    session_id = models.CharField(
        max_length=100,
        help_text="The server's id for the session, so a re-import updates it.",
    )
    started_at = models.DateTimeField()
    minutes = models.PositiveIntegerField(default=0)
    position_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Where in the book the session stopped, as listening time.",
    )

    class Meta:
        """Meta options for the model."""

        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["book", "session_id"],
                name="app_booklisteningsession_unique",
            ),
        ]

    def __str__(self):
        """Return the session as 'title: minutes on date'."""
        return f"{self.book.item.title}: {self.minutes}m on {self.started_at:%Y-%m-%d}"


class Comic(Media):
    """Model for comics."""

    tracker = FieldTracker()

    reading_time = models.PositiveIntegerField(
        default=0,
        help_text="Minutes spent reading, added up from check-ins or entered by hand.",
    )

    @property
    def formatted_progress(self):
        """Return progress as a percentage when percentage tracking is enabled."""
        return _percentage_progress_text(self) or str(self.progress)

    @property
    def progress_is_percentage(self):
        """Return whether formatted_progress is rendering a percentage."""
        return _percentage_progress_text(self) is not None

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in."""
        return "percentage" if self.progress_is_percentage else "issues"

    def increase_progress(self):
        """Increase progress, respecting the percentage tracking preference."""
        _percentage_increase_progress(self)

    def decrease_progress(self):
        """Decrease progress, respecting the percentage tracking preference."""
        _percentage_decrease_progress(self)


class ComicIssue(Media):
    """Model for individual comic issues."""

    tracker = FieldTracker()

    @property
    def progress_unit(self):
        """Return the unit `progress` is measured in."""
        return "pages"
