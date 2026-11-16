"""Per-season, per-episode breakdown of how much of a show the user has watched.

The result is plain data so the same component can render one show in a modal
today and a row per show on a library progress page later.

Watched means finished at least once, in any pass. While a rewatch is open the
breakdown also reports how much of the current pass is done, using the same
pass-aware numbers as the progress card. The episode list comes from the
provider catalogue. An episode only counts toward a total once it has aired,
and specials and dropped seasons stay out of the overall figures, matching the
card's percentage.
"""

import json
import logging
from dataclasses import dataclass, field

from django.db.models import Prefetch, prefetch_related_objects
from django.utils import timezone
from django.utils.translation import gettext as _

from app.models import Episode, Season, Status
from app.providers import services
from app.services.bulk_episode_tracking import coerce_episode_datetime
from app.services.rewatch_progress import RewatchRun, resolve_rewatch
from app.templatetags.app_tags import media_url

logger = logging.getLogger(__name__)

WATCHED = "watched"
UNWATCHED = "unwatched"
UPCOMING = "upcoming"


@dataclass
class SeasonProgress:
    """One season's episodes and totals."""

    number: int
    title: str
    url: str
    dropped: bool = False
    rewatching: bool = False
    catalogue_missing: bool = False
    count_unaired: bool = False
    episodes: list = field(default_factory=list)
    tracker: Season | None = field(default=None, repr=False, compare=False)

    @property
    def counted(self):
        """Return the episodes that count toward totals.

        Aired or watched ones, except on a season's own page, where the card
        divides by the provider's full episode count, so unaired ones count too.
        """
        if self.count_unaired:
            return self.episodes
        return [ep for ep in self.episodes if ep["state"] != UPCOMING]

    @property
    def total(self):
        """Return how many episodes count toward this season's total."""
        return len(self.counted)

    @property
    def watched(self):
        """Return how many episodes of the season are watched."""
        return sum(ep["state"] == WATCHED for ep in self.episodes)

    @property
    def percent(self):
        """Return the watched percentage, or None when nothing has aired."""
        if self.catalogue_missing or not self.total:
            return None
        return round(self.watched / self.total * 100)

    @property
    def rewatch_watched(self):
        """Return how many watched episodes are also done in the current pass."""
        return sum(ep["state"] == WATCHED and ep["rewatched"] for ep in self.episodes)

    @property
    def rewatch_percent(self):
        """Return the current pass's percentage while rewatching, else None."""
        if self.percent is None or not self.rewatching:
            return None
        return round(self.rewatch_watched / self.total * 100)

    @property
    def is_specials(self):
        """Return whether this is the specials season."""
        return self.number == 0

    @property
    def counts_toward_overall(self):
        """Return whether the season feeds the show's overall figures."""
        return not self.is_specials and not self.dropped and not self.catalogue_missing

    @property
    def minutes_left(self):
        """Return unwatched runtime in minutes, or None if any runtime is unknown."""
        left = [ep for ep in self.counted if ep["state"] == UNWATCHED]
        if any(not ep["runtime"] for ep in left):
            return None
        return sum(ep["runtime"] for ep in left)


