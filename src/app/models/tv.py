import contextlib
import logging
from datetime import UTC, datetime

from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models, transaction
from django.db.models import F, Max
from django.utils import timezone
from django.utils.functional import cached_property
from model_utils import FieldTracker
from requests import RequestException
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import events
from app import cache_utils, providers
from app.models.choices import USER_HELD_STATUSES, MediaTypes, Sources, Status
from app.models.item import Item
from app.models.manager import MediaManager
from app.models.media import Media, ScoreMonitorField
from app.public_errors import PublicMessageError
from app.services.sync_lock import serialized_state_write
from integrations.import_scope import ImportScopedManager

logger = logging.getLogger(__name__)

# Sentinel distinguishing "no explicit end_date supplied" (fall back to the
# user's resolve_watch_date behavior) from an explicit value, including None
# (blank date deliberately chosen on the completion form).
_UNSET_END_DATE = object()
# Marks an open-but-unfilled metadata memo, see `TV._fetch_tv_metadata`.
_UNSET_TV_METADATA = object()
MIN_VALID_RELEASE_YEAR = 1900
# History labels for status changes Floppy makes on its own, so the Edit
# Tracking modal can say what moved a show (#1133).
NEXT_SEASON_REASON = "Next season started"
EPISODE_PLAYED_REASON = "Episode played"

# How a provider's production status is classified. Only ENDED permits
# finalizing a tracked show, so UNKNOWN (the provider said something we do not
# recognize) and ABSENT (it said nothing) are both non-terminal by
# construction: a renamed, localized or malformed value must never read as
# "ended" and silently complete a show the user is still watching (#375).
PRODUCTION_STATUS_ONGOING = "ongoing"
PRODUCTION_STATUS_ENDED = "ended"
PRODUCTION_STATUS_UNKNOWN = "unknown"
PRODUCTION_STATUS_ABSENT = ""

# TMDB, TVDB and the anime providers each spell these differently. Being
# non-exhaustive here is safe - an unlisted value falls through to UNKNOWN,
# which blocks completion - so err towards leaving a value out rather than
# guessing it is terminal.
ONGOING_PRODUCTION_STATUSES = frozenset(
    {
        # TMDB
        "returning series",
        "in production",
        "post production",
        "planned",
        "pilot",
        # TVDB
        "continuing",
        "upcoming",
        # AniList / MAL
        "releasing",
        "ongoing",
        "currently airing",
        "not yet aired",
        "not yet released",
        "hiatus",
        "on hiatus",
    },
)

# The only vocabulary that lets a show be finalized. Keep it conservative:
# every addition here is a new way for a sync to complete someone's show.
TERMINAL_PRODUCTION_STATUSES = frozenset(
    {
        # TMDB / TVDB
        "ended",
        "canceled",
        "cancelled",
        # AniList / MAL
        "finished",
        "finished airing",
        "completed",
        "complete",
        "concluded",
        "discontinued",
    },
)


def classify_production_status(raw_status):
    """Classify a provider's production status.

    Returns PRODUCTION_STATUS_ONGOING, _ENDED, _UNKNOWN (the provider reported
    something unrecognized) or _ABSENT (it reported nothing). Callers must
    treat anything other than _ENDED as "not proven finished" - an allowlist
    of ongoing values inverted into "ended" would make every unrecognized
    string terminal.
    """
    text = "" if raw_status is None else str(raw_status).strip()
    if not text:
        return PRODUCTION_STATUS_ABSENT

    normalized = text.casefold().replace("_", " ")
    if normalized in ONGOING_PRODUCTION_STATUSES:
        return PRODUCTION_STATUS_ONGOING
    if normalized in TERMINAL_PRODUCTION_STATUSES:
        return PRODUCTION_STATUS_ENDED
    return PRODUCTION_STATUS_UNKNOWN


class RewatchAlreadyCompleteError(PublicMessageError):
    """Raised when a rewatch's start date leaves nothing left to rewatch."""


def _runtime_minutes(value):
    """Return a positive runtime in minutes, or None.

    Providers disagree on the type: TMDB sends an int, manual items store the
    string the custom-metadata form was filled in with.
    """
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return minutes if minutes > 0 else None


def _percent_of_max(count, max_progress):
    """Return count as a 0-100 percent of max_progress, or None when it is unknown."""
    try:
        max_progress = int(max_progress)
    except (TypeError, ValueError):
        return None
    if max_progress <= 0:
        return None
    return max(0, min(round(count / max_progress * 100), 100))