@dataclass
class ProgressBreakdown:
    """A show (or one season) with its seasons and overall totals."""

    title: str
    seasons: list
    catalogue_unavailable: bool = False
    single_season: bool = False
    rewatch_run: RewatchRun | None = None

    @property
    def overall(self):
        """Return the seasons that feed the overall figures."""
        if self.single_season:
            return [season for season in self.seasons if not season.catalogue_missing]
        return [season for season in self.seasons if season.counts_toward_overall]

    @property
    def total(self):
        """Return the overall episode total."""
        return sum(season.total for season in self.overall)

    @property
    def watched(self):
        """Return the overall watched count."""
        return sum(season.watched for season in self.overall)

    @property
    def rewatching(self):
        """Return whether any season feeding the overall figures is in a rewatch."""
        return any(season.rewatching for season in self.overall)

    @property
    def rewatch_watched(self):
        """Return the overall count finished in an open pass, over rewatching seasons only."""
        return sum(
            season.rewatch_watched for season in self.overall if season.rewatching
        )

    @property
    def rewatch_total(self):
        """Name the scope denominator, independently of its position on the bar."""
        if self.rewatch_run and not self.rewatch_run.explicit and not self.single_season:
            return len(self.rewatch_run.keys)
        return self.total

    @property
    def rewatch_offset(self):
        """Position a season-only replay over its part of the show."""
        if not self.rewatch_run or self.single_season or not self.total:
            return 0
        keys = [(season.number, ep["number"]) for season in self.overall for ep in season.counted]
        first = self.rewatch_run.keys[0] if self.rewatch_run.keys else None
        return round(keys.index(first) / self.total * 100, 4) if first in keys else 0

    @property
    def rewatch_width(self):
        """The glow's width uses the bar denominator, not the scope denominator."""
        return round(self.rewatch_watched / self.total * 100, 4) if self.total else 0

    @property
    def rewatch_season(self):
        """Identify a local run when shown against the whole show."""
        if self.rewatch_run and not self.single_season and not self.rewatch_run.explicit:
            return self.rewatch_run.season_number
        return None

    @property
    def rewatch_label(self):
        """A scoped label for a glow which may sit in the middle of the bar."""
        if self.rewatch_percent is None:
            return ""
        if self.rewatch_season is not None:
            return _("Season %(season)s rewatch · %(watched)s/%(total)s · %(percent)s%%") % {
                "season": self.rewatch_season,
                "watched": self.rewatch_watched,
                "total": self.rewatch_total,
                "percent": self.rewatch_percent,
            }
        return _("Rewatching · %(percent)s%%") % {"percent": self.rewatch_percent}

    @property
    def run_key(self):
        """Identify only a run visible in this particular progress surface."""
        return self.rewatch_run.key if self.rewatch_run and self.rewatching else ""

    def display_payload(self, instance_id, media_type):
        """Share the same values with Alpine after a fragment refresh."""
        return {
            "id": instance_id, "media_type": media_type,
            "percent": self.percent, "rewatch": self.rewatch_percent,
            "offset": self.rewatch_offset, "width": self.rewatch_width,
            "label": self.rewatch_label, "run_key": self.run_key,
            "caught_up": self.caught_up,
        }

    @property
    def rewatch_percent(self):
        """Return the overall current-pass percentage while rewatching, else None."""
        if not self.rewatching or not self.total:
            return None
        return round(self.rewatch_watched / self.rewatch_total * 100)

    @property
    def left(self):
        """Return how many aired episodes are still unwatched."""
        return self.total - self.watched

    @property
    def percent(self):
        """Return the overall watched percentage, or None."""
        return round(self.watched / self.total * 100) if self.total else None

    @property
    def caught_up(self):
        """Return whether every aired episode is watched and more is still to come.

        Display only (rule R5 in docs/architecture/status-rules.md): a show
        or season that is up to date but not finished keeps its stored status.
        A scope with nothing left to air is Completed, not caught up, and one
        with nothing aired yet has nothing to be caught up on.
        """
        if self.catalogue_unavailable:
            return False
        # A season the provider left out may hold unwatched aired episodes, so
        # a partial response cannot prove the user is caught up.
        if any(
            season.catalogue_missing and not season.is_specials and not season.dropped
            for season in self.seasons
        ):
            return False
        scope = self.overall
        aired = [ep for row in scope for ep in row.episodes if ep["state"] != UPCOMING]
        if not aired or any(ep["state"] == UNWATCHED for ep in aired):
            return False
        # An announced season with no episode list yet still counts as to come.
        return any(
            ep["state"] == UPCOMING for row in scope for ep in row.episodes
        ) or any(not row.episodes for row in scope)

    @property
    def minutes_left(self):
        """Return overall unwatched runtime, or None if any runtime is unknown."""
        parts = [season.minutes_left for season in self.overall]
        if any(part is None for part in parts):
            return None
        return sum(parts)

    @property
    def minutes_left_label(self):
        """Return the unwatched runtime as text, or "" when it is not known."""
        minutes = self.minutes_left
        return format_minutes(minutes) if minutes else ""

    @property
    def open_state_json(self):
        """Return which season rows start expanded, keyed for the template's Alpine state."""
        opened = self.first_open_season
        return json.dumps(
            {
                f"s{season.number}": self.single_season or season.number == opened
                for season in self.seasons
            },
        )

    @property
    def first_open_season(self):
        """Return the number of the first season with something left to watch."""
        for season in self.seasons:
            if season.counts_toward_overall and season.watched < season.total:
                return season.number
        return None


def format_minutes(minutes):
    """Return minutes as "1h 25m" (or "40m")."""
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest}m" if hours else f"{rest}m"


def _runtime_minutes(value):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _season_payloads(media_id, source, season_numbers):
    """Return {season_number: provider payload}, or {} when the provider fails."""
    try:
        with services.interactive_request_scope():
            if season_numbers is None:
                base = services.get_media_metadata("tv", media_id, source)
                seasons = (base.get("related") or {}).get("seasons") or []
                season_numbers = [
                    season["season_number"]
                    for season in seasons
                    if season.get("season_number") is not None
                ]
            if not season_numbers:
                return {}
            grouped = services.get_media_metadata(
                "tv_with_seasons",
                media_id,
                source,
                season_numbers,
            )
    except services.ProviderAPIError:
        logger.warning(
            "Progress breakdown could not load episodes for media_id=%s",
            media_id,
            exc_info=True,
        )
        return {}
    payloads = {}
    for number in season_numbers:
        payload = grouped.get(f"season/{number}")
        if isinstance(payload, dict):
            payloads[number] = payload
    return payloads