class TV(Media):
    """Model for TV shows."""

    active_episode_order = models.ForeignKey(
        "app.EpisodeOrder", null=True, blank=True, on_delete=models.PROTECT,
        related_name="trackers",
    )

    @property
    def tracking_media_id(self):
        """Return the identity of the selected episode catalogue."""
        return self.active_episode_order.media_id if self.active_episode_order_id else self.item.media_id

    @property
    def tracking_source(self):
        """Return the provider of the selected episode catalogue."""
        return self.active_episode_order.provider if self.active_episode_order_id else self.item.source

    tracker = FieldTracker()

    class Meta:
        """Meta options for the model."""

        ordering = ["user", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="%(app_label)s_%(class)s_unique_item_user",
            ),
        ]

    @tracker  # postpone field reset until after the save
    @serialized_state_write
    def save(self, *args, **kwargs):
        """Save the media instance."""
        is_create = self._state.adding
        self._include_updated_at(kwargs)
        super(Media, self).save(*args, **kwargs)

        # A show created directly as COMPLETED (rather than transitioned into it)
        # must still fan out completion to its seasons/episodes.
        completed_on_create = is_create and self.status == Status.COMPLETED.value
        if (
            not is_create and self.tracker.has_changed("status")
        ) or completed_on_create:
            if self.status == Status.COMPLETED.value:
                try:
                    self._completed()
                except (
                    providers.services.ProviderAPIError,
                    RequestException,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as error:
                    logger.warning(
                        "Skipping completion fan-out due to missing metadata"
                        " for %s: %s",
                        self.tracking_media_id,
                        error,
                    )

            elif self.status == Status.DROPPED.value:
                self._mark_in_progress_seasons_as_dropped()

            elif (
                self.status == Status.IN_PROGRESS.value
                and not self.seasons.filter(status=Status.IN_PROGRESS.value).exists()
            ):
                self._start_next_available_season()

            self.item.fetch_releases(delay=True)
        elif (
            not is_create
            and self.status == Status.IN_PROGRESS.value
            and not self.seasons.filter(status=Status.IN_PROGRESS.value).exists()
        ):
            # Keep TV+Season state aligned even when status was already IN_PROGRESS.
            self._start_next_available_season()

        cache_utils.clear_library_view_caches(
            self.user_id,
            media_types=(MediaTypes.TV.value,),
        )

    @cached_property
    def progress(self):
        """Return the total episodes watched for the TV show, excluding dropped seasons."""
        return sum(
            season.progress
            for season in self.seasons.all()
            if season.item.season_number != 0 and season.status != Status.DROPPED.value
        )

    @cached_property
    def watched_once_count(self):
        """Return the episodes finished at least once, excluding specials and dropped seasons."""
        return sum(
            len(season.watched_once_numbers)
            for season in self.seasons.all()
            if season.item.season_number != 0 and season.status != Status.DROPPED.value
        )

    @property
    def watched_once_percentage(self):
        """Return percent of the show watched at least once (0-100), or None."""
        return _percent_of_max(
            self.watched_once_count,
            getattr(self, "max_progress", None),
        )

    @property
    def rewatch_percentage(self):
        """Return percent of the open rewatch pass completed, or None outside one."""
        if not self.is_rewatching:
            return None
        # Only seasons with an open pass count, so rewatching one season of a
        # show reads as that season's share of the whole show.
        done = sum(
            len(season.completed_episode_numbers)
            for season in self.seasons.all()
            if season.item.season_number != 0
            and season.status != Status.DROPPED.value
            and season.is_rewatching
        )
        return _percent_of_max(done, getattr(self, "max_progress", None))

    @cached_property
    def completed_episode_count(self):
        """Return the count of distinct watched episodes, excluding dropped seasons."""
        return sum(
            season.completed_episode_count
            for season in self.seasons.all()
            if season.item.season_number != 0 and season.status != Status.DROPPED.value
        )

    def _plays_sort_value(self):
        """Return completed-episode count (not furthest position) for plays/time-watched UI."""
        aggregated_progress = getattr(self, "aggregated_progress", None)
        if aggregated_progress is not None:
            return aggregated_progress
        return self.completed_episode_count

    @property
    def is_rewatching(self):
        """Return whether any season of the show is in a rewatch pass."""
        return any(
            season.rewatch_started_at is not None
            for season in self.seasons.all()
            if season.status != Status.DROPPED.value
        )

    @property
    def rewatch_started_at(self):
        """Return when this show's rewatch began, or None.

        TV has no rewatch_started_at column of its own — the pass lives on
        each season — so this reads the earliest open one. `start_rewatch`
        opens every season together, but a season skipped for already
        being covered (or one whose pass was started separately) can leave
        them different; the earliest is the more honest "since" to show.
        """
        dates = [
            season.rewatch_started_at
            for season in self.seasons.all()
            if season.rewatch_started_at is not None
        ]
        return min(dates) if dates else None

    def start_rewatch(self, started_at=None):
        """Open a rewatch pass on every season with something left to rewatch.

        A season already fully covered by plays at or after `started_at`
        is skipped rather than opened and immediately closed again —
        that's more confusing than just leaving it alone. A season with
        an already-open pass is left untouched too, so a duplicate
        submission or retry can't silently push its cutoff later and
        strand plays logged against the original one as pre-cutoff
        history. Raises RewatchAlreadyCompleteError only if every season
        would be skipped, so the caller can tell the user nothing would
        happen. Returns the seasons that *were* skipped (empty if none)
        so a caller can still tell the user about a partial skip, since
        that succeeds silently otherwise.
        """
        from app.models.media import BasicMedia  # avoid a module-load cycle

        is_explicit_start = started_at is not None
        started_at = started_at or timezone.now()
        all_seasons = [
            season
            for season in self.seasons.filter(item__season_number__gt=0)
            if season.status != Status.DROPPED.value
        ]
        if not all_seasons:
            no_seasons_msg = "This show has no seasons to rewatch."
            raise RewatchAlreadyCompleteError(no_seasons_msg)

        seasons = [season for season in all_seasons if season.rewatch_started_at is None]
        if not seasons:
            already_open_msg = "Every season is already being rewatched."
            raise RewatchAlreadyCompleteError(already_open_msg)
        BasicMedia.objects.annotate_max_progress(seasons, MediaTypes.SEASON.value)

        skipped = [
            season
            for season in seasons
            if is_explicit_start
            and season._would_be_immediately_complete(
                started_at,
                getattr(season, "max_progress", None),
            )
        ]
        openable = [season for season in seasons if season not in skipped]
        if not openable:
            all_covered_msg = "Every season is already fully watched from that date."
            raise RewatchAlreadyCompleteError(all_covered_msg)

        for season in openable:
            season.rewatch_started_at = started_at
            season.status = Status.IN_PROGRESS.value
            season._invalidate_episode_stats()  # same-module sibling
        bulk_update_with_history(
            openable,
            Season,
            fields=["rewatch_started_at", "status"],
        )

        if self.status != Status.IN_PROGRESS.value:
            self.status = Status.IN_PROGRESS.value
            bulk_update_with_history([self], TV, fields=["status"])

        return skipped

    def stop_rewatch(self, max_progress=None):
        """Close every open rewatch pass on the show's seasons.

        `max_progress` is ignored: seasons can have different episode
        counts, so each resolves its own via `Season.stop_rewatch`. Also
        reconciles the show's own status against where its seasons land —
        ending a pass before it finished can fall back to Completed (or
        stay In Progress) independently of what the show currently reads
        as, same as `start_rewatch`.
        """
        seasons = [
            season
            for season in self.seasons.filter(item__season_number__gt=0)
            if season.status != Status.DROPPED.value
        ]
        for season in seasons:
            season.stop_rewatch()

        if not seasons:
            return
        if all(season.status == Status.COMPLETED.value for season in seasons):
            # Only the provider's production status can finish the show; a
            # returning series stays in progress after its rewatch ends.
            self._handle_completed_season(
                max(season.item.season_number for season in seasons),
            )
            return
        if self.status != Status.IN_PROGRESS.value:
            self.status = Status.IN_PROGRESS.value
            bulk_update_with_history([self], TV, fields=["status"])

    @property
    def progress_percentage(self):
        """Return percent of the show actually watched (0-100), or None.

        Unlike `progress` (furthest episode number touched, kept for #327's
        skip-tolerant behavior), this reflects distinct completed episodes
        against the show's total, for progress-bar style displays.
        """
        max_progress_value = getattr(self, "max_progress", None)
        try:
            max_progress_value = int(max_progress_value)
        except (TypeError, ValueError):
            return None
        if max_progress_value <= 0:
            return None
        percentage = round(self.completed_episode_count / max_progress_value * 100)
        return max(0, min(percentage, 100))

    @cached_property
    def last_watched(self):
        """Return the latest watched episode in SxxExx format."""
        watched_episodes = [
            {
                "season": season.item.season_number,
                "episode": episode.item.episode_number,
                "end_date": getattr(episode, "_card_last_end", episode.end_date),
            }
            for season in self.seasons.all()
            if hasattr(season, "episodes") and season.item.season_number != 0
            for episode in season.episodes.all()
            if getattr(episode, "_card_last_end", episode.end_date) is not None
        ]

        if not watched_episodes:
            return ""

        latest_episode = max(
            watched_episodes,
            key=lambda x: (x["end_date"], x["season"], x["episode"]),
        )

        return f"S{latest_episode['season']:02d}E{latest_episode['episode']:02d}"

    @cached_property
    def progressed_at(self):
        """Return the date when the last attached episode was watched."""
        dates = self._season_activity_dates("progressed_at", include_specials=True)
        return max(dates) if dates else None

    @cached_property
    def start_date(self):
        """Return the first watched date, preferring main seasons over specials."""
        dates = self._season_activity_dates("start_date")
        if dates:
            return min(dates)
        special_dates = self._season_activity_dates("start_date", include_specials=True)
        if special_dates:
            return min(special_dates)
        if self.status == Status.IN_PROGRESS.value:
            return self.created_at
        return None

    @cached_property
    def end_date(self):
        """Return the last watched date across main seasons and specials."""
        dates = self._season_activity_dates("end_date", include_specials=True)
        return max(dates) if dates else None

    def _get_quick_update_season(self, operation):
        """Return the season that should handle quick TV progress updates."""
        seasons = sorted(
            (season for season in self.seasons.all() if season.item.season_number != 0),
            key=lambda season: season.item.season_number,
        )

        next_episode_target = self.next_episode_target()
        if next_episode_target is not None:
            return next_episode_target[0]

        for season in seasons:
            if season.status == Status.IN_PROGRESS.value:
                return season

        if operation == "increase" and self._start_next_available_season():
            return (
                self.seasons.filter(
                    item__season_number__gt=0,
                    status=Status.IN_PROGRESS.value,
                )
                .order_by("item__season_number")
                .first()
            )

        if operation == "decrease":
            for season in reversed(seasons):
                if season.progress > 0:
                    return season

        return None

    def next_episode_target(self, *, episode_numbers_by_season=None):
        """Return the first tracked season and episode that can be watched next."""
        seasons = sorted(
            (season for season in self.seasons.all() if season.item.season_number != 0),
            key=lambda season: season.item.season_number,
        )
        has_caught_up_season = False
        planning_continuation = None
        paused_continuation = None

        for season in seasons:
            if season.status == Status.DROPPED.value:
                continue

            next_episode_number = season.next_episode_number(
                episode_numbers=(
                    episode_numbers_by_season.get(season.item.season_number, [])
                    if episode_numbers_by_season is not None else None
                ),
            )
            if next_episode_number is None:
                has_caught_up_season = (
                    season.status == Status.COMPLETED.value or season.progress > 0
                )
                continue

            # A paused season is still where the user left the show, so it can be
            # watched next -- but only once no unpaused season qualifies, because
            # pausing one season and moving on to a later one is also normal. It
            # never hands off to a following planning season: the user stopped here.
            if season.status == Status.PAUSED.value:
                if season.progress > 0 and paused_continuation is None:
                    paused_continuation = season, next_episode_number
                has_caught_up_season = False
                continue

            if season.progress > 0 or season.status == Status.IN_PROGRESS.value:
                return season, next_episode_number

            if (
                has_caught_up_season
                and season.status == Status.PLANNING.value
                and planning_continuation is None
            ):
                planning_continuation = season, next_episode_number

            has_caught_up_season = False

        return planning_continuation or paused_continuation

    def increase_progress(self, watch_operation_id=None):
        """Increase TV progress by advancing the active season."""
        if watch_operation_id:
            from app import fork_services_episode

            operation_id = fork_services_episode.normalize_watch_operation_id(
                watch_operation_id,
            )
            claimed = (
                Episode.objects.filter(watch_operation_id=operation_id)
                .select_related("related_season", "item")
                .first()
            )
            if claimed is not None:
                if claimed.related_season.related_tv_id != self.id:
                    raise fork_services_episode.EpisodeWatchConflictError
                return fork_services_episode.resolve_episode_watch_replay(
                    claimed,
                    user_id=self.user_id,
                )
        season = self._get_quick_update_season("increase")
        if season is None:
            logger.info("No season available to increase progress for %s", self)
            return None
        return season.increase_progress(watch_operation_id=watch_operation_id)

    def decrease_progress(self):
        """Decrease TV progress by rewinding the relevant season."""
        season = self._get_quick_update_season("decrease")
        if season is None:
            logger.info("No season available to decrease progress for %s", self)
            return
        season.decrease_progress()

    def _season_activity_dates(self, attr_name, include_specials=False):
        """Collect season activity dates, optionally including specials."""
        dates = []
        for season in self.seasons.all():
            season_number = getattr(season.item, "season_number", None)
            if not include_specials and season_number == 0:
                continue

            date_value = getattr(season, attr_name, None)
            if date_value is not None:
                dates.append(date_value)

        return dates

    def _completed(self, *, prepare_only=False):
        """Create remaining seasons and episodes for a TV show."""
        tv_metadata = providers.services.get_media_metadata(
            self.item.media_type,
            self.tracking_media_id,
            self.tracking_source,
        )
        max_progress = tv_metadata["max_progress"]

        if not max_progress or self.progress > max_progress:
            return None

        seasons_to_create = []
        seasons_to_update = []
        episodes_to_create = []


        season_numbers = [
            season["season_number"]
            for season in tv_metadata["related"]["seasons"]
            if season["season_number"] != 0
        ]
        tv_with_seasons_metadata = providers.services.get_media_metadata(
            "tv_with_seasons",
            self.tracking_media_id,
            self.tracking_source,
            season_numbers,
        )
        for season_number in season_numbers:
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

            # Use season poster if available, otherwise fallback to TV show poster
            season_image = season_metadata.get("image") or self.item.image

            target_bucket = (
                MediaTypes.ANIME.value
                if self.item.library_media_type == MediaTypes.ANIME.value
                else MediaTypes.SEASON.value
            )
            allowed_buckets = [self.item.library_media_type]
            if target_bucket not in allowed_buckets:
                allowed_buckets.append(target_bucket)

            season_identity = {
                "media_id": self.tracking_media_id,
                "source": self.tracking_source,
                "media_type": MediaTypes.SEASON.value,
                "season_number": season_number,
                "episode_order": self.active_episode_order,
            }
            item = None
            for bucket in allowed_buckets:
                item = (
                    Item.objects.filter(
                        **season_identity,
                        library_media_type=bucket,
                    )
                    .order_by("id")
                    .first()
                )
                if item is not None:
                    break

            if item is None:
                item, _ = Item.objects.get_or_create(
                    **season_identity,
                    library_media_type=target_bucket,
                    defaults={
                        **Item.title_fields_from_metadata(
                            season_metadata,
                            fallback_title=self.item.title,
                        ),
                        "image": season_image,
                    },
                )
            try:
                season_instance = Season.objects.get(
                    item=item,
                    user=self.user,
                )

                if season_instance.status != Status.COMPLETED.value:
                    season_instance.status = Status.COMPLETED.value
                    seasons_to_update.append(season_instance)

            except Season.DoesNotExist:
                seasons_to_create.append(
                    Season(
                        item=item,
                        score=None,
                        status=Status.COMPLETED.value,
                        notes="",
                        related_tv=self,
                        user=self.user,
                    ),
                )

        if not prepare_only:
            bulk_create_with_history(seasons_to_create, Season)
            bulk_update_with_history(seasons_to_update, Season, ["status"])

        for season_instance in seasons_to_create + seasons_to_update:
            season_metadata = tv_with_seasons_metadata[
                f"season/{season_instance.item.season_number}"
            ]
            episodes_to_create.extend(
                season_instance.get_remaining_eps(
                    season_metadata,
                    end_date=getattr(self, "_pending_end_date", _UNSET_END_DATE),
                ),
            )
        if prepare_only:
            return {"season": seasons_to_create, "episode": episodes_to_create}, seasons_to_update
        bulk_create_with_history(episodes_to_create, Episode)
        if episodes_to_create:
            # The bulk write fires no signals, and the show's own save signal
            # ran before these episodes existed.
            from app import signals, statistics_sync

            statistics_sync.mark_rows(
                self.user_id, episodes_to_create, reason="tv_completed_fan_out"
            )
            signals.invalidate_bulk_episode_history(
                self.user_id, episodes_to_create, reason="tv_completed_fan_out"
            )
        return None

    def _mark_in_progress_seasons_as_dropped(self):
        """Mark all in-progress seasons as dropped."""
        in_progress_seasons = list(
            self.seasons.filter(status=Status.IN_PROGRESS.value),
        )

        for season in in_progress_seasons:
            season.status = Status.DROPPED.value

        if in_progress_seasons:
            bulk_update_with_history(
                in_progress_seasons,
                Season,
                fields=["status"],
            )

    def _fetch_tv_metadata(self):
        """Return this show's provider metadata, or None if unreachable.

        None means the provider could not be reached, which callers must not
        confuse with "the provider answered and carries no status". Reuses the
        value fetched earlier in the same completion pass when one is open (see
        `_handle_completed_season`); outside such a pass it always refetches,
        so a later operation on the same instance can't read stale metadata.
        """
        cached = getattr(self, "_tv_metadata_cache", _UNSET_TV_METADATA)
        if cached is not _UNSET_TV_METADATA:
            return cached

        try:
            metadata = providers.services.get_media_metadata(
                self.item.media_type,
                self.tracking_media_id,
                self.tracking_source,
            )
        except (
            providers.services.ProviderAPIError,
            RequestException,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            logger.warning(
                "Could not fetch metadata for %s (%s, %s): %s",
                self.item.title,
                self.tracking_media_id,
                self.tracking_source,
                error,
            )
            metadata = None

        if hasattr(self, "_tv_metadata_cache"):
            self._tv_metadata_cache = metadata
        return metadata

    def resolve_production_status(self):
        """Return the provider's production status text for this show.

        None means the provider could not be reached, so a caller deciding
        whether to finalize a status can tell "we don't know" apart from "the
        provider carries no status" - guessing "ended" during an outage is
        exactly the clobbering #375 is about. An empty string means the
        provider answered but reports no status.
        """
        metadata = self._fetch_tv_metadata()
        if metadata is None:
            return None
        if not isinstance(metadata, dict):
            metadata = {}

        details = metadata.get("details")
        if not isinstance(details, dict):
            details = {}

        raw_status = (
            details.get("status")
            or metadata.get("status")
            or getattr(self.item, "status", "")
            or ""
        )
        return str(raw_status).strip()

    @staticmethod
    def _first_air_date_has_passed(value):
        """Return whether a provider season's first air date is in the past."""
        if not value:
            return False
        if isinstance(value, str):
            try:
                value = datetime.strptime(value[:10], "%Y-%m-%d")  # noqa: DTZ007
            except ValueError:
                return False
        try:
            if timezone.is_naive(value):
                value = timezone.make_aware(value, timezone.get_current_timezone())
            return timezone.localtime(value).date() <= timezone.localdate()
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _first_air_date_is_future(value):
        """Return whether a provider season's first air date is known and ahead."""
        if not value:
            return False
        if isinstance(value, str):
            try:
                value = datetime.strptime(value[:10], "%Y-%m-%d")  # noqa: DTZ007
            except ValueError:
                return False
        try:
            if timezone.is_naive(value):
                value = timezone.make_aware(value, timezone.get_current_timezone())
            return timezone.localtime(value).date() > timezone.localdate()
        except (AttributeError, TypeError, ValueError):
            return False

    def has_unfinished_aired_seasons(self):
        """Return whether any season still has aired episodes left to finish.

        A Planning season the provider dates in the future, with no history
        and no released episode, is announced rather than unfinished, so it
        never blocks the show from completing (rule R3 in
        docs/architecture/status-rules.md). A season with no provider date
        still counts: undated is not the same as unaired.
        """
        unfinished = list(
            self.seasons.filter(item__season_number__gt=0).exclude(
                status=Status.COMPLETED.value,
            ),
        )
        announced = [
            season
            for season in unfinished
            if season.status == Status.PLANNING.value
            and not self._season_has_local_episode_evidence(season)
        ]
        if len(announced) < len(unfinished):
            return True
        if not announced:
            return False

        metadata = self._fetch_tv_metadata()
        related = metadata.get("related") if isinstance(metadata, dict) else None
        seasons_data = related.get("seasons") if isinstance(related, dict) else None
        air_dates = {}
        for season_data in seasons_data if isinstance(seasons_data, list) else []:
            if isinstance(season_data, dict):
                air_dates.setdefault(
                    season_data.get("season_number"),
                    season_data.get("first_air_date"),
                )
        return any(
            not self._first_air_date_is_future(
                air_dates.get(season.item.season_number),
            )
            for season in announced
        )

    def _season_has_local_episode_evidence(self, season):
        """Return whether local history proves a season has episodes to watch."""
        return (
            season.status == Status.IN_PROGRESS.value
            or season.progress > 0
            or bool(season.available_episode_numbers())
        )

    def _start_next_available_season(
        self,
        min_season_number=0,
    ):
        """Find the next season that has aired and set it to in-progress."""
        min_season_number = int(min_season_number or 0)

        all_seasons = list(
            self.seasons.filter(
                item__season_number__gt=min_season_number,
            ).order_by("item__season_number"),
        )
        candidates = [
            season
            for season in all_seasons
            if season.status != Status.COMPLETED.value
            and season.status not in USER_HELD_STATUSES
        ]

        next_unwatched_season = None
        unaired_candidates = []
        for season in candidates:
            if self._season_has_local_episode_evidence(season):
                next_unwatched_season = season
                break
            unaired_candidates.append(season)

        tv_metadata = None
        related_seasons = []
        related_by_number = {}

        def load_tv_related_seasons():
            nonlocal tv_metadata, related_seasons
            if tv_metadata is not None:
                return
            metadata = self._fetch_tv_metadata()
            tv_metadata = metadata if isinstance(metadata, dict) else {}
            related = (
                tv_metadata.get("related")
                if isinstance(tv_metadata.get("related"), dict)
                else {}
            )
            related_seasons = (
                related.get("seasons")
                if isinstance(related.get("seasons"), list)
                else []
            )
            for season_data in related_seasons:
                if not isinstance(season_data, dict):
                    continue
                season_number = season_data.get("season_number")
                if season_number is None:
                    continue
                try:
                    season_number = int(season_number)
                except (TypeError, ValueError):
                    continue
                related_by_number.setdefault(season_number, season_data)

        if next_unwatched_season is None and unaired_candidates:
            # A planning season with no local history is only promoted when
            # the provider says its first episode has already aired.
            load_tv_related_seasons()
            for season in unaired_candidates:
                related_data = related_by_number.get(season.item.season_number, {})
                if self._first_air_date_has_passed(
                    related_data.get("first_air_date"),
                ):
                    next_unwatched_season = season
                    break

        season_started = False

        if next_unwatched_season is not None:
            if next_unwatched_season.status != Status.IN_PROGRESS.value:
                next_unwatched_season.status = Status.IN_PROGRESS.value
                bulk_update_with_history(
                    [next_unwatched_season],
                    Season,
                    fields=["status"],
                    default_change_reason=NEXT_SEASON_REASON,
                )
            season_started = True
        else:
            # If all existing seasons are watched, start the next one the
            # provider confirms has aired. Future/undated seasons stay
            # planning: completing season N must not create a phantom
            # in-progress season N+1.
            load_tv_related_seasons()
            existing_season_numbers = {
                season.item.season_number for season in all_seasons
            }

            for season_data in related_seasons:
                if not isinstance(season_data, dict):
                    continue
                season_number = season_data.get("season_number")
                if season_number is None:
                    continue
                try:
                    season_number = int(season_number)
                except (TypeError, ValueError):
                    continue
                if (
                    season_number <= min_season_number
                    or season_number in existing_season_numbers
                    or not self._first_air_date_has_passed(
                        season_data.get("first_air_date"),
                    )
                ):
                    continue

                # Use season poster if available, otherwise fallback to TV show poster
                season_image = season_data.get("image") or self.item.image

                item, _ = Item.objects.get_or_create(
                    media_id=self.tracking_media_id,
                    source=self.tracking_source,
                    media_type=MediaTypes.SEASON.value,
                    episode_order=self.active_episode_order,
                    library_media_type=(
                        MediaTypes.ANIME.value
                        if self.item.library_media_type == MediaTypes.ANIME.value
                        else MediaTypes.SEASON.value
                    ),
                    season_number=season_number,
                    defaults={
                        **Item.title_fields_from_metadata(
                            season_data,
                            fallback_title=self.item.title,
                        ),
                        "image": season_image,
                    },
                )

                next_unwatched_season = Season(
                    item=item,
                    user=self.user,
                    related_tv=self,
                    status=Status.IN_PROGRESS.value,
                )
                bulk_create_with_history(
                    [next_unwatched_season],
                    Season,
                    default_change_reason=NEXT_SEASON_REASON,
                )
                season_started = True
                break

        if season_started and self.status != Status.IN_PROGRESS.value:
            self.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [self],
                TV,
                fields=["status"],
                default_change_reason=NEXT_SEASON_REASON,
            )

        return season_started

    def _handle_completed_season(
        self,
        completed_season_number,
    ):
        """Start the next season, or complete the TV show if no seasons remain."""
        if self.status in USER_HELD_STATUSES:
            # The user dropped or paused this show; finishing one of its
            # seasons (often via an import backfill) must not restart it.
            return

        # Both halves of this decision - "is there a next season" and "has the
        # show ended" - need the same provider payload, and this runs once per
        # season inside the home-screen loop. Open a memo for the pass so it is
        # fetched once, and close it after so nothing later reads it stale.
        self._tv_metadata_cache = _UNSET_TV_METADATA
        try:
            self._handle_completed_season_inner(completed_season_number)
        finally:
            # pop, not del: a nested completion on the same instance closes the
            # memo first, and the outer teardown must not then raise.
            self.__dict__.pop("_tv_metadata_cache", None)

    def _handle_completed_season_inner(
        self,
        completed_season_number,
    ):
        """Body of `_handle_completed_season`, run with a metadata memo open."""
        if self._start_next_available_season(
            completed_season_number,
        ):
            return

        if not self.has_unfinished_aired_seasons():
            production_status = self.resolve_production_status()

            if production_status is None:
                # The provider is unreachable, so we cannot tell whether the
                # show has ended. Leave the status alone: defaulting to
                # Completed here would overwrite a status the user set, for a
                # reason they can never see (#375).
                return

            classification = classify_production_status(production_status)

            if classification == PRODUCTION_STATUS_ONGOING:
                # The show is still running, so finishing its current seasons
                # does not finish the show. This holds whatever the user set:
                # Paused and Dropped are their choices too, and completing a
                # still-airing show over them is the same clobbering as #375.
                return

            if classification == PRODUCTION_STATUS_UNKNOWN:
                # The provider reported a status we don't recognize. It may
                # well mean "still running" - a renamed, localized or new
                # value - so treat it as indeterminate rather than guessing
                # the show is over.
                logger.warning(
                    "Unrecognized production status %r for %s (%s, %s);"
                    " leaving status untouched",
                    production_status,
                    self.item.title,
                    self.item.media_id,
                    self.item.source,
                )
                return

            if (
                classification == PRODUCTION_STATUS_ABSENT
                and self.tracking_source != Sources.MANUAL.value
            ):
                # A provider that answers without any status gives no evidence
                # the show has ended. Only a manual show, which has no provider
                # to ask, is finished by watching everything the user added.
                return

            if self.status != Status.COMPLETED.value:
                self.status = Status.COMPLETED.value
                bulk_update_with_history(
                    [self],
                    TV,
                    fields=["status"],
                    default_change_reason="All seasons watched",
                )


class ActiveSeasonManager(MediaManager):
    """Keep archived order history out of active season projections."""

    def get_queryset(self):
        """Return seasons belonging to the active tracking history."""
        return super().get_queryset().filter(order_archived=False)


class Season(Media):
    """Model for seasons of TV shows."""

    related_tv = models.ForeignKey(
        TV,
        on_delete=models.CASCADE,
        related_name="seasons",
    )
    order_archived = models.BooleanField(default=False)
    objects = ActiveSeasonManager()
    all_objects = ImportScopedManager()
    rewatch_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the current rewatch began. Plays before it are kept as"
            " history but don't count towards the current pass."
        ),
    )

    tracker = FieldTracker()

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        constraints = [
            models.UniqueConstraint(
                fields=["related_tv", "item"],
                name="%(app_label)s_season_unique_tv_item",
            ),
        ]

    def __str__(self):
        """Return the title of the media and season number."""
        return f"{self.item.title} S{self.item.season_number}"

    def available_episode_numbers(self):
        """Return released episode numbers known by the local calendar."""
        prefetched_events = getattr(self.item, "prefetched_events", None)
        if prefetched_events is None:
            event_rows = events.models.Event.objects.filter(
                item=self.item,
                content_number__isnull=False,
            ).only("content_number", "datetime")
        else:
            event_rows = prefetched_events

        from events.models import is_released_datetime

        now = timezone.now()
        episode_numbers = set()
        for event in event_rows:
            episode_number = getattr(event, "content_number", None)
            event_datetime = getattr(event, "datetime", None)
            if episode_number is None or event_datetime is None:
                continue
            if (
                event_datetime.year < MIN_VALID_RELEASE_YEAR
                or not is_released_datetime(event_datetime, now)
            ):
                continue
            try:
                episode_numbers.add(int(episode_number))
            except (TypeError, ValueError):
                continue

        return sorted(episode_numbers)

    def next_episode_number(self, episode_numbers=None):
        """Return the next released episode, or none when the season is caught up."""
        if episode_numbers is None:
            episode_numbers = self.available_episode_numbers()

        if not episode_numbers:
            max_progress = getattr(self, "max_progress", None)
            try:
                max_progress = int(max_progress)
            except (TypeError, ValueError):
                max_progress = None
            if max_progress and max_progress > 0:
                episode_numbers = range(1, max_progress + 1)

        return providers.tmdb.find_next_episode(
            self.progress,
            [{"episode_number": number} for number in episode_numbers],
        )

    @tracker  # postpone field reset until after the save
    @serialized_state_write
    def save(self, *args, **kwargs):
        """Save the media instance."""
        # if related_tv is not set
        if self.related_tv_id is None:
            self.related_tv = self.get_tv()

        is_create = self._state.adding
        self._include_updated_at(kwargs)
        super(Media, self).save(*args, **kwargs)

        # A season created directly as COMPLETED (rather than transitioned into
        # it) must still create the remaining episode watch records.
        completed_on_create = is_create and self.status == Status.COMPLETED.value
        if (
            not is_create and self.tracker.has_changed("status")
        ) or completed_on_create:
            if self.status == Status.COMPLETED.value:
                try:
                    season_metadata = providers.services.get_media_metadata(
                        MediaTypes.SEASON.value,
                        self.item.media_id,
                        self.item.source,
                        [self.item.season_number],
                    )
                    episodes_to_create = self.get_remaining_eps(
                        season_metadata,
                        end_date=getattr(self, "_pending_end_date", _UNSET_END_DATE),
                    )
                    if episodes_to_create:
                        bulk_create_with_history(
                            episodes_to_create,
                            Episode,
                        )
                        from app import signals, statistics_sync

                        statistics_sync.mark_rows(
                            self.user_id,
                            episodes_to_create,
                            reason="season_completed_fan_out",
                        )
                        signals.invalidate_bulk_episode_history(
                            self.user_id,
                            episodes_to_create,
                            reason="season_completed_fan_out",
                        )

                    # Completing the season ends any pass it was in.
                    if self.rewatch_started_at is not None:
                        self.rewatch_started_at = None
                        self._invalidate_episode_stats()
                        bulk_update_with_history(
                            [self],
                            Season,
                            fields=["rewatch_started_at"],
                        )

                    related_tv = self.related_tv
                    if related_tv.status == Status.PLANNING.value:
                        # Marking a season complete starts the show even when
                        # the next season has not aired yet. That next season
                        # stays planning, so this is the only status change.
                        related_tv.status = Status.IN_PROGRESS.value
                        bulk_update_with_history(
                            [related_tv],
                            TV,
                            fields=["status"],
                            default_change_reason="Season completed",
                        )
                    related_tv._handle_completed_season(
                        self.item.season_number,
                    )
                except (
                    providers.services.ProviderAPIError,
                    RequestException,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as error:
                    logger.warning(
                        "Skipping season completion fan-out due to missing"
                        " metadata for %s S%s: %s",
                        self.item.media_id,
                        self.item.season_number,
                        error,
                    )

            elif (
                self.status == Status.DROPPED.value
                and self.related_tv.status != Status.DROPPED.value
            ):
                self.related_tv.status = Status.DROPPED.value
                bulk_update_with_history(
                    [self.related_tv],
                    TV,
                    fields=["status"],
                    default_change_reason="Season dropped",
                )

            elif (
                self.status == Status.IN_PROGRESS.value
                and self.related_tv.status != Status.IN_PROGRESS.value
            ):
                self.related_tv.status = Status.IN_PROGRESS.value
                bulk_update_with_history(
                    [self.related_tv],
                    TV,
                    fields=["status"],
                    default_change_reason="Season started",
                )

            self.item.fetch_releases(delay=True)

        cache_utils.clear_library_view_caches(
            self.user_id,
            media_types=(MediaTypes.SEASON.value,),
        )

    @property
    def progress(self):
        """Return the furthest episode reached in the current explicit watch pass."""
        stats = self._get_episode_stats()
        if not stats["completed_episode_numbers"]:
            return 0
        # _get_episode_stats already limits explicit rewatches by their start
        # date. Uneven lifetime repeat counts do not define another pass (#1584).
        return stats["max_episode_number"]

    @property
    def completed_episode_numbers(self):
        """Return the episode numbers with a completed play in the current pass."""
        return set(self._get_episode_stats()["completed_episode_numbers"])

    @property
    def watched_once_numbers(self):
        """Return the episode numbers completed in any pass, rewatches or not."""
        return set(self._get_episode_stats()["watched_once_numbers"])

    @property
    def watched_once_percentage(self):
        """Return percent of the season watched at least once (0-100), or None."""
        return _percent_of_max(
            len(self._get_episode_stats()["watched_once_numbers"]),
            getattr(self, "max_progress", None),
        )

    @property
    def rewatch_percentage(self):
        """Return percent of the open rewatch pass completed, or None outside one."""
        if not self.is_rewatching:
            return None
        return _percent_of_max(
            self.completed_episode_count,
            getattr(self, "max_progress", None),
        )

    @property
    def completed_episode_count(self):
        """Return the number of unique episodes with a completed play."""
        stats = self._get_episode_stats()
        return len(stats["completed_episode_numbers"])

    def _plays_sort_value(self):
        """Return completed-episode count (not furthest position) for plays/time-watched UI."""
        aggregated_progress = getattr(self, "aggregated_progress", None)
        if aggregated_progress is not None:
            return aggregated_progress
        return self.completed_episode_count

    @property
    def progress_percentage(self):
        """Return percent of the season actually watched (0-100), or None.

        Unlike `progress` (furthest episode number touched, kept for #327's
        skip-tolerant behavior), this reflects distinct completed episodes
        against the season's total, for progress-bar style displays.
        """
        max_progress_value = getattr(self, "max_progress", None)
        try:
            max_progress_value = int(max_progress_value)
        except (TypeError, ValueError):
            return None
        if max_progress_value <= 0:
            return None
        percentage = round(self.completed_episode_count / max_progress_value * 100)
        return max(0, min(percentage, 100))

    def _is_fully_watched(self, max_progress=None):
        """Return whether the current pass covers every episode of the season."""
        max_progress_value = max_progress
        if max_progress_value is None:
            max_progress_value = getattr(self, "max_progress", None)
        try:
            max_progress_value = int(max_progress_value)
        except (TypeError, ValueError):
            return False
        return (
            max_progress_value > 0
            and self.completed_episode_count >= max_progress_value
        )

    def _would_be_immediately_complete(self, started_at, max_progress=None):
        """Return whether opening a pass from started_at leaves nothing to rewatch.

        Checked without persisting: swaps in the candidate start, asks
        `_is_fully_watched` (which reads plays through the pass window it
        implies), then puts the season's actual state back.
        """
        original_started_at = self.rewatch_started_at
        self.rewatch_started_at = started_at
        self._invalidate_episode_stats()
        try:
            return self._is_fully_watched(max_progress)
        finally:
            self.rewatch_started_at = original_started_at
            self._invalidate_episode_stats()

    def derived_status_from_episode_progress(
        self,
        max_progress=None,
        *,
        resume_paused=False,
    ):
        """Return the effective season status from local episode history.

        Paused is sticky by default so a season the user parked keeps reading
        as paused everywhere it is displayed. `resume_paused` lifts that for
        the one write that disproves it: logging a new episode is the user
        coming back, so the season resumes. Dropped is never lifted -- that is
        the only status where the user said they are not returning.
        """
        if self.status == Status.DROPPED.value:
            return self.status
        if self.status == Status.PAUSED.value and not resume_paused:
            return self.status

        completed_episode_count = self.completed_episode_count
        progress_value = self.progress

        if self._is_fully_watched(max_progress):
            return Status.COMPLETED.value
        if progress_value > 0 or completed_episode_count > 0:
            return Status.IN_PROGRESS.value
        if self.status == Status.PLANNING.value:
            return Status.PLANNING.value
        return self.status

    def promote_to_completed_if_fully_watched(self, max_progress=None):
        """Persist a completed season when local episode history proves it."""
        desired_status = self.derived_status_from_episode_progress(
            max_progress=max_progress,
        )
        if desired_status != Status.COMPLETED.value or self.status in {
            Status.COMPLETED.value,
            Status.IN_PROGRESS.value,
        }:
            return False

        self.status = Status.COMPLETED.value
        update_fields = ["status"]
        if self.rewatch_started_at is not None:
            self.rewatch_started_at = None
            self._invalidate_episode_stats()
            update_fields.append("rewatch_started_at")
        bulk_update_with_history([self], Season, fields=update_fields)
        self.related_tv._handle_completed_season(self.item.season_number)
        return True

    @property
    def is_rewatching(self):
        """Return whether the season is in an open rewatch pass."""
        return self.rewatch_started_at is not None

    def refresh_from_db(self, *args, **kwargs):
        """Drop cached episode stats so a reload can't serve the old window."""
        super().refresh_from_db(*args, **kwargs)
        self._invalidate_episode_stats()

    @property
    def pass_started_on(self):
        """Return the start of the day the current pass began, or None.

        Watch dates are often date-only and land at midnight, so a pass started
        at 09:00 has to accept a play dated that same morning. Comparing against
        the day boundary does that without letting an old play count just
        because its row happens to have been written during the pass.
        """
        if self.rewatch_started_at is None:
            return None
        return timezone.localtime(self.rewatch_started_at).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    def play_counts_for_pass(self, episode):
        """Return whether a play belongs to the season's current pass."""
        if self.rewatch_started_at is None:
            return True
        played_at = episode.end_date or episode.created_at
        if played_at is None:
            return True
        rewatch_started = self.rewatch_started_at
        if timezone.is_naive(played_at):
            played_at = timezone.make_aware(played_at, UTC)
        if timezone.is_naive(rewatch_started):
            rewatch_started = timezone.make_aware(rewatch_started, UTC)
        if played_at >= rewatch_started:
            return True
        started_on = self.pass_started_on
        if started_on is None:
            return False
        if timezone.is_naive(started_on):
            started_on = timezone.make_aware(started_on, UTC)
        if played_at < started_on:
            return False
        # Date-only plays are stored at local midnight, so compare in the same
        # local zone `pass_started_on` was built in. Reading the raw UTC value
        # here would see a non-UTC deployment's local midnight as the previous
        # day's 22:00Z and drop the play from its own pass.
        local_played_at = timezone.localtime(played_at)
        return (
            local_played_at.hour == 0
            and local_played_at.minute == 0
            and local_played_at.second == 0
            and local_played_at.microsecond == 0
        )

    def _invalidate_episode_stats(self):
        """Drop cached episode stats after the pass or its plays changed."""
        if hasattr(self, "_episode_stats_cache"):
            del self._episode_stats_cache

    def start_rewatch(self, started_at=None, max_progress=None):
        """Begin a new pass, keeping every existing play as history.

        A no-op if a pass is already open, so a duplicate submission or
        retry can't silently push the cutoff later and strand plays
        logged against the original one as pre-cutoff history. Raises
        RewatchAlreadyCompleteError if the plays already logged at or
        after `started_at` already cover the season — opening a pass
        that would immediately close itself again is more confusing than
        just refusing it. Looks up its own episode count when
        `max_progress` isn't supplied — see `stop_rewatch` for why that
        can't be a shared value.
        """
        if self.rewatch_started_at is not None:
            return
        is_explicit_start = started_at is not None
        started_at = started_at or timezone.now()
        if max_progress is None:
            from app.models.media import BasicMedia  # avoid a module-load cycle

            BasicMedia.objects.annotate_max_progress([self], MediaTypes.SEASON.value)
            max_progress = getattr(self, "max_progress", None)

        if is_explicit_start and self._would_be_immediately_complete(started_at, max_progress):
            already_complete_msg = "Every episode is already watched from that date."
            raise RewatchAlreadyCompleteError(already_complete_msg)

        self.rewatch_started_at = started_at
        self.status = Status.IN_PROGRESS.value
        self._invalidate_episode_stats()
        bulk_update_with_history(
            [self],
            Season,
            fields=["rewatch_started_at", "status"],
        )

    def stop_rewatch(self, max_progress=None):
        """Abandon the current pass; the full history decides the status again.

        Looks up its own episode count when `max_progress` isn't supplied,
        so a caller resolving several seasons at once (e.g.
        `TV.stop_rewatch`) doesn't have to share one value across seasons
        that can have different episode counts.
        """
        if self.rewatch_started_at is None:
            return
        if max_progress is None:
            from app.models.media import BasicMedia  # avoid a module-load cycle

            BasicMedia.objects.annotate_max_progress([self], MediaTypes.SEASON.value)
            max_progress = getattr(self, "max_progress", None)
        self.rewatch_started_at = None
        self._invalidate_episode_stats()
        if self._is_fully_watched(max_progress):
            self.status = Status.COMPLETED.value
        bulk_update_with_history(
            [self],
            Season,
            fields=["rewatch_started_at", "status"],
        )

    def finish_rewatch_if_complete(self, max_progress=None):
        """Close the pass and complete the season once every episode is replayed.

        Does not fan out to the parent TV — callers that care (e.g.
        advancing to the next season) do that themselves, since a bulk
        caller closing several seasons at once needs that to happen after
        every season in the batch has settled, not mid-loop.
        """
        if self.rewatch_started_at is None:
            return False
        if (
            self.derived_status_from_episode_progress(max_progress=max_progress)
            != Status.COMPLETED.value
        ):
            return False

        self.rewatch_started_at = None
        self.status = Status.COMPLETED.value
        self._invalidate_episode_stats()
        bulk_update_with_history(
            [self],
            Season,
            fields=["rewatch_started_at", "status"],
        )
        return True

    def _get_episode_stats(self):
        """Return cached episode stats for this season."""
        cached = getattr(self, "_episode_stats_cache", None)
        if cached is not None:
            return cached

        # Filter in Python: season querysets prefetch `episodes`, and a
        # queryset filter here would bypass that cache and turn every list
        # page into an N+1. Without a prefetch, join the catalogue rows so
        # reading each episode number below is not one query per episode.
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("episodes")
        all_episodes = list(
            prefetched if prefetched is not None else self.episodes.select_related("item")
        )
        episodes = [ep for ep in all_episodes if self.play_counts_for_pass(ep)]
        completed_episode_numbers = set()
        max_episode_number = 0

        # Every episode ever finished, in any pass: "have I watched it all once".
        watched_once_numbers = {
            ep.item.episode_number
            for ep in all_episodes
            if (
                ep._card_completed_count
                if getattr(ep, "_card_is_summary", False)
                else ep.status == Status.COMPLETED.value
            )
        }

        for ep in episodes:
            ep_num = ep.item.episode_number
            count = (
                ep._card_completed_count
                if getattr(ep, "_card_is_summary", False)
                else int(ep.status == Status.COMPLETED.value)
            )
            if count:
                completed_episode_numbers.add(ep_num)
            if (
                (
                    (getattr(ep, "_card_completed_count", 0) or getattr(ep, "_card_dropped_count", 0))
                    if getattr(ep, "_card_is_summary", False)
                    else ep.status in {Status.COMPLETED.value, Status.DROPPED.value}
                )
                and ep_num
                and ep_num > max_episode_number
            ):
                max_episode_number = ep_num

        cached = {
            "completed_episode_numbers": completed_episode_numbers,
            "watched_once_numbers": watched_once_numbers,
            "max_episode_number": max_episode_number,
        }
        self._episode_stats_cache = cached
        return cached

    @property
    def progressed_at(self):
        """Return the date when the last episode was watched."""
        dates = [
            getattr(episode, "_card_last_end", episode.end_date)
            for episode in self.episodes.all()
            if getattr(episode, "_card_last_end", episode.end_date) is not None
        ]
        return max(dates) if dates else None

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            getattr(episode, "_card_first_end", episode.end_date)
            for episode in self.episodes.all()
            if getattr(episode, "_card_first_end", episode.end_date) is not None
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            getattr(episode, "_card_last_end", episode.end_date)
            for episode in self.episodes.all()
            if getattr(episode, "_card_last_end", episode.end_date) is not None
        ]
        return max(dates) if dates else None

    def increase_progress(self, watch_operation_id=None):
        """Watch the next episode of the season."""
        if watch_operation_id:
            from app import fork_services_episode

            operation_id = fork_services_episode.normalize_watch_operation_id(
                watch_operation_id,
            )
            claimed = (
                Episode.objects.filter(watch_operation_id=operation_id)
                .select_related("related_season", "item")
                .first()
            )
            if claimed is not None:
                return fork_services_episode.resolve_episode_watch_replay(
                    claimed,
                    user_id=self.user_id,
                    season_id=self.id,
                )
        season_metadata = providers.services.get_media_metadata(
            MediaTypes.SEASON.value,
            self.item.media_id,
            self.item.source,
            [self.item.season_number],
        )
        episodes = season_metadata["episodes"]

        next_episode_number = providers.tmdb.find_next_episode(self.progress, episodes)

        now = timezone.now()

        if next_episode_number:
            return self.watch(
                next_episode_number,
                now,
                watch_operation_id=watch_operation_id,
            )
        logger.info("No more episodes to watch.")
        return None

    def watch(
        self,
        episode_number,
        end_date,
        watch_operation_id=None,
        external_id=None,
        **episode_fields,
    ):
        """Create or add a repeat to an episode of the season."""
        from app import fork_services_episode

        item = self.get_episode_item(episode_number)

        result = fork_services_episode.create_episode_watch(
            self,
            item,
            end_date,
            watch_operation_id=watch_operation_id,
            external_id=external_id,
            **episode_fields,
        )
        if result.created:
            logger.info("%s created successfully.", result.episode)
            cache_utils.clear_library_view_caches(
                self.user_id,
                media_types=(MediaTypes.EPISODE.value,),
            )

            entry_source = episode_fields.get("entry_source")
            if entry_source and self.entry_source != entry_source:
                self.entry_source = entry_source
                self.save(update_fields=["entry_source"])
        return result

    def decrease_progress(self):
        """Unwatch the current episode of the season."""
        self.unwatch(self.progress)

    def unwatch(self, episode_number, external_id=None):
        """Unwatch the episode instance.

        Targets the play matching ``external_id`` when one is given, otherwise
        the most recent play of the episode.
        """
        item = self.get_episode_item(episode_number)

        # The latest finished play, never an open one ahead of it: databases
        # disagree on where a NULL end date sorts.
        episodes = Episode.objects.filter(
            related_season=self,
            item=item,
        ).order_by(F("end_date").desc(nulls_last=True), "-created_at")

        if external_id:
            episode = episodes.filter(external_id=external_id).first()
        else:
            episode = episodes.first()

        if episode is None:
            logger.warning(
                "Episode %s does not exist.",
                self.item,
            )
            return

        # Get count before deletion for logging
        remaining_count = episodes.count() - 1

        episode.delete()
        logger.info(
            "Deleted %s S%02dE%02d (%d remaining instances)",
            self.item.title,
            self.item.season_number,
            episode_number,
            remaining_count,
        )

        # Re-evaluate season/TV status after deletion so completed shows don't stay "In progress"
        if hasattr(self, "_episode_stats_cache"):
            delattr(self, "_episode_stats_cache")
        self._sync_status_after_episode_change()
        cache_utils.clear_library_view_caches(
            self.user_id,
            media_types=(MediaTypes.EPISODE.value,),
        )

    def _sync_status_after_episode_change(self, *, max_progress=None, plays_added=False):
        """Recalculate season (and TV) status from local episode history.

        `max_progress` is the provider's episode count when the caller already
        has it; otherwise release events and the local count stand in.
        `plays_added` marks a write that logged new plays (bulk Episode Plays):
        like `Episode.save`, it resumes a paused season and completes an
        in-progress one instead of reading In progress as a manual reopen.

        Whether the show is finished is never decided here: a season that
        becomes complete hands off to `TV._handle_completed_season`, the one
        place that checks the provider's production status.
        """
        if self.status == Status.DROPPED.value:
            return
        if self.status == Status.PAUSED.value and not plays_added:
            return

        # What episodes do we have logged?
        episode_numbers = set(
            self.episodes.filter(
                status__in=[Status.COMPLETED.value, Status.DROPPED.value],
            ).values_list("item__episode_number", flat=True),
        )
        episode_numbers.discard(None)

        # Best local hint for total episodes: release events in the DB
        from events.models import released_q

        total_eps = (
            events.models.Event.objects.filter(
                item=self.item,
                content_number__isnull=False,
            )
            .filter(released_q("datetime", timezone.now()))
            .aggregate(max_ep=Max("content_number"))["max_ep"]
            or 0
        )
        local_total = self.item.local_season_episode_count or 0
        known_total = max_progress or total_eps or local_total or None

        # Keep an explicit empty-history fallback: the shared derived-status
        # helper intentionally preserves the current status when there is no
        # episode evidence, which would leave an un-watched season completed
        # after its last play is removed.
        if not episode_numbers:
            desired_status = Status.PLANNING.value
        else:
            desired_status = self.derived_status_from_episode_progress(
                max_progress=known_total,
                resume_paused=plays_added,
            )

            # A manually reopened season is deliberately kept in progress even
            # when the current play history still covers every episode.
            if (
                desired_status == Status.COMPLETED.value
                and self.status == Status.IN_PROGRESS.value
                and not plays_added
            ):
                desired_status = Status.IN_PROGRESS.value

        became_completed = (
            desired_status == Status.COMPLETED.value
            and self.status != Status.COMPLETED.value
        )
        if desired_status and self.status != desired_status:
            self.status = desired_status
            bulk_update_with_history([self], Season, fields=["status"])
        if plays_added:
            self.finish_rewatch_if_complete(max_progress=known_total)

        # Align the parent TV unless it was dropped explicitly
        tv = getattr(self, "related_tv", None)
        if not tv or tv.status == Status.DROPPED.value or not desired_status:
            return
        if desired_status == Status.COMPLETED.value and (
            became_completed
            or plays_added
            or not tv.has_unfinished_aired_seasons()
        ):
            if tv.status == Status.PLANNING.value:
                # Logged plays mean the show has started, whether or not
                # the handoff below goes on to finish it.
                tv.status = Status.IN_PROGRESS.value
                bulk_update_with_history([tv], TV, fields=["status"])
            # Starts the next season, or completes the show only when the
            # provider says it has ended (a returning series stays open).
            # Also runs on a re-sync with every season complete, so a show
            # left open by a provider outage is finished once it answers.
            tv._handle_completed_season(self.item.season_number)
            return
        if tv.status != Status.IN_PROGRESS.value:
            tv.status = Status.IN_PROGRESS.value
            bulk_update_with_history([tv], TV, fields=["status"])

    def get_tv(self):
        """Get related TV instance for a season and create it if it doesn't exist."""
        if self.item.episode_order_id:
            return TV.objects.get(user=self.user, item_id=self.item.episode_order.show_id)
        # Scope to the season's own bucket (anime vs. non-anime) so a season
        # is never silently attached to a TV row from the show's other
        # identity when both exist — see #623.
        is_anime_bucket = self.item.library_media_type == MediaTypes.ANIME.value

        def _bucket_scoped(queryset):
            if is_anime_bucket:
                return queryset.filter(item__library_media_type=MediaTypes.ANIME.value)
            return queryset.exclude(item__library_media_type=MediaTypes.ANIME.value)

        try:
            tv = _bucket_scoped(
                TV.objects.filter(
                    item__media_id=self.item.media_id,
                    item__media_type=MediaTypes.TV.value,
                    item__season_number=None,
                    item__source=self.item.source,
                    user=self.user,
                ),
            ).get()
        except TV.MultipleObjectsReturned:
            tv = (
                _bucket_scoped(
                    TV.objects.filter(
                        item__media_id=self.item.media_id,
                        item__media_type=MediaTypes.TV.value,
                        item__season_number=None,
                        item__source=self.item.source,
                        user=self.user,
                    ),
                )
                .order_by("id")
                .first()
            )
            logger.warning(
                "Multiple TV records for media_id=%s source=%s user=%s — using oldest",
                self.item.media_id,
                self.item.source,
                self.user_id,
            )
            return tv
        except TV.DoesNotExist:
            fallback_title = self.item.series_name or self.item.title
            try:
                tv_metadata = providers.services.get_media_metadata(
                    MediaTypes.TV.value,
                    self.item.media_id,
                    self.item.source,
                )
                season_count = tv_metadata.get("details", {}).get("seasons")
                if season_count is None:
                    season_count = len(
                        tv_metadata.get("related", {}).get("seasons", [])
                    )
            except (
                Exception
            ) as exc:  # pragma: no cover - defensive for test/no-network paths
                logger.warning(
                    "Could not fetch TV metadata for media_id=%s while creating season parent: %s",
                    self.item.media_id,
                    exc,
                )
                tv_metadata = {
                    "title": fallback_title,
                    "localized_title": fallback_title,
                    "original_title": None,
                    "image": self.item.image,
                    "details": {},
                    "related": {"seasons": []},
                }
                season_count = None

            # creating tv with multiple seasons from a completed season
            if self.status == Status.COMPLETED.value and season_count > 1:
                status = Status.IN_PROGRESS.value
            else:
                status = self.status

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.TV.value,
                library_media_type=self.item.library_media_type,
                defaults={
                    **Item.title_fields_from_metadata(
                        tv_metadata,
                        fallback_title=fallback_title,
                    ),
                    "image": tv_metadata.get("image") or self.item.image,
                },
            )

            tv = TV(
                item=item,
                score=None,
                status=status,
                notes="",
                user=self.user,
            )

            # save_base to avoid custom save method
            TV.save_base(tv)

            logger.info("%s did not exist, it was created successfully.", tv)

        return tv

    def get_remaining_eps(self, season_metadata, end_date=_UNSET_END_DATE):
        """Return episodes needed to complete a season."""
        plays = Episode.objects.filter(related_season=self) if self.pk else Episode.objects.none()
        started_on = self.pass_started_on
        if started_on is None:
            latest_watched_ep_num = plays.aggregate(
                latest_watched_ep_num=Max("item__episode_number"),
            )["latest_watched_ep_num"]
        else:
            # Narrow in SQL, then apply `play_counts_for_pass` so the plays
            # treated as watched here are exactly the ones that count toward
            # the pass's progress. Any divergence between the two leaves a
            # season unable to ever reach max_progress.
            plays = plays.filter(
                models.Q(end_date__gte=started_on)
                | models.Q(end_date__isnull=True, created_at__gte=started_on),
            ).select_related("item")
            latest_watched_ep_num = max(
                (
                    play.item.episode_number
                    for play in plays
                    if play.item.episode_number is not None
                    and self.play_counts_for_pass(play)
                ),
                default=None,
            )

        if latest_watched_ep_num is None:
            latest_watched_ep_num = 0

        episodes_to_create = []

        # Completion reuses catalogue Items, but still runs the same metadata
        # updates and save hooks below. New identities keep get_or_create's
        # uniqueness/race handling and normal creation signals.
        existing_items = {}
        if not self.item.episode_order_id:
            season_bucket = self.item.library_media_type
            episode_bucket = (
                season_bucket
                if season_bucket and season_bucket != MediaTypes.SEASON.value
                else MediaTypes.EPISODE.value
            )
            for item in Item.objects.filter(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.EPISODE.value,
                library_media_type=episode_bucket,
                season_number=self.item.season_number,
                episode_number__gt=latest_watched_ep_num,
            ).defer("watch_providers"):
                number = item.episode_number
                # Preserve get_or_create's MultipleObjectsReturned semantics
                # for ambiguous identities across episode-order catalogues.
                existing_items[number] = None if number in existing_items else item

        # Calculate current time once before the loop
        now = timezone.now().replace(second=0, microsecond=0)

        # Create Episode objects for the remaining episodes
        for episode in reversed(season_metadata["episodes"]):
            if episode["episode_number"] <= latest_watched_ep_num:
                break

            item = self.get_episode_item(
                episode["episode_number"], season_metadata,
                existing_item=existing_items.get(episode["episode_number"]),
            )

            # An explicit end_date (including None) from the completion form
            # applies uniformly; otherwise fall back to the user's preference.
            if end_date is _UNSET_END_DATE:
                resolved_end_date = self.user.resolve_watch_date(
                    now, episode.get("air_date")
                )
            else:
                resolved_end_date = end_date

            episode_db = Episode(
                related_season=self,
                item=item,
                end_date=resolved_end_date,
            )
            episodes_to_create.append(episode_db)

        return episodes_to_create

    def get_episode_item(self, episode_number, season_metadata=None, *, existing_item=None):
        """Get the episode item instance, create it if it doesn't exist."""
        if self.item.episode_order_id:
            from app.services.episode_coordinates import InvalidEpisodeCoordinateError

            item = Item.objects.filter(
                episode_order_id=self.item.episode_order_id,
                media_type=MediaTypes.EPISODE.value,
                season_number=self.item.season_number,
                episode_number=int(episode_number),
            ).first()
            if item is None:
                raise InvalidEpisodeCoordinateError(  # noqa: TRY003
                    "Episode is absent from this order.",  # noqa: EM101
                )
            return item
        if not season_metadata:
            season_metadata = providers.services.get_media_metadata(
                MediaTypes.SEASON.value,
                self.item.media_id,
                self.item.source,
                [self.item.season_number],
            )

        from app import helpers

        image = settings.IMG_NONE
        runtime_minutes = None
        release_datetime = None
        matched_episode = {}
        tvdb_episode_images = {}
        normalized_episode_number = int(episode_number)

        if (
            isinstance(season_metadata, dict)
            and isinstance(season_metadata.get("episodes"), list)
        ):
            from app.services.episode_coordinates import (
                InvalidEpisodeCoordinateError,
                cleanup_episode_history_for_season,
                resolve_episode_coordinate,
            )

            try:
                matched_episode = resolve_episode_coordinate(
                    self.item.media_id,
                    self.item.source,
                    self.item.season_number,
                    normalized_episode_number,
                    season_metadata=season_metadata,
                ).episode
            except InvalidEpisodeCoordinateError:
                cleanup_episode_history_for_season(self, normalized_episode_number)
                raise

        if self.item.source == Sources.TMDB.value:
            if isinstance(season_metadata, dict):
                tvdb_episode_images = season_metadata.get("_tvdb_episode_image_map")
                if tvdb_episode_images is None:
                    tvdb_episode_images = providers.tmdb.get_tvdb_episode_image_map(
                        season_metadata.get("tvdb_id"),
                        season_metadata.get("season_number") or self.item.season_number,
                        tmdb_media_id=self.item.media_id,
                    )
                    season_metadata["_tvdb_episode_image_map"] = tvdb_episode_images
            else:
                tvdb_episode_images = providers.tmdb.get_tvdb_episode_image_map(
                    season_metadata.get("tvdb_id"),
                    season_metadata.get("season_number") or self.item.season_number,
                    tmdb_media_id=self.item.media_id,
                )

        if isinstance(season_metadata, dict):
            episodes_by_number = season_metadata.get("_episodes_by_number")
            if episodes_by_number is None:
                episodes_by_number = {}
                for episode in season_metadata.get("episodes") or []:
                    if not isinstance(episode, dict):
                        continue
                    try:
                        episode_key = int(episode["episode_number"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    episodes_by_number[episode_key] = episode
                season_metadata["_episodes_by_number"] = episodes_by_number
            matched_episode = episodes_by_number.get(normalized_episode_number, {})
        else:
            for episode in season_metadata["episodes"]:
                try:
                    metadata_episode_number = int(episode["episode_number"])
                except (KeyError, TypeError, ValueError):
                    continue
                if metadata_episode_number == normalized_episode_number:
                    matched_episode = episode
                    break

        if matched_episode:
            image = helpers.first_real_image(
                (
                    f"https://image.tmdb.org/t/p/original{matched_episode['still_path']}"
                    if matched_episode.get("still_path")
                    else None
                ),
                tvdb_episode_images.get(str(episode_number)),
                matched_episode.get("image"),
            )

            # Extract runtime from episode metadata. TMDB sends an integer
            # number of minutes, manual items whatever the custom-metadata form
            # stored (a string), so compare only after coercing.
            runtime_minutes = _runtime_minutes(matched_episode.get("runtime"))

            # Extract release_datetime from episode air_date
            air_date = matched_episode.get("air_date")
            if air_date:
                from events.calendar.helpers import date_parser

                with contextlib.suppress(ValueError, TypeError):
                    # Providers return a date-only value (TMDB as a
                    # "YYYY-MM-DD" string, TVDB sometimes as a naive
                    # datetime) with no real-world timezone attached - it's
                    # a calendar date, not a local wall-clock moment. Store
                    # the unknown-time sentinel so release state treats it as
                    # an all-day date instead of counting it from midday UTC.
                    release_datetime = (
                        air_date
                        if hasattr(air_date, "year") and timezone.is_aware(air_date)
                        else date_parser(air_date)
                    )

        # An episode's library bucket should mirror the show's grouping bucket
        # (e.g. 'tv'/'anime' for grouped anime), never the season's own 'season'
        # type. Copying the season's 'season' bucket onto episodes is what
        # produced the stray ('episode','season') items, so fall back to
        # 'episode' whenever the season is in its own (non-grouped) bucket.
        season_bucket = self.item.library_media_type
        episode_bucket = (
            season_bucket
            if season_bucket and season_bucket != MediaTypes.SEASON.value
            else MediaTypes.EPISODE.value
        )

        item, created = existing_item, False
        if item is None:
            item, created = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.EPISODE.value,
                library_media_type=episode_bucket,
                season_number=self.item.season_number,
                episode_number=normalized_episode_number,
                defaults={
                    **Item.title_fields_from_episode_metadata(
                        matched_episode,
                        fallback_title=self.item.title,
                    ),
                    "image": image,
                    "runtime_minutes": runtime_minutes,
                    "release_datetime": release_datetime,
                },
            )

        # Update fields if not set and we have them now
        updated = False
        if not created:
            update_fields = []
            title_fields = Item.title_fields_from_episode_metadata(
                matched_episode,
                fallback_title=self.item.title,
            )
            for field_name, value in title_fields.items():
                if getattr(item, field_name) != value:
                    setattr(item, field_name, value)
                    update_fields.append(field_name)
                    updated = True
            if item.library_media_type != episode_bucket:
                item.library_media_type = episode_bucket
                update_fields.append("library_media_type")
                updated = True
            if not item.runtime_minutes and runtime_minutes:
                item.runtime_minutes = runtime_minutes
                update_fields.append("runtime_minutes")
                updated = True
            if not item.release_datetime and release_datetime:
                item.release_datetime = release_datetime
                update_fields.append("release_datetime")
                updated = True
            if updated:
                item.save(update_fields=update_fields)
        elif created:
            # Ensure runtime and release_datetime are set for newly created items
            needs_save = False
            if runtime_minutes and not item.runtime_minutes:
                item.runtime_minutes = runtime_minutes
                needs_save = True
            if release_datetime and not item.release_datetime:
                item.release_datetime = release_datetime
                needs_save = True
            if needs_save:
                item.save(
                    update_fields=[
                        "library_media_type",
                        "runtime_minutes",
                        "release_datetime",
                    ],
                )

        return item


class ActiveEpisodeManager(ImportScopedManager):
    """Exclude retained migration records from watch projections."""

    def get_queryset(self):
        """Return active watches only; all_objects retains archived history."""
        return super().get_queryset().filter(
            order_archived=False,
            rating_only=False,
        )


class PlayEpisodeManager(ImportScopedManager):
    """Every play, archived or not, but never a rating-only row."""

    def get_queryset(self):
        """Return plays only; `ratings` is the one manager that sees the rest."""
        return super().get_queryset().filter(rating_only=False)


class Episode(models.Model):
    """Model for episodes of a season."""

    tracker = FieldTracker(fields=["status", "dropped"])
    order_archived = models.BooleanField(default=False)
    history = HistoricalRecords(
        cascade_delete_history=True,
        excluded_fields=[
            "item",
            "related_season",
            "created_at",
            "score",
            "scored_at",
            "watch_operation_id",
            "external_id",
            "rating_only",
            # `start_date` and `status` are tracked: a play can be left in
            # progress, and the history modal must tell it apart from a finish
            # without a date (issues #377, #1278).
            "notes",
            "entry_source",
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    watch_operation_id = models.UUIDField(null=True, unique=True, editable=False)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, null=True)
    related_season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    end_date = models.DateTimeField(null=True, blank=True)
    start_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED.value,
    )
    notes = models.TextField(blank=True, default="")
    entry_source = models.CharField(max_length=50, blank=True, default="")
    dropped = models.BooleanField(default=False)
    # A rating for an episode nobody has watched. It is not a play, so the
    # `objects` and `all_objects` managers never return it and no watch
    # projection (progress, History, Statistics) can count it; the first real
    # play of the episode takes its score and deletes it.
    rating_only = models.BooleanField(default=False)
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
    objects = ActiveEpisodeManager()
    all_objects = PlayEpisodeManager()
    # Rating paths only: also sees rating-only rows. Manager order matters,
    # `objects` must stay the default (reverse relations use it).
    ratings = ImportScopedManager()

    class Meta:
        """Meta options for the model."""

        ordering = [
            "related_season",
            "item__episode_number",
            "-end_date",
            "-created_at",
        ]
        # History and Statistics build one day at a time with an end_date
        # range; without this they walked every episode the user watched.
        indexes = [
            models.Index(fields=["end_date"], name="app_episode_end_date_idx"),
            # The user predicate reaches episodes through their seasons; these
            # let each season be probed by date. The partial index serves the
            # open-play branch (`end_date IS NULL AND start_date ...`).
            models.Index(
                fields=["related_season", "end_date"],
                name="app_episode_season_end_idx",
            ),
            models.Index(
                fields=["related_season", "start_date"],
                name="app_episode_season_open_idx",
                condition=models.Q(end_date__isnull=True),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["related_season", "item", "external_id"],
                name="app_episode_unique_episode_external_id",
                condition=models.Q(external_id__isnull=False) & ~models.Q(
                    external_id="",
                ),
            ),
        ]

    def __str__(self):
        """Return the season and episode number."""
        return self.item.__str__()

    @serialized_state_write
    def save(self, *args, **kwargs):
        """Save the episode instance."""
        from app.services.completion import (
            finalize_completed_entry,
            prepare_completed_entry,
        )

        if self.tracker.has_changed("status"):
            self.dropped = self.status == Status.DROPPED.value
        elif self.tracker.has_changed("dropped"):
            self.status = (
                Status.DROPPED.value if self.dropped else Status.COMPLETED.value
            )
        if self.rating_only:
            # Not a play: no completion handling, no season/TV status sync.
            super().save(*args, **kwargs)
            return
        if self._state.adding and self.score is None:
            # A rating belongs to the episode, not to one viewing of it — the
            # score endpoint writes every play at once — so a replay inherits
            # the rating instead of coming back unrated.
            # It keeps the rating's own timestamp too, so a replay does not
            # make an old rating look newly given.
            self.score, self.scored_at = (
                Episode.ratings.filter(
                    related_season_id=self.related_season_id,
                    item_id=self.item_id,
                )
                .exclude(score__isnull=True)
                .values_list("score", "scored_at")
                .first()
            ) or (None, None)

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

        # The play now carries the rating, so the rating-only row is redundant.
        Episode.ratings.filter(
            related_season_id=self.related_season_id,
            item_id=self.item_id,
            rating_only=True,
        ).delete()

        season_number = self.item.season_number
        if season_number is None:
            return
        stored_count = self._stored_season_episode_count()

        # clear prefetch cache to get the updated episodes
        if hasattr(self.related_season, "_episode_stats_cache"):
            delattr(self.related_season, "_episode_stats_cache")
        self.related_season.refresh_from_db()

        max_progress = self._max_progress_for_status_sync(
            season_number,
            stored_count,
        )
        if max_progress is None:
            return
        self.related_season.max_progress = max_progress

        desired_status = self.related_season.derived_status_from_episode_progress(
            max_progress=max_progress,
            resume_paused=True,
        )

        if desired_status != self.related_season.status:
            self.related_season.status = desired_status
            bulk_update_with_history(
                [self.related_season],
                Season,
                fields=["status"],
                default_change_reason=EPISODE_PLAYED_REASON,
            )

        # Close an explicit rewatch once this play completed it, so the next
        # pass starts from a clean window.
        self.related_season.finish_rewatch_if_complete(max_progress=max_progress)

        if desired_status == Status.COMPLETED.value:
            related_tv = self.related_season.related_tv
            if related_tv.status == Status.PLANNING.value:
                # Logging a play starts the show even when that play also
                # completes its only aired season and a later season is only
                # planned. Future planning seasons no longer auto-start, so
                # this is the remaining place that marks the show as watched.
                related_tv.status = Status.IN_PROGRESS.value
                bulk_update_with_history(
                    [related_tv],
                    TV,
                    fields=["status"],
                    default_change_reason=EPISODE_PLAYED_REASON,
                )
            related_tv._handle_completed_season(season_number)
        elif self.related_season.related_tv.status != Status.IN_PROGRESS.value:
            self.related_season.related_tv.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [self.related_season.related_tv],
                TV,
                fields=["status"],
                default_change_reason=EPISODE_PLAYED_REASON,
            )

    # Episode is not a Media subclass; share its score formatting so an episode
    # card shows its rating like every other card.

    @serialized_state_write
    def delete(self, *args, **kwargs):
        """Delete inside the same per-user barrier as a save."""
        return super().delete(*args, **kwargs)

    formatted_score = Media.formatted_score

    @property
    def progress(self):
        """Expose episode number as progress for list rendering/sorting fallbacks."""
        if hasattr(self, "_progress_override"):
            return self._progress_override
        item = getattr(self, "item", None)
        return item.episode_number if item else None

    @progress.setter
    def progress(self, value):
        self._progress_override = value

    @property
    def max_progress(self):
        """Expose related season max progress when available."""
        if hasattr(self, "_max_progress_override"):
            return self._max_progress_override
        related_season = getattr(self, "related_season", None)
        return getattr(related_season, "max_progress", None)

    @max_progress.setter
    def max_progress(self, value):
        self._max_progress_override = value

    @property
    def progressed_at(self):
        """Return the progressed at."""
        return None

    def _stored_season_episode_count(self):
        """Return the season item's stored provider episode count, if usable.

        None for a custom episode order: its seasons are the order's, not the
        provider's, and `get_media_metadata` resolves them from the order.
        Reads the loaded season item when there is one, else one column.
        """
        if str(self.item.media_id).startswith("order_"):
            return None
        season = self.related_season
        if Season._meta.get_field("item").is_cached(season):
            count = season.item.provider_episode_count
        else:
            count = (
                Item.objects.filter(pk=season.item_id)
                .values_list("provider_episode_count", flat=True)
                .first()
            )
        return count if count and count > 0 else None

    def _max_progress_for_status_sync(self, season_number, stored_count):
        """Return the season's episode count for the status sync, or None to skip.

        The stored count may only answer "not finished yet". It is refreshed
        when a payload is fetched, which can be long ago: a season that was
        still airing then has a stored count that is too low, and trusting it
        would complete the season early. So it is used only while the plays
        of this pass are below it; a play that reaches it asks the provider
        payload, exactly as before. The remaining difference is a count that
        is too high (the provider removed episodes), which leaves the season
        open until the next payload fetch lowers it.
        """
        season = self.related_season
        if stored_count and season.completed_episode_count < stored_count:
            return stored_count

        try:
            tv_with_seasons_metadata = providers.services.get_media_metadata(
                "tv_with_seasons",
                self.item.media_id,
                self.item.source,
                [season_number],
            )
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]
            max_progress = len(season_metadata["episodes"])
        except (
            providers.services.ProviderAPIError,
            RequestException,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            max_progress = self._local_season_max_progress()
            if not max_progress:
                logger.warning(
                    "Skipping Episode status sync due to missing metadata for "
                    "%s S%sE%s: %s",
                    self.item.media_id,
                    season_number,
                    self.item.episode_number,
                    error,
                )
                return None
            logger.info(
                "Using locally recorded episode count %s for %s S%s "
                "(provider metadata missing)",
                max_progress,
                self.item.media_id,
                season_number,
            )
            return max_progress

        if max_progress != stored_count and not str(self.item.media_id).startswith(
            "order_",
        ):
            # The payload is in hand, so later plays of this season need not
            # ask for it. A queryset update sends no post_save.
            Item.objects.filter(pk=season.item_id).exclude(
                provider_episode_count=max_progress,
            ).update(provider_episode_count=max_progress)
        return max_progress

    def _local_season_max_progress(self):
        """Return the media-server-sourced episode count for this season, if any.

        Only set when the provider has no metadata for the season, so the count
        is the sole authority available for completion.
        """
        season_item = getattr(self.related_season, "item", None)
        count = getattr(season_item, "local_season_episode_count", None)
        return count if count and count > 0 else None