def _episode_rows(payload, watched_numbers, pass_numbers, link, now):
    """Merge a catalogue season with the watched numbers into episode rows."""
    rows = {}
    for episode in (payload or {}).get("episodes") or []:
        number = episode.get("episode_number")
        if number is None:
            continue
        air_date = coerce_episode_datetime(episode.get("air_date"))
        if number in watched_numbers:
            state = WATCHED
        elif air_date is not None and air_date <= now:
            state = UNWATCHED
        else:
            state = UPCOMING
        rows[number] = {
            "number": number,
            "title": episode.get("name") or episode.get("title") or "",
            "air_date": air_date,
            "runtime": _runtime_minutes(
                episode.get("runtime_minutes") or episode.get("runtime"),
            ),
            "state": state,
            "rewatched": number in pass_numbers,
        }
    # A play of an episode the catalogue no longer lists still counts for the
    # card, so it has to count here or the two would disagree.
    for number in watched_numbers - rows.keys():
        rows[number] = {
            "number": number,
            "title": "",
            "air_date": None,
            "runtime": 0,
            "state": WATCHED,
            "rewatched": number in pass_numbers,
        }
    ordered = [rows[number] for number in sorted(rows)]
    for row in ordered:
        row["url"] = link(row["number"])
    return ordered


def build_progress_breakdown(*, tv=None, season=None):
    """Return the breakdown for a tracked show, or for one tracked season."""
    if (tv is None) == (season is None):
        msg = "Pass exactly one of tv or season."
        raise ValueError(msg)

    if season is not None:
        tv = season.related_tv
    tracked = list(
        Season.objects.filter(related_tv=tv)
        .select_related("item"),
    )
    if season is not None:
        tracked = [season if row.pk == season.pk else row for row in tracked]
    prefetch_related_objects(
        [row for row in tracked if row.is_rewatching],
        Prefetch("episodes", queryset=Episode.objects.select_related("item").only(
            "id", "related_season_id", "status", "dropped", "end_date", "created_at",
            "item__id", "item__episode_number",
        )),
    )
    media_id, source = tv.tracking_media_id, tv.tracking_source

    title = tv.item.title
    payloads = _season_payloads(media_id, source, None)
    by_number = {tracked_season.item.season_number: tracked_season for tracked_season in tracked}
    watched_by_season = {}
    watched_rows = (
        Episode.objects.filter(related_season__in=tracked, status=Status.COMPLETED.value)
        .order_by().values_list("related_season_id", "item__episode_number").distinct()
    )
    for season_id, episode_number in watched_rows:
        watched_by_season.setdefault(season_id, set()).add(episode_number)
    now = timezone.now()

    def link_for(season_number):
        def link(episode_number):
            return media_url(
                {
                    "media_type": "episode",
                    "source": source,
                    "media_id": media_id,
                    "title": title,
                    "season_number": season_number,
                    "episode_number": episode_number,
                },
            )

        return link

    seasons = []
    for number in sorted(payloads.keys() | by_number.keys()):
        tracked_season = by_number.get(number)
        payload = payloads.get(number)
        watched_numbers = (
            watched_by_season.get(tracked_season.pk, set()) if tracked_season else set()
        )
        pass_numbers = (
            tracked_season.completed_episode_numbers
            if tracked_season and tracked_season.is_rewatching else watched_numbers
        )
        default_title = "Specials" if number == 0 else f"Season {number}"
        seasons.append(
            SeasonProgress(
                number=number,
                title=(payload or {}).get("season_title") or default_title,
                url=media_url(
                    {
                        "media_type": "season",
                        "source": source,
                        "media_id": media_id,
                        "title": title,
                        "season_number": number,
                    },
                ),
                dropped=bool(
                    tracked_season and tracked_season.status == Status.DROPPED.value,
                ),
                rewatching=bool(tracked_season and tracked_season.is_rewatching),
                tracker=tracked_season,
                catalogue_missing=payload is None,
                count_unaired=season is not None and number == season.item.season_number,
                episodes=_episode_rows(
                    payload,
                    watched_numbers,
                    pass_numbers,
                    link_for(number),
                    now,
                ),
            ),
        )

    run = resolve_rewatch(tv, seasons)
    if run is not None:
        for row in seasons:
            row.rewatching = any(key[0] == row.number for key in run.keys)
            for episode in row.episodes:
                episode["rewatched"] = (row.number, episode["number"]) in run.completed
    if season is not None:
        seasons = [row for row in seasons if row.number == season.item.season_number]
    return ProgressBreakdown(
        title=title,
        seasons=seasons,
        rewatch_run=run,
        catalogue_unavailable=not payloads,
        single_season=season is not None,
    )
