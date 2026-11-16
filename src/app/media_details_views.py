import json
import logging
import math
import time

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

from app import (
    carousel as carousel_media,
)
from app import (
    config,
    credits,  # noqa: A004  # app.credits module, not the site builtin
    custom_metadata,
    helpers,
    metadata_utils,
    podcast_views,
    statistics_cache,
)
from app import statistics as stats
from app.activity_builders import (
    _build_detail_activity_state,
    _build_detail_activity_subtitle,
    _get_game_lengths_refresh_lock,
    _normalize_detail_episode_actions,
    _paginate_detail_episodes,
    _queue_game_lengths_refresh,
    _should_queue_game_lengths_refresh,
)
from app.db_retry import run_retryable_db_operation
from app.detail_builders import (
    _apply_cached_hltb_link,
    _build_detail_link_sections,
    _build_detail_person_rows,
    _build_episode_graph_from_season_cache,
    _build_game_lengths_context,
    _build_imdb_rating_context,
    _build_mal_rating_context,
    _build_opencritic_context,
    _build_season_scores_graph,
    _build_series_graph_data,
    _build_stored_season_scores_graph,
    _build_trakt_popularity_context,
    build_book_formats,
)
from app.detail_related import (
    drop_recommendations_if_hidden,
    enrich_detail_related_cards,
    enrich_detail_seasons,
)
from app.log_safety import exception_summary
from app.media_list_views import _collect_reading_activity_day_keys
from app.metadata_sync_views import _build_flat_anime_episode_preview
from app.models import (
    Anime,
    BackfillReconcileState,
    BasicMedia,
    ComicIssue,
    CreditRoleType,
    HardcoverEditionPreference,
    Item,
    MediaTypes,
    Sources,
)
from app.models.episode_runtimes import build_season_runtime_index
from app.providers import services, tmdb
from app.services import metadata_resolution, opencritic_scores, youtube_game_watch
from app.services.metadata_fallback import stored_metadata_fallback
from app.tag_views import (
    _build_detail_tag_sections,
    _detail_request_url,
    _resolve_detail_tag_genres,
)
from app.track_modal_views import _DummyPodcastWrapper
from app.view_constants import (
    DETAIL_CAROUSEL_FRAGMENT,
    DETAIL_SECONDARY_FRAGMENT,
    force_live_metadata_cache_key,
)
from lists.views_helpers import get_public_list_for_item

logger = logging.getLogger(__name__)

RUNTIME_UNKNOWN_AIRED = 999998  # aired but runtime unknown

# How long a podcast show's page renders its stored episodes before a view
# re-reads the feed.
PODCAST_DETAIL_RSS_REFRESH_SECONDS = 15 * 60

# How often the details fragment may drop an item's provider cache and refetch
# because a field is missing. When the provider has no such data, the refetch
# returns the same gap, so without a limit every visit paid for a live call.
DETAIL_FORCED_REFETCH_SECONDS = 24 * 60 * 60


def _detail_refetch_allowed(reason, source, media_type, media_id):
    """Return True at most once per window for this item and reason."""
    return cache.add(
        f"detail_forced_refetch:{reason}:{source}:{media_type}:{media_id}",
        True,
        DETAIL_FORCED_REFETCH_SECONDS,
    )


def _enrich_comic_issues(issues, user, source):
    """Attach user tracking history to each issue dict from the volume issues list."""
    if not issues:
        return issues

    issue_ids = [str(issue["media_id"]) for issue in issues]
    items_qs = Item.objects.filter(
        media_id__in=issue_ids,
        source=source,
        media_type=MediaTypes.COMIC_ISSUE.value,
    )
    item_by_media_id = {item.media_id: item for item in items_qs}

    history_by_media_id = {}
    if user is not None and user.is_authenticated:
        tracked = ComicIssue.objects.filter(
            user=user,
            item__in=item_by_media_id.values(),
        ).select_related("item")
        for entry in tracked:
            history_by_media_id.setdefault(entry.item.media_id, []).append(entry)

    enriched = []
    for issue in issues:
        entry = dict(issue)
        media_id = str(entry["media_id"])
        entry["history"] = history_by_media_id.get(media_id, [])
        entry["item"] = item_by_media_id.get(media_id)
        enriched.append(entry)
    return enriched


def _issue_extra_details(media_metadata, authors_linked):
    """Return (label, text) rows for comic issue details the template lacks.

    Covers what the Metadata editor can set: authors (when none are linked),
    publisher, format, country, languages and genres. Values may be a string
    or a list, so both are flattened to one comma-separated line.
    """
    if not isinstance(media_metadata, dict):
        return []
    details = media_metadata.get("details") or {}
    candidates = [
        (gettext("Author"), None if authors_linked else details.get("author")),
        (gettext("Publisher"), details.get("publisher")),
        (gettext("Format"), details.get("format")),
        (gettext("Country"), details.get("country")),
        (gettext("Languages"), details.get("languages")),
        (gettext("Genres"), media_metadata.get("genres")),
    ]
    rows = []
    for label, value in candidates:
        values = value if isinstance(value, (list, tuple)) else [value]
        text = ", ".join(str(v).strip() for v in values if v and str(v).strip())
        if text:
            rows.append((label, text))
    return rows


def _metadata_episode_count(media_metadata):
    """Return a numeric episode count from metadata, or None when unavailable.

    Providers disagree on the shape of their episode payloads: TVDB and MAL
    expose ``details["episodes"]`` as a count, while the anime detail preview
    stores a list of episode rows under the top-level ``episodes`` key. Falling
    back to the raw value rendered the list itself into the Collection panel
    (see the "0/[{'media_id': ...}]" report), so collapse any sequence to its
    length and reject anything that is not a usable count.
    """
    candidates = (
        (media_metadata.get("details") or {}).get("episodes"),
        media_metadata.get("episodes"),
    )
    for candidate in candidates:
        if isinstance(candidate, bool) or candidate is None:
            continue
        if isinstance(candidate, int):
            if candidate > 0:
                return candidate
            continue
        if isinstance(candidate, str):
            try:
                parsed = int(candidate.strip())
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
            continue
        if isinstance(candidate, (list, tuple, set, frozenset, dict)) and len(
            candidate,
        ):
            return len(candidate)
    return None


def _get_tv_runtime_display_fallback(detail_item, media_metadata):
    """Return a best-effort runtime string for TV details when provider runtime is missing."""
    if not detail_item or detail_item.media_type != MediaTypes.TV.value:
        return None

    runtime_minutes = getattr(detail_item, "runtime_minutes", None)
    if runtime_minutes and runtime_minutes < RUNTIME_UNKNOWN_AIRED:
        return tmdb.get_readable_duration(runtime_minutes)

    if detail_item.runtime:
        parsed_runtime = stats.parse_runtime_to_minutes(detail_item.runtime)
        if parsed_runtime and parsed_runtime > 0:
            return tmdb.get_readable_duration(parsed_runtime)

    episode_runtimes = list(
        Item.objects.filter(
            media_id=detail_item.media_id,
            source=detail_item.source,
            media_type=MediaTypes.EPISODE.value,
            runtime_minutes__isnull=False,
        )
        .exclude(
            runtime_minutes__in=[999998, 999999],
        )
        .values_list("runtime_minutes", flat=True),
    )
    if episode_runtimes:
        return tmdb.get_readable_duration(
            round(sum(episode_runtimes) / len(episode_runtimes))
        )

    details = media_metadata.get("details") if isinstance(media_metadata, dict) else {}
    if not isinstance(details, dict):
        details = {}

    max_seasons = details.get("seasons")
    try:
        max_seasons = int(max_seasons)
    except (TypeError, ValueError):
        max_seasons = 5
    max_seasons = max(1, min(max_seasons, 20))

    # Read every candidate season in one grouped request. One request for each
    # season made a detail page wait for up to 20 cache round trips, one after
    # another (#725).
    runtime_minutes = build_season_runtime_index(
        [detail_item.media_id],
        season_numbers=range(1, max_seasons + 1),
    ).get(detail_item.media_id)
    if runtime_minutes:
        return tmdb.get_readable_duration(runtime_minutes)

    return None


@login_not_required
@require_GET
@xframe_options_sameorigin
def youtube_player(request):
    """Embed YouTube in a same-origin document with an origin-only referrer."""
    from app.youtube_channel_art import VIDEO_ID

    video_id = request.GET.get("v", "")
    if not VIDEO_ID.fullmatch(video_id):
        return HttpResponse(status=400)
    response = render(
        request,
        "app/components/youtube_player.html",
        {
            "video_id": video_id,
            "autoplay": request.GET.get("autoplay") == "1",
        },
    )
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def _detail_skeleton_heights(
    media_metadata,
    media_type,
    *,
    studios_count=0,
    has_collection_data=False,
    has_details=False,
    genres_count=0,
):
    """Estimate the deferred secondary fragment's height for its skeleton.

    The shell cannot render the fragment (it is deliberately deferred), so the
    placeholder reserves the section's settled height from the same metadata
    the fragment will use. The constants follow the detail grid's card sizes;
    they only need to keep content below from jumping while the fragment loads.
    """
    related = media_metadata.get("related") or {}
    seasons = len(related.get("seasons") or [])
    recommendations = len(related.get("recommendations") or [])
    related_items = sum(
        len(items)
        for name, items in related.items()
        if name not in ("seasons", "recommendations") and isinstance(items, list)
    )
    cast = min(len(media_metadata.get("cast") or []), 20)
    crew = min(len(media_metadata.get("crew") or []), 20)
    detail_rows = len(media_metadata.get("details") or {}) if has_details else 0

    def sidebar(mobile):
        total = 0
        if detail_rows:
            total += 72 + detail_rows * 64 if mobile else 44 + 32 + detail_rows * 20
        else:
            total += 100
        if genres_count:
            rows = max(1, math.ceil(genres_count / (4 if mobile else 2)))
            total += 68 + 56 + (rows - 1) * 32 if mobile else 68 + 32 + rows * 32
        if studios_count:
            total += 68 + 56 + studios_count * 16 if mobile else 68 + 32 + studios_count * 20
        if mobile and has_collection_data and media_type in ("tv", "season"):
            total += 200
        else:
            total += 124
        return total

    def section(rows, card_height, columns, gap):
        if not rows:
            return 0
        grid_rows = math.ceil(rows / columns)
        return 44 + grid_rows * card_height + (grid_rows - 1) * gap

    def main(mobile):
        card_height = 162 if mobile else 233
        columns = 3 if mobile else 5
        gap = 12 if mobile else 16
        heights = [
            section(related_items, card_height, columns, gap),
            section(seasons, card_height, columns, gap),
            240 if cast else 0,
            240 if crew else 0,
            207 if mobile and recommendations else 264 if recommendations else 0,
        ]
        heights = [height for height in heights if height]
        return sum(heights) + 32 * (len(heights) - 1) + (32 if heights else 0)

    has_section_data = any((related_items, seasons, cast, crew, recommendations))
    if not has_section_data:
        # The shell's metadata does not include the provider's related/cast/crew
        # payloads (they are fetched for the deferred fragment), so reserve the
        # settled height of the sections those payloads normally produce.
        defaults = {
            "movie": (1246, 2251),
            "tv": (1495, 2288),
            "anime": (1495, 2288),
            "season": (1000, 1800),
            "game": (1200, 2200),
            "boardgame": (1200, 2200),
            "book": (484, 460),
            "comic": (484, 460),
            "manga": (484, 460),
            "music": (224, 272),
        }
        desktop, mobile = defaults.get(media_type, (620, 620))
        return {"desktop": desktop, "mobile": mobile}

    promoted = 97 if media_type in ("movie", "tv", "anime") else 0
    desktop = max(sidebar(False), main(False)) + promoted
    mobile = sidebar(True) + 24 + main(True) + (promoted if media_type in ("movie", "tv", "anime") else 0)
    return {"desktop": desktop, "mobile": mobile}


@login_not_required
@require_GET
def media_details(
    request,
    source,
    media_type,
    media_id,
    title,
):
    """Return the details page for a media item."""
    if request.GET.get("fragment") == "video-metadata":
        if source != Sources.YOUTUBE.value or media_type != MediaTypes.VIDEO.value:
            return HttpResponse(status=404)
        item = Item.objects.filter(
            source=source,
            media_type=media_type,
            media_id=media_id,
        ).first()
        if not item:
            return HttpResponse(status=404)
        from app.youtube_channel_art import video_details

        extra = video_details(media_id)
        # A client may have supplied a description while the request was in flight.
        item.refresh_from_db(fields=["synopsis"])
        return HttpResponse(
            render_to_string(
                "app/components/video_details_extra.html",
                {
                    "avatar": extra["avatar"],
                    "description": extra["description"] if not item.synopsis else "",
                    "description_media": {"synopsis": extra["description"]},
                },
                request=request,
            )
        )

    if request.GET.get("fragment") == DETAIL_CAROUSEL_FRAGMENT:
        # .strip() matters: an empty carousel must render as a truly empty
        # string, not whitespace, so input.css's #detail-carousel-wrap:not(:empty)
        # rule (a whitespace-only text node still counts as a child for :empty)
        # correctly falls back to the non-carousel layout.
        html = render_to_string(
            "app/components/detail_carousel_fragment.html",
            {
                "carousel": carousel_media.resolve_carousel_media(
                    media_type, source, media_id
                )
            },
            request=request,
        ).strip()
        return HttpResponse(html)

    detail_view_started_at = time.perf_counter()
    carousel_supported = carousel_media.carousel_supported(
        media_type,
        source,
    ) and not carousel_media.confirmed_empty(media_type, source, media_id)
    render_secondary_only = (
        request.GET.get("fragment") == DETAIL_SECONDARY_FRAGMENT
        and media_type != MediaTypes.PODCAST.value
    )
    initial_carousel = (
        carousel_media.resolve_carousel_media(
            media_type, source, media_id, cached_only=True
        )
        if carousel_supported and not render_secondary_only
        else None
    )
    defer_detail_secondary = (
        not render_secondary_only and media_type != MediaTypes.PODCAST.value
    )
    # Only the shell renders the score chips that draw the series graphs; the
    # secondary fragment would otherwise scan the episode rows for nothing.
    graphs_rendered = not render_secondary_only and media_type in (
        MediaTypes.TV.value,
        MediaTypes.ANIME.value,
    )
    detail_return_url = _detail_request_url(request)
    detail_carousel_fragment_url = (
        _detail_request_url(request, fragment=DETAIL_CAROUSEL_FRAGMENT)
        if carousel_supported
        else None
    )
    detail_secondary_fragment_url = _detail_request_url(
        request,
        fragment=DETAIL_SECONDARY_FRAGMENT,
    )

    # Anonymous views and explicit public-list links are public (no user-specific
    # data/actions), while only a validated list reference can expose owner notes.
    is_anonymous = not request.user.is_authenticated
    public_list_view = request.GET.get("public_view") == "1"
    public_view = is_anonymous or public_list_view

    # For public views, find a public list containing this item to get the owner
    list_owner = None
    public_notes_view = False
    if public_list_view:
        try:
            item = Item.objects.filter(
                media_id=media_id,
                source=source,
                media_type=media_type,
            ).first()
            if item:
                public_list = get_public_list_for_item(
                    request.GET.get("public_list"),
                    item,
                )
                if public_list:
                    list_owner = public_list.owner
                    public_notes_view = public_list.include_notes
        except Exception:  # noqa: S110  # deliberate best-effort; failure is non-fatal here
            # If we can't find a list owner, list_owner stays None
            pass

    detail_persistence_deferred = False
    detail_db_max_retries = 0

    def _mark_detail_persistence_deferred(_error=None):
        nonlocal detail_persistence_deferred
        detail_persistence_deferred = True

    def _best_effort_detail_db_work(operation, *, fallback=None, operation_name):
        return run_retryable_db_operation(
            operation,
            mode="best_effort",
            fallback=fallback,
            operation_name=operation_name,
            operation_logger=logger,
            max_retries=detail_db_max_retries,
            on_deferred=_mark_detail_persistence_deferred,
        )

    def _best_effort_detail_followup(
        operation,
        *,
        operation_name,
        fallback=False,
    ):
        try:
            return operation()
        except Exception:
            logger.warning(
                "Skipping detail follow-up %s for %s due to error",
                operation_name,
                request.path,
                exc_info=True,
            )
            _mark_detail_persistence_deferred()
            return fallback

    # A list can hold an album, saved under the album page's id. That id is not
    # a recording, so the album page is the only place that can describe it.
    if media_type == MediaTypes.MUSIC.value and source == Sources.MUSICBRAINZ.value:
        from app.services.music import album_for_media_id
        from app.templatetags.app_tags import music_album_url

        album = album_for_media_id(media_id, user=request.user)
        if album:
            return redirect(music_album_url(album))

    # For podcast shows (identified by podcast_uuid), show show detail page
    if media_type == MediaTypes.PODCAST.value and source in {
        Sources.POCKETCASTS.value,
        Sources.GPODDER.value,
        Sources.AUDIOBOOKSHELF.value,
        Sources.MANUAL.value,
    }:
        from app.models import PodcastShow, PodcastShowTracker
        from app.providers.services import _podcast_external_links

        # Check if this is a show (podcast_uuid) or an episode (episode_uuid)
        show = PodcastShow.objects.filter(podcast_uuid=media_id).first()

        # If show not found, resolve a provider identifier (an iTunes id or a
        # GPodder feed URL) into the local show catalog.
        if not show:
            from django.contrib import messages

            from app.services.podcast_import import (
                PodcastImportError,
                resolve_show_for_media_id,
            )
            from app.templatetags import app_tags

            try:
                show = resolve_show_for_media_id(media_id, source=source)
                if show is not None:
                    return redirect(
                        "media_details",
                        source=source,
                        media_type=MediaTypes.PODCAST.value,
                        media_id=show.podcast_uuid,
                        title=app_tags.slug(show.title or "") or "podcast",
                    )
            except PodcastImportError as e:
                messages.error(request, str(e))
                # Fall through to empty metadata
            except Exception as e:
                logger.exception(
                    "Failed to enrich podcast from provider metadata: %s",
                    exception_summary(e),  # noqa: TRY401  # exception_summary() is the project's sanitised rendering
                )
                messages.error(request, f"Failed to load podcast details: {e}")
                # Fall through to empty metadata

        if show:
            # This is a show, not an episode - show show detail page
            tracker = (
                PodcastShowTracker.objects.filter(user=request.user, show=show).first()
                if not public_view
                else None
            )

            # Reconcile the catalog against the feed: pick up episodes
            # published since the last visit, and backfill website_url on the
            # ones already stored, which is the only path that repairs rows
            # created before podcast website links existed (issue #1014).
            # The feed is a full third-party download plus episode writes, so
            # repeat views inside the window render the stored episodes.
            if (
                show.rss_feed_url
                and not public_view
                and cache.add(
                    f"podcast:detail-rss-refresh:{show.id}",
                    True,
                    PODCAST_DETAIL_RSS_REFRESH_SECONDS,
                )
            ):
                from app.tasks_podcast import refresh_podcast_show_from_rss_task

                # Queued, so a slow feed host never holds the render; new
                # episodes show on the next view.
                _best_effort_detail_followup(
                    lambda: refresh_podcast_show_from_rss_task.delay(show.id),
                    operation_name="podcast_show_rss_refresh",
                )

            # Newest first, undated last: the order every episode page uses.
            episodes = podcast_views.ordered_episodes(show)

            # The viewer's totals are summed in SQL. A show can hold thousands
            # of listens, and the page only needs their counts and dates.
            if not public_view:
                listen_totals = podcast_views.podcast_show_totals(request.user, show)
            else:
                listen_totals = {"listened": 0, "minutes": 0}
            total_listened = listen_totals["listened"]
            total_minutes = listen_totals["minutes"]

            # Build episode items - create Item objects for enrichment
            # Initially load first 20 episodes, rest will be loaded via infinite scroll
            initial_limit = 20
            page_episodes = list(episodes[:initial_limit])
            episode_items_map = podcast_views.episode_items_by_uuid(
                show,
                page_episodes,
                source=source,
                media_type=media_type,
            )
            # enrich_items_with_user_data expects dicts with media_id, source, media_type
            episode_items_data = [
                {
                    "media_id": episode.episode_uuid,
                    "source": source,
                    "media_type": media_type,
                }
                for episode in page_episodes
            ]

            # Enrich episodes with user data
            enriched_episodes_raw = helpers.enrich_items_with_user_data(
                request,
                episode_items_data,
                user=None if public_view else request.user,
            )

            # Replace dict items with Item model instances
            enriched_episodes = []
            for enriched in enriched_episodes_raw:
                # Get the Item object from our map
                item_obj = episode_items_map.get(enriched["item"]["media_id"])
                if item_obj:
                    enriched_episodes.append(
                        {
                            "item": item_obj,
                            "media": enriched["media"],
                        }
                    )
                else:
                    # Fallback: fetch Item from database
                    enriched_episodes.append(
                        {
                            "item": Item.objects.get(
                                media_id=enriched["item"]["media_id"],
                                source=enriched["item"]["source"],
                                media_type=enriched["item"]["media_type"],
                            ),
                            "media": enriched["media"],
                        }
                    )

            # Build episode data in TV season format (inline episodes, not related items)
            episode_list = []
            # One read each for the page's play rows and their completed-play
            # history, instead of a few queries per episode.
            podcasts_by_episode = (
                {}
                if public_view
                else podcast_views.user_podcasts_by_episode(
                    request.user,
                    show,
                    page_episodes,
                )
            )
            history_by_podcast_id = podcast_views.completed_plays_by_podcast_id(
                {
                    podcast.id
                    for entries in podcasts_by_episode.values()
                    for podcast in entries
                }
                | {
                    enriched["media"].id
                    for enriched in enriched_episodes
                    if enriched["media"]
                },
            )
            for episode_obj, enriched in zip(
                page_episodes, enriched_episodes, strict=False
            ):
                duration_str = helpers.seconds_to_hm(episode_obj.duration)

                # Get user's podcast media for this episode
                episode_media = enriched["media"]
                episode_history = []
                if episode_media:
                    # Only completed plays (history records with end_date)
                    episode_history = history_by_podcast_id.get(
                        episode_media.id,
                        [],
                    )[:10]

                # Get all Podcast entries for this episode to aggregate history
                all_podcasts = podcasts_by_episode.get(episode_obj.id, [])

                # Create a wrapper object that aggregates history from all podcast entries
                if all_podcasts:
                    # Aggregate all history records from all podcast entries
                    # Only include history records with end_date (completed plays)
                    all_history = []
                    for podcast in all_podcasts:
                        # Only include history records with end_date (completed plays)
                        all_history.extend(history_by_podcast_id.get(podcast.id, []))

                    # Sort by end_date descending (most recent first) for display
                    all_history.sort(
                        key=lambda x: (
                            x.end_date
                            or timezone.datetime.min.replace(tzinfo=timezone.utc)
                        ),
                        reverse=True,
                    )

                    class PodcastHistoryWrapper:
                        """Wrapper to aggregate history from multiple Podcast entries."""

                        def __init__(self, podcasts, item, history_list):
                            self.item = item
                            self.id = podcasts[0].id if podcasts else 0
                            self._podcasts = podcasts
                            self._history_list = history_list
                            in_progress_entry = next(
                                (entry for entry in podcasts if not entry.end_date),
                                None,
                            )
                            self.in_progress_instance_id = (
                                in_progress_entry.id if in_progress_entry else None
                            )

                        @property
                        def completed_play_count(self):
                            """Return count of completed plays (history records with end_date)."""
                            # Since we already filtered all_history to only include records with end_date,
                            # we can just count the length of the filtered history_list
                            return len(self._history_list)

                        @property
                        def has_in_progress_entry(self):
                            return bool(self.in_progress_instance_id)

                        @property
                        def history(self):
                            """Return a queryset-like object that aggregates all history."""

                            class HistoryProxy:
                                def __init__(self, history_list):
                                    self._history = history_list

                                def all(self):
                                    return self._history

                                def count(self):
                                    return len(self._history)

                                def filter(self, **kwargs):
                                    # Simple filtering for history_user
                                    if "history_user" in kwargs:
                                        user = kwargs["history_user"]
                                        filtered = [
                                            h
                                            for h in self._history
                                            if getattr(h, "history_user", None) == user
                                            or getattr(h, "history_user", None) is None
                                        ]
                                        return HistoryProxy(filtered)
                                    return self

                                def order_by(self, order):
                                    # Re-sort based on order string (e.g., 'end_date' or '-end_date')
                                    if order == "end_date":
                                        sorted_list = sorted(
                                            self._history,
                                            key=lambda x: (
                                                x.end_date
                                                or timezone.datetime.min.replace(
                                                    tzinfo=timezone.utc
                                                )
                                            ),
                                        )
                                    elif order == "-end_date":
                                        sorted_list = sorted(
                                            self._history,
                                            key=lambda x: (
                                                x.end_date
                                                or timezone.datetime.min.replace(
                                                    tzinfo=timezone.utc
                                                )
                                            ),
                                            reverse=True,
                                        )
                                    else:
                                        sorted_list = self._history
                                    return HistoryProxy(sorted_list)

                            return HistoryProxy(self._history_list)

                    podcast_wrapper = PodcastHistoryWrapper(
                        all_podcasts, enriched["item"], all_history
                    )
                else:
                    podcast_wrapper = _DummyPodcastWrapper(enriched["item"])

                # Create episode dict compatible with TV episode format
                # Include media_id, source, media_type for tracking modals
                episode_item = enriched["item"]
                episode_list.append(
                    {
                        "title": episode_obj.title,
                        "description": episode_obj.description or "",
                        "episode_number": episode_obj.episode_number or 0,
                        "image": episode_obj.image
                        or show.image
                        or settings.IMG_NONE,
                        "air_date": episode_obj.published,
                        "runtime": duration_str,
                        "overview": episode_obj.description or "",
                        "website_url": episode_obj.website_url,
                        "history": episode_history,
                        "media": episode_media,
                        "item": episode_item,
                        # Add fields needed for episode tracking modals
                        "media_id": episode_item.media_id,
                        "source": episode_item.source,
                        "media_type": episode_item.media_type,
                        # Add adapter objects for music-style modal
                        "track_adapter": podcast_views.PodcastEpisodeAdapter(
                            episode_obj,
                        ),
                        "album_adapter": podcast_views.PodcastShowAdapter(show),
                        "music_wrapper": podcast_wrapper,
                    }
                )

            # Build metadata dict for show
            media_metadata = {
                "title": show.title,
                "image": show.image or settings.IMG_NONE,
                "synopsis": show.description or "",  # Use description as synopsis
                "source": source,
                "media_type": media_type,
                "media_id": media_id,
                "genres": show.genres or [],
                "details": {
                    "author": show.author,
                    "language": show.language,
                    "website_url": show.website_url,
                },
                "external_links": _podcast_external_links(show),
                "episodes": episode_list,  # Use episodes key like TV seasons
            }
            media_metadata.setdefault("source_url", None)
            media_metadata.setdefault("tracking_source_url", None)
            media_metadata.setdefault("display_source_url", None)

            # For pagination, calculate if there are more episodes
            total_episodes_count = episodes.count()
            has_more = total_episodes_count > initial_limit
            next_page = 2 if has_more else None
            media_metadata["max_progress"] = total_episodes_count

            podcast_play_stats = None
            activity_subtitle = None
            if not public_view and total_listened:
                podcast_play_stats = {
                    "first_played": listen_totals["first_played"],
                    "last_played": listen_totals["last_played"],
                    "total_minutes": total_minutes,
                    "total_hours": total_minutes // 60,
                    "total_minutes_remainder": total_minutes % 60,
                    "total_plays": listen_totals["completed"] or total_listened,
                }
                activity_subtitle = _build_detail_activity_subtitle(
                    MediaTypes.PODCAST.value,
                    media_metadata,
                    tracker,
                    podcast_play_stats,
                )

            context = {
                "user": request.user,
                "media": media_metadata,
                "media_type": media_type,
                "current_instance": tracker,  # Use tracker as current_instance for compatibility
                # Podcast templates only ask whether the viewer has listened.
                "user_medias": total_listened > 0,
                "podcast_show": show,
                "podcast_tracker": tracker,
                "episode_manager_props": (
                    None
                    if public_view
                    else podcast_views.episode_manager_props(request, show)
                ),
                "episodes": episode_list,  # Use episode_list with adapter objects
                "paginated_episodes": episode_list,  # For fragment compatibility
                "total_episodes": total_episodes_count,
                "total_listened": total_listened,
                "total_minutes": total_minutes,
                "public_view": public_view,
                "play_stats": podcast_play_stats,
                "activity_subtitle": activity_subtitle,
                "IMG_NONE": settings.IMG_NONE,
                "TRACK_TIME": True,
                "has_more_episodes": has_more,  # Keep for backward compatibility
                "has_more": has_more,  # For fragment compatibility
                "next_page": next_page,
                "show_id": show.id,  # For API endpoint
                # The show's website belongs in the same links popover every
                # other detail page uses, rather than a podcast-only widget.
                "detail_link_sections": _build_detail_link_sections(
                    media_metadata,
                    media_type,
                    source,
                    source,
                ),
            }
            return render(request, "app/media_details.html", context)

    tracking_media_type = metadata_resolution.get_tracking_media_type(
        media_type,
        source=source,
    )
    detail_item_lookup = {
        "media_id": media_id,
        "source": source,
        "media_type": tracking_media_type,
    }
    if metadata_resolution.is_grouped_anime_route(media_type, source=source):
        detail_item_lookup["library_media_type"] = MediaTypes.ANIME.value

    detail_item = Item.objects.filter(**detail_item_lookup).first()

    hardcover_edition_id = None
    if source == Sources.HARDCOVER.value and media_type == MediaTypes.BOOK.value:
        # #539: let the requester preview a specific edition (query param), or
        # fall back to their saved edition choice for an already-tracked book.
        hardcover_edition_id = request.GET.get("edition_id") or None
        if not hardcover_edition_id and request.user.is_authenticated and detail_item:
            preference = HardcoverEditionPreference.objects.filter(
                user=request.user,
                item=detail_item,
            ).first()
            if preference:
                hardcover_edition_id = preference.edition_id

    metadata_kwargs = {}
    if hardcover_edition_id:
        metadata_kwargs["edition_id"] = hardcover_edition_id

    # A "Sync metadata with provider" action just did a live fetch: skip the
    # stored-metadata shortcut on the page load(s) that follow so the full
    # payload (score, cast, recommendations, etc.) isn't replaced by the
    # Item-only fallback (#931).
    force_live_metadata = detail_item is not None and cache.get(
        force_live_metadata_cache_key(detail_item.id),
    )
    can_skip_live_fetch = (
        not force_live_metadata
        and not render_secondary_only
        and detail_item is not None
        and detail_item.metadata_fetched_at is not None
        # Stored text has no locale provenance. An explicit TMDB language must
        # use its language-keyed provider cache while the upgrade repair runs.
        and not (
            source == Sources.TMDB.value
            and (
                getattr(request.user, "metadata_language", "")
                or metadata_resolution.metadata_language_default(
                    request.user, detail_item
                )
                != settings.TMDB_LANG
                or BackfillReconcileState.objects.filter(
                    key=f"metadata_language:{request.user.pk}:{settings.TMDB_LANG}",
                    completed_at__isnull=True,
                ).exists()
            )
        )
    )

    provider_metadata_unavailable = False
    if can_skip_live_fetch:
        media_metadata = stored_metadata_fallback(detail_item)
    else:
        try:
            with services.interactive_request_scope():
                media_metadata = services.get_media_metadata(
                    media_type,
                    media_id,
                    source,
                    language=metadata_resolution.metadata_language_default(
                        request.user, detail_item
                    ),
                    user=request.user,
                    **metadata_kwargs,
                )
        except services.ProviderAPIError:
            if detail_item is None:
                raise
            provider_metadata_unavailable = True
            logger.warning(
                "Falling back to stored metadata for media_id=%s due to provider API error",
                media_id,
            )
            media_metadata = stored_metadata_fallback(detail_item)

    if isinstance(media_metadata, dict):
        media_metadata.update(Item.title_fields_from_metadata(media_metadata))

    if media_type == MediaTypes.COMIC.value and isinstance(media_metadata, dict):
        raw_issues = media_metadata.pop("issues", None)
        if raw_issues:
            media_metadata["episodes"] = _enrich_comic_issues(
                raw_issues,
                request.user,
                media_metadata["source"],
            )

    if (
        render_secondary_only
        and detail_item is None
        and source == Sources.IGDB.value
        and media_type == MediaTypes.GAME.value
        and isinstance(media_metadata, dict)
    ):
        detail_item_outcome = _best_effort_detail_db_work(
            lambda: Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults={
                    **Item.title_fields_from_metadata(media_metadata),
                    "image": media_metadata.get("image") or settings.IMG_NONE,
                },
            ),
            fallback=lambda: (None, False),
            operation_name="IGDB detail item create",
        )
        detail_item, _ = detail_item_outcome.value

    # When the user prefers original titles, aggressively refresh stale TMDB cache
    # if we don't yet have an original title. This lets details-page opens backfill
    # better title variants that can then propagate across the UI.
    tmdb_detail_cache_key = f"{Sources.TMDB.value}_{tracking_media_type}_{media_id}"
    should_refresh_tmdb_titles = (
        request.user.is_authenticated
        and source == Sources.TMDB.value
        and not provider_metadata_unavailable
        and tracking_media_type
        in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
        )
        and getattr(request.user, "title_display_preference", "localized") == "original"
        and isinstance(media_metadata, dict)
        and not media_metadata.get("original_title")
    )
    if (
        render_secondary_only
        and should_refresh_tmdb_titles
        and _detail_refetch_allowed("titles", source, tracking_media_type, media_id)
    ):
        cache.delete(tmdb_detail_cache_key)
        media_metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            language=metadata_resolution.metadata_language_default(
                request.user, detail_item
            ),
            user=request.user,
        )
        if isinstance(media_metadata, dict):
            media_metadata.update(Item.title_fields_from_metadata(media_metadata))

    should_refresh_tmdb_tv_credits = (
        source == Sources.TMDB.value
        and not provider_metadata_unavailable
        and tracking_media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value)
        and isinstance(media_metadata, dict)
        and not media_metadata.get("cast")
        and not media_metadata.get("crew")
    )
    if (
        render_secondary_only
        and should_refresh_tmdb_tv_credits
        and _detail_refetch_allowed("credits", source, tracking_media_type, media_id)
    ):
        cache.delete(tmdb_detail_cache_key)
        media_metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            language=metadata_resolution.metadata_language_default(
                request.user, detail_item
            ),
            user=request.user,
        )
        if isinstance(media_metadata, dict):
            media_metadata.update(Item.title_fields_from_metadata(media_metadata))

    identity_media_metadata = media_metadata

    if render_secondary_only and detail_item and isinstance(media_metadata, dict):
        title_fields = Item.title_fields_from_metadata(
            media_metadata,
            fallback_title=detail_item.title,
        )
        update_fields = []
        normalized_existing_titles = {
            "title": Item._normalize_title_value(detail_item.title),
            "original_title": Item._normalize_title_value(detail_item.original_title),
            "localized_title": Item._normalize_title_value(detail_item.localized_title),
        }
        for field_name, normalized_value in normalized_existing_titles.items():
            if (
                normalized_value
                and getattr(detail_item, field_name) != normalized_value
            ):
                setattr(detail_item, field_name, normalized_value)
                update_fields.append(field_name)

        for field_name in ("title", "original_title", "localized_title"):
            desired_value = title_fields.get(field_name)
            if desired_value and getattr(detail_item, field_name) != desired_value:
                setattr(detail_item, field_name, desired_value)
                update_fields.append(field_name)
        if (
            media_metadata.get("synopsis")
            and detail_item.synopsis != media_metadata["synopsis"]
        ):
            detail_item.synopsis = media_metadata["synopsis"]
            update_fields.append("synopsis")
        if update_fields:
            _best_effort_detail_db_work(
                lambda: detail_item.save(update_fields=update_fields),
                operation_name="detail item title sync",
            )

    # Persist series info for books if available
    if (
        render_secondary_only
        and media_type == MediaTypes.BOOK.value
        and isinstance(media_metadata, dict)
    ):
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
            update_fields = []
            if (
                media_metadata.get("series_name")
                and item.series_name != media_metadata["series_name"]
            ):
                item.series_name = media_metadata["series_name"]
                update_fields.append("series_name")
            if (
                media_metadata.get("series_position") is not None
                and item.series_position != media_metadata["series_position"]
            ):
                item.series_position = media_metadata["series_position"]
                update_fields.append("series_position")

            if update_fields:
                _best_effort_detail_db_work(
                    lambda: item.save(update_fields=update_fields),
                    operation_name="detail book-series sync",
                )
        except Item.DoesNotExist:
            pass

    igdb_game_studios_missing = (
        source == Sources.IGDB.value
        and tracking_media_type == MediaTypes.GAME.value
        and isinstance(media_metadata, dict)
        and "studios_full" not in media_metadata
    )

    if isinstance(media_metadata, dict):
        media_metadata.setdefault("cast", [])
        media_metadata.setdefault("crew", [])
        media_metadata.setdefault("studios_full", [])

    metadata_resolution_result = None
    should_resolve_metadata = bool(
        detail_item
        and isinstance(media_metadata, dict)
        and (
            media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value)
            or custom_metadata.supports_custom_provider(media_type)
        )
    )
    if render_secondary_only and should_resolve_metadata:
        metadata_resolution_result = metadata_resolution.resolve_detail_metadata(
            request.user if request.user.is_authenticated else None,
            item=detail_item,
            route_media_type=media_type,
            media_id=media_id,
            source=source,
            base_metadata=media_metadata,
            persistence_mode="best_effort",
            retry_max_retries=detail_db_max_retries,
            on_persistence_deferred=_mark_detail_persistence_deferred,
            persist_links=False,
        )
        media_metadata = metadata_resolution_result.header_metadata
        media_metadata.update(
            Item.title_fields_from_metadata(
                media_metadata,
                fallback_title=detail_item.title if detail_item else "",
            ),
        )

    # For podcasts, videos and manual music entries, ensure source is in metadata dict
    # (fixes KeyError in template — see services.get_media_metadata's music/manual stub)
    if media_type in (
        MediaTypes.PODCAST.value,
        MediaTypes.MUSIC.value,
        MediaTypes.VIDEO.value,
    ) and isinstance(media_metadata, dict):
        media_metadata["source"] = source
        media_metadata["media_type"] = media_type
        media_metadata["media_id"] = media_id

    if render_secondary_only and detail_item and isinstance(media_metadata, dict):
        metadata_update_fields = metadata_utils.apply_item_metadata(
            detail_item,
            identity_media_metadata,
        )
        if metadata_update_fields:
            detail_item.metadata_fetched_at = timezone.now()
            metadata_update_fields.append("metadata_fetched_at")
            _best_effort_detail_db_work(
                lambda: detail_item.save(update_fields=metadata_update_fields),
                operation_name="detail metadata sync",
            )

    if (
        render_secondary_only
        and source in (Sources.TMDB.value, Sources.TVDB.value)
        and tracking_media_type
        in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
        )
        and isinstance(media_metadata, dict)
        and detail_item
    ):
        missing_people = not detail_item.person_credits.exists()
        # TVDB never returns studio data, so it would otherwise always look
        # "missing" and re-trigger this sync on every page view.
        missing_studios = (
            source == Sources.TMDB.value and not detail_item.studio_credits.exists()
        )
        if missing_people or missing_studios:
            _best_effort_detail_db_work(
                lambda: credits.sync_item_credits_from_metadata(
                    detail_item,
                    media_metadata,
                ),
                operation_name="detail credits sync",
            )

    should_refresh_igdb_game_studios = (
        source == Sources.IGDB.value
        and tracking_media_type == MediaTypes.GAME.value
        and detail_item is not None
        and igdb_game_studios_missing
    )
    if (
        render_secondary_only
        and should_refresh_igdb_game_studios
        and _detail_refetch_allowed("studios", source, tracking_media_type, media_id)
    ):
        cache.delete(f"{source}_{tracking_media_type}_{media_id}")
        media_metadata = services.get_media_metadata(media_type, media_id, source)
        if isinstance(media_metadata, dict):
            media_metadata.update(Item.title_fields_from_metadata(media_metadata))

    if (
        render_secondary_only
        and detail_item
        and source == Sources.IGDB.value
        and tracking_media_type == MediaTypes.GAME.value
        and isinstance(media_metadata, dict)
        and media_metadata.get("studios_full")
    ):
        existing_studio_ids = {
            str(studio_credit.studio.source_studio_id)
            for studio_credit in detail_item.studio_credits.select_related("studio")
            if studio_credit.studio
            and studio_credit.studio.source_studio_id is not None
        }
        incoming_studio_ids = {
            str(studio.get("studio_id") or studio.get("id"))
            for studio in media_metadata.get("studios_full", [])
            if isinstance(studio, dict)
            and (studio.get("studio_id") or studio.get("id"))
        }
        if existing_studio_ids != incoming_studio_ids:
            _best_effort_detail_db_work(
                lambda: credits.sync_item_credits_from_metadata(
                    detail_item,
                    {
                        "studios_full": media_metadata.get("studios_full", []),
                    },
                ),
                operation_name="IGDB detail studio sync",
            )

    identity_media_metadata = media_metadata

    if (
        render_secondary_only
        and source == Sources.IGDB.value
        and tracking_media_type == MediaTypes.GAME.value
        and detail_item
        and isinstance(media_metadata, dict)
    ):
        metadata_update_fields = metadata_utils.apply_item_metadata(
            detail_item,
            identity_media_metadata,
        )
        if metadata_update_fields:
            detail_item.metadata_fetched_at = timezone.now()
            metadata_update_fields.append("metadata_fetched_at")
            _best_effort_detail_db_work(
                lambda: detail_item.save(update_fields=metadata_update_fields),
                operation_name="IGDB detail metadata sync",
            )

    _apply_cached_hltb_link(media_metadata, detail_item)

    game_lengths = (
        _build_game_lengths_context(detail_item)
        if source == Sources.IGDB.value and media_type == MediaTypes.GAME.value
        else None
    )
    if (
        game_lengths
        and game_lengths.get("source") == "igdb"
        and isinstance(media_metadata, dict)
        and media_metadata.get("source_url")
    ):
        game_lengths["source_url"] = media_metadata["source_url"]
    game_lengths_fetch_queued = False
    game_lengths_refresh_pending = False
    if render_secondary_only and _should_queue_game_lengths_refresh(detail_item):
        game_lengths_refresh_pending = (
            _get_game_lengths_refresh_lock(
                detail_item,
                force=False,
                fetch_hltb=True,
            )
            is not None
        )
        if not game_lengths_refresh_pending:
            game_lengths_fetch_queued = _best_effort_detail_followup(
                lambda: _queue_game_lengths_refresh(
                    detail_item,
                    force=False,
                    fetch_hltb=True,
                ),
                operation_name="game lengths refresh enqueue",
                fallback=False,
            )
            game_lengths_refresh_pending = game_lengths_fetch_queued or (
                _get_game_lengths_refresh_lock(
                    detail_item,
                    force=False,
                    fetch_hltb=True,
                )
                is not None
            )
    trakt_score = _build_trakt_popularity_context(detail_item, media_type)
    imdb_score = _build_imdb_rating_context(detail_item, media_type)
    mal_score = _build_mal_rating_context(detail_item, media_type)
    opencritic_score = _build_opencritic_context(detail_item, media_type)
    if media_type == MediaTypes.GAME.value:
        _best_effort_detail_followup(
            lambda: opencritic_scores.queue_refresh(detail_item, request.user),
            operation_name="OpenCritic refresh enqueue",
            fallback=False,
        )

    author_detail_keys = ("author", "authors", "people")
    authors_linked = []
    if (
        render_secondary_only
        and media_type
        in (
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MANGA.value,
        )
        and isinstance(media_metadata, dict)
    ):

        def _collect_authors_linked(metadata_payload):
            linked = []

            if detail_item:
                author_credits = (
                    detail_item.person_credits.filter(
                        role_type=CreditRoleType.AUTHOR.value,
                    )
                    .select_related("person")
                    .order_by("sort_order", "person__name")
                )
                for author_credit in author_credits:
                    person = author_credit.person
                    linked.append(
                        {
                            "source": person.source,
                            "person_id": person.source_person_id,
                            "name": person.name,
                        },
                    )

            authors_full_payload = metadata_payload.get("authors_full")
            if not linked and isinstance(authors_full_payload, list):
                for author in authors_full_payload:
                    person_id = author.get("person_id") or author.get("id")
                    name = (author.get("name") or "").strip()
                    if person_id is None or not name:
                        continue
                    linked.append(
                        {
                            "source": source,
                            "person_id": str(person_id),
                            "name": name,
                        },
                    )

            return linked

        authors_full = media_metadata.get("authors_full")
        if detail_item and isinstance(authors_full, list):
            _best_effort_detail_db_work(
                lambda: credits.sync_item_author_credits(detail_item, authors_full),
                operation_name="detail author-credit sync",
            )

        authors_linked = _collect_authors_linked(media_metadata)

        details_payload = media_metadata.get("details")
        if not isinstance(details_payload, dict):
            details_payload = {}

        # Old provider cache entries may include plain author names but no authors_full
        # IDs, which prevents author links from rendering.
        should_refresh_author_cache = (
            not authors_linked
            and detail_item is not None
            and any(details_payload.get(key) for key in author_detail_keys)
            and not isinstance(media_metadata.get("authors_full"), list)
        )
        if should_refresh_author_cache and _detail_refetch_allowed(
            "authors", source, media_type, media_id
        ):
            cache_key = f"{source}_{media_type}_{media_id}"
            cache.delete(cache_key)
            media_metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                user=request.user,
            )
            if isinstance(media_metadata, dict):
                media_metadata.setdefault("cast", [])
                media_metadata.setdefault("crew", [])
                media_metadata.setdefault("studios_full", [])
                refreshed_authors_full = media_metadata.get("authors_full")
                if detail_item and isinstance(refreshed_authors_full, list):
                    _best_effort_detail_db_work(
                        lambda: credits.sync_item_author_credits(
                            detail_item,
                            refreshed_authors_full,
                        ),
                        operation_name="refreshed detail author-credit sync",
                    )
                authors_linked = _collect_authors_linked(media_metadata)

    studio_detail_keys = ("studios", "companies")
    studios_linked = []

    def _collect_studios_linked(metadata_payload):
        linked = []

        if detail_item:
            studio_credits = detail_item.studio_credits.select_related(
                "studio"
            ).order_by("sort_order", "studio__name")
            for studio_credit in studio_credits:
                studio = studio_credit.studio
                linked.append(
                    {
                        "source": studio.source,
                        "studio_id": studio.source_studio_id,
                        "name": studio.name,
                        "logo": studio.logo,
                    },
                )

        studios_full_payload = metadata_payload.get("studios_full")
        if not linked and isinstance(studios_full_payload, list):
            for studio in studios_full_payload:
                studio_id = studio.get("studio_id") or studio.get("id")
                name = (studio.get("name") or "").strip()
                if studio_id is None or not name:
                    continue
                linked.append(
                    {
                        "source": source,
                        "studio_id": str(studio_id),
                        "name": name,
                        "logo": (studio.get("logo") or "").strip(),
                    },
                )

        return linked

    if render_secondary_only and isinstance(media_metadata, dict):
        studios_linked = _collect_studios_linked(media_metadata)

    # Prefer a stored poster/cover override when the tracked item has one -
    # unless this request just fetched a specific Hardcover edition (query
    # param preview or the viewer's saved preference), whose cover the live
    # fetch above already resolved and which would otherwise be immediately
    # discarded in favor of the item's default-edition cover (#1251). This is
    # display-only: the shared Item is never written here, since the edition
    # choice is per-viewer, not the item's own record (#1283 review).
    if (
        detail_item
        and isinstance(media_metadata, dict)
        and detail_item.image
        and detail_item.image != settings.IMG_NONE
        and not hardcover_edition_id
    ):
        media_metadata["image"] = detail_item.image

    # For TV shows and grouped anime, enrich season cards from season-detail metadata.
    if media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value) and isinstance(
        media_metadata,
        dict,
    ):
        details, seasons = enrich_detail_seasons(
            media_metadata,
            media_id=media_id,
            source=source,
            user=request.user,
            detail_item=detail_item,
            render_secondary_only=render_secondary_only,
        )

        if not details.get("runtime"):
            fallback_runtime = _get_tv_runtime_display_fallback(
                detail_item, media_metadata
            )
            if fallback_runtime:
                details["runtime"] = fallback_runtime

        if render_secondary_only:
            tv_poster = media_metadata.get("image")
            if tv_poster:
                for season in seasons:
                    season_image = season.get("image")
                    if not season_image or season_image == settings.IMG_NONE:
                        season["image"] = tv_poster

            # Proactively kick off Trakt episode rating fetch for any seasons
            # that already have episode Items but are missing trakt_rating.
            # This primes the cache so the Trakt series graph tooltip has data
            # ready before the user hovers.
            from app.providers import trakt as _trakt

            if (
                not public_view
                and _trakt.is_configured()
                and source
                in {
                    Sources.TMDB.value,
                    Sources.TVDB.value,
                }
            ):
                from app.models import Item as _Item
                from app.tasks_trakt import (
                    populate_trakt_episode_ratings_for_season,
                )

                pending_seasons = (
                    _Item.objects.filter(
                        media_id=str(media_id),
                        source=source,
                        media_type=MediaTypes.EPISODE.value,
                        trakt_rating__isnull=True,
                    )
                    .values_list("season_number", flat=True)
                    .distinct()
                )
                for sn in pending_seasons:
                    populate_trakt_episode_ratings_for_season.delay(
                        str(media_id), source, sn
                    )

    # For public views, we don't need user media data
    if public_view:
        user_medias = []
        current_instance = None
    else:
        user_medias = list(
            BasicMedia.objects.filter_media_prefetch(
                request.user,
                media_id,
                media_type,
                source,
            ),
        )
        if user_medias:
            user_medias.sort(key=helpers.entry_activity_key, reverse=True)
        current_instance = user_medias[0] if user_medias else None

    if current_instance is not None:
        _best_effort_detail_followup(
            lambda: helpers.refresh_item_image_if_missing(
                current_instance.item,
                media_metadata.get("image")
                if isinstance(media_metadata, dict)
                else None,
            ),
            operation_name="image refresh",
        )

    if media_type in (
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.ANIME.value,
    ):
        runtime_media = current_instance
        if runtime_media is None and detail_item is not None:
            runtime_model = apps.get_model(app_label="app", model_name=media_type)
            runtime_media = runtime_model(item=detail_item)

        if runtime_media is not None and isinstance(media_metadata, dict):
            BasicMedia.objects.annotate_max_progress([runtime_media], media_type)
            total_runtime_display = runtime_media.formatted_total_runtime
            if total_runtime_display and total_runtime_display != "--":
                details = media_metadata.get("details")
                if not isinstance(details, dict):
                    details = {}
                    media_metadata["details"] = details

                if details.get("runtime"):
                    ordered_details = {}
                    for key, value in details.items():
                        ordered_details[key] = value
                        if key == "runtime":
                            ordered_details["total_runtime"] = total_runtime_display
                    details.clear()
                    details.update(ordered_details)
                else:
                    details["total_runtime"] = total_runtime_display

    # Apply the same rating aggregation logic as in the media list
    if user_medias and len(user_medias) > 1:
        latest_rating = None
        latest_activity = None

        for user_media in user_medias:
            if user_media.score is not None:
                if user_media.end_date:
                    entry_activity = user_media.end_date
                elif user_media.progressed_at:
                    entry_activity = user_media.progressed_at
                else:
                    entry_activity = user_media.created_at

                if latest_activity is None or entry_activity > latest_activity:
                    latest_activity = entry_activity
                    latest_rating = user_media.score

        if latest_rating is not None:
            current_instance.score = latest_rating

    if (
        render_secondary_only
        and not public_view
        and current_instance
        and media_type
        in (
            MediaTypes.GAME.value,
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MANGA.value,
        )
        and isinstance(media_metadata, dict)
    ):
        details = media_metadata.get("details", {})
        if not isinstance(details, dict):
            details = {}
        metadata_genres = stats._coerce_genre_list(
            media_metadata.get("genres")
            or details.get("genres")
            or media_metadata.get("genre")
            or details.get("genre"),
        )
        item = current_instance.item
        genres_updated = False
        if item:
            if metadata_genres and metadata_genres != item.genres:
                item.genres = metadata_genres
                genre_save_outcome = _best_effort_detail_db_work(
                    lambda: item.save(update_fields=["genres"]),
                    operation_name="detail genre sync",
                )
                genres_updated = not genre_save_outcome.deferred
                media_metadata["genres"] = metadata_genres
            elif item.genres:
                media_metadata["genres"] = item.genres
        if genres_updated and media_type in (
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MANGA.value,
        ):
            day_keys = _collect_reading_activity_day_keys(user_medias)
            if day_keys:
                statistics_cache.invalidate_statistics_days(
                    request.user.id,
                    day_values=day_keys,
                    reason="details_genres_update",
                )

    play_stats, activity_subtitle = _build_detail_activity_state(
        media_type,
        media_metadata,
        current_instance=current_instance,
        user_medias=user_medias,
        public_view=public_view,
    )

    drop_recommendations_if_hidden(request, media_metadata)

    # Enrich related items with user tracking data
    # For public views, use list owner's data if available
    if render_secondary_only and media_metadata.get("related"):
        enrich_detail_related_cards(
            request,
            media_metadata,
            media_type=media_type,
            tracking_user=list_owner,
        )

    # For music tracks, get linked artist and album for navigation
    music_artist = None
    music_album = None
    if media_type == MediaTypes.MUSIC.value and current_instance:
        music_artist = getattr(current_instance, "artist", None)
        music_album = getattr(current_instance, "album", None)

    notes_entry = None
    notes_entries = []
    if render_secondary_only and not public_view and user_medias:
        notes_entries = [
            entry for entry in user_medias if entry.notes and entry.notes.strip()
        ]
        notes_entry = notes_entries[0] if notes_entries else None
    elif render_secondary_only and public_notes_view and list_owner:
        public_user_medias = list(
            BasicMedia.objects.filter_media_prefetch(
                list_owner,
                media_id,
                media_type,
                source,
            ),
        )
        notes_entries = [
            entry for entry in public_user_medias if entry.notes and entry.notes.strip()
        ]
        notes_entry = notes_entries[0] if notes_entries else None

    if (
        render_secondary_only
        and media_type == MediaTypes.ANIME.value
        and not media_metadata.get("episodes")
    ):
        try:
            flat_anime_episode_preview = _build_flat_anime_episode_preview(
                request,
                detail_item=detail_item,
                media_id=media_id,
                base_metadata=media_metadata,
                metadata_resolution_result=metadata_resolution_result,
                retry_max_retries=detail_db_max_retries,
                on_persistence_deferred=_mark_detail_persistence_deferred,
            )
        except services.ProviderAPIError:
            logger.warning(
                "Skipping optional anime episode preview for media_id=%s due to provider API error",
                media_id,
            )
            flat_anime_episode_preview = None
        if flat_anime_episode_preview:
            media_metadata["episodes"] = flat_anime_episode_preview

    # Get collection entries for this item (if not public view and not podcast)
    collection_entry = None
    collection_entries = []
    collection_stats = None
    fetching_collection_data = False
    item_id_for_polling = None

    if (
        (render_secondary_only or media_type == MediaTypes.COMIC_ISSUE.value)
        and not public_view
        and media_type != MediaTypes.PODCAST.value
    ):
        from app.helpers import (
            get_item_collection_entries,
            get_tv_show_collection_stats,
        )

        try:
            item = detail_item or Item.objects.get(**detail_item_lookup)
            collection_entries = list(get_item_collection_entries(request.user, item))
            collection_entry = collection_entries[0] if collection_entries else None

            # For TV shows, also get collection statistics (episodes/seasons)
            if media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
                # Use episode count from metadata if available to match Details pane
                metadata_episode_count = _metadata_episode_count(media_metadata)
                collection_stats = get_tv_show_collection_stats(
                    request.user, item, metadata_episode_count=metadata_episode_count
                )

            # If no collection entry exists and auto-fetch is supported, trigger background fetch
            if not collection_entry and config.supports_collection_auto_fetch(
                media_type
            ):
                plex_account = getattr(request.user, "plex_account", None)
                if plex_account and plex_account.plex_token:
                    from integrations.tasks import fetch_collection_metadata_for_item

                    # Trigger background task to fetch collection data
                    followup_started = _best_effort_detail_followup(
                        lambda: fetch_collection_metadata_for_item.delay(
                            user_id=request.user.id,
                            item_id=item.id,
                            lookup_policy="cached_only",
                        ),
                        operation_name="collection metadata auto-fetch",
                        fallback=None,
                    )
                    if followup_started is not None:
                        # Use module-level logger directly to avoid UnboundLocalError
                        logging.getLogger(__name__).info(
                            "Triggered background collection fetch for %s - %s (item_id=%s)",
                            request.user.username,
                            item.title,
                            item.id,
                        )
                        # TODO(issue-166): Re-enable a user-facing collection-fetching banner only after
                        # the background task reliably self-resolves for empty collections; remove this
                        # reminder once that task/UX overhaul is complete.
                        fetching_collection_data = True
                        item_id_for_polling = item.id
        except Item.DoesNotExist:
            pass

    has_collection_data = bool(collection_entries) or collection_entry is not None
    detail_skeleton_heights = (
        _detail_skeleton_heights(
            media_metadata,
            media_type,
            studios_count=len(studios_linked or []),
            has_collection_data=has_collection_data,
            has_details=bool(media_metadata.get("details")),
            genres_count=len(media_metadata.get("genres") or []),
        )
        if defer_detail_secondary
        else {"desktop": 0, "mobile": 0}
    )

    if media_type in [
        MediaTypes.TV.value,
        MediaTypes.MOVIE.value,
        MediaTypes.ANIME.value,
    ]:
        watch_provider_region = (
            request.user.watch_provider_region
            if request.user.is_authenticated
            else None
        )
        watch_provider_payload = media_metadata.get("providers")
        tmdb_media_id = None
        tmdb_media_type = media_type
        if (
            render_secondary_only
            and media_type == MediaTypes.ANIME.value
            and source == Sources.MAL.value
            and not watch_provider_payload
            and watch_provider_region != "UNSET"
        ):
            try:
                identity = metadata_resolution.resolve_mal_tmdb_identity(media_id)
            except (services.ProviderAPIError, TypeError, ValueError) as error:
                logger.warning(
                    "Skipping watch providers for MAL anime media_id=%s: "
                    "mapping resolution failed: %s",
                    media_id,
                    exception_summary(error),
                )
                identity = None
            if identity:
                tmdb_media_id = identity.media_id
                tmdb_media_type = identity.media_type
        elif (
            render_secondary_only
            and detail_item
            and media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value)
            and (not watch_provider_payload or source == Sources.TVDB.value)
        ):
            tmdb_media_id = metadata_resolution.resolve_provider_media_id(
                detail_item,
                Sources.TMDB.value,
                route_media_type=media_type,
                persistence_mode="best_effort",
                retry_max_retries=detail_db_max_retries,
                on_deferred=_mark_detail_persistence_deferred,
                persist_links=False,
            )

        if tmdb_media_id:
            try:
                tmdb_metadata = services.get_media_metadata(
                    tmdb_media_type,
                    tmdb_media_id,
                    Sources.TMDB.value,
                    language=metadata_resolution.metadata_language_default(
                        request.user, detail_item
                    ),
                )
            except services.ProviderAPIError:
                # Watch providers are TMDB-only enrichment. A dead TMDB
                # mapping must not take down a page the tracking provider
                # can render on its own.
                logger.warning(
                    "Skipping watch providers for %s media_id=%s: mapped TMDB "
                    "ID %s could not be fetched",
                    source,
                    media_id,
                    tmdb_media_id,
                )
            else:
                watch_provider_payload = tmdb_metadata.get("providers")

        if (
            detail_item
            and isinstance(watch_provider_payload, dict)
            and watch_provider_payload
            and detail_item.watch_providers != watch_provider_payload
        ):
            detail_item.watch_providers = watch_provider_payload
            _best_effort_detail_followup(
                lambda: detail_item.save(update_fields=["watch_providers"]),
                operation_name="watch provider cache refresh",
                fallback=None,
            )

        watch_providers = (
            tmdb.filter_providers(
                watch_provider_payload,
                watch_provider_region,
            )
            if watch_provider_payload is not None
            else None
        )

        if request.user.is_authenticated and request.user.pinned_watch_providers:
            pinned_matches = tmdb.pinned_provider_matches(
                detail_item, request.user.pinned_watch_providers
            )
            if pinned_matches:
                merged = {p["provider_id"]: p for p in (watch_providers or [])}
                for provider in pinned_matches:
                    merged.setdefault(provider["provider_id"], provider)
                watch_providers = sorted(
                    merged.values(), key=lambda e: e.get("display_priority", 999)
                )
    else:
        watch_providers = None

    display_provider = (
        metadata_resolution_result.display_provider
        if metadata_resolution_result
        else source
    )
    identity_provider = (
        metadata_resolution_result.identity_provider
        if metadata_resolution_result
        else source
    )
    grouped_preview = (
        metadata_resolution_result.grouped_preview
        if metadata_resolution_result
        else None
    )
    grouped_preview_target = (
        metadata_resolution_result.grouped_preview_target
        if metadata_resolution_result
        else None
    )
    metadata_provider_options = metadata_resolution.available_metadata_provider_options(
        media_type,
        identity_provider=identity_provider,
    )
    can_update_metadata_provider = bool(
        not public_view and detail_item is not None and metadata_provider_options
    )
    can_migrate_grouped_anime = False
    migrated_grouped_item = None
    migrated_grouped_title = None
    if (
        render_secondary_only
        and not public_view
        and media_type == MediaTypes.ANIME.value
        and detail_item is not None
    ):
        migrated_entry = (
            Anime.all_objects.filter(
                user=request.user,
                item=detail_item,
                migrated_to_item__isnull=False,
            )
            .select_related("migrated_to_item")
            .order_by("-migrated_at")
            .first()
        )
        if migrated_entry and migrated_entry.migrated_to_item:
            migrated_grouped_item = migrated_entry.migrated_to_item
            migrated_grouped_title = migrated_grouped_item.get_display_title(
                request.user,
            )

        can_migrate_grouped_anime = bool(
            detail_item.source == Sources.MAL.value
            and detail_item.media_type == MediaTypes.ANIME.value
            and display_provider in {Sources.TMDB.value, Sources.TVDB.value}
            and grouped_preview
            and Anime.objects.filter(user=request.user, item=detail_item).exists()
        )

    episode_load_more = None
    if (
        render_secondary_only
        and media_type != MediaTypes.PODCAST.value
        and media_metadata.get("episodes")
    ):
        media_metadata["episodes"] = _normalize_detail_episode_actions(
            media_metadata["episodes"],
        )
        media_metadata["episodes"], episode_load_more = _paginate_detail_episodes(
            request,
            media_metadata["episodes"],
        )

    if media_type == MediaTypes.VIDEO.value and not render_secondary_only:
        from app.image_cache import rewrite_image_url
        from app.templatetags.app_tags import youtube_landscape_thumbnail
        from app.youtube_channel_art import VIDEO_ID

        is_youtube_video = source == Sources.YOUTUBE.value and VIDEO_ID.fullmatch(
            str(media_id)
        )
        image = media_metadata.get("image")
        if not helpers.has_real_image(image) and is_youtube_video:
            image = f"https://i.ytimg.com/vi/{media_id}/hqdefault.jpg"
        if helpers.has_real_image(image):
            thumbnail = rewrite_image_url(youtube_landscape_thumbnail(image))
            initial_carousel = {
                "overview": {
                    "url": thumbnail,
                    "thumb_url": thumbnail,
                    "fallback_url": rewrite_image_url(image),
                },
                "video": {"key": media_id, "show_player_initially": True}
                if is_youtube_video
                else None,
                "photos": [],
                "hero": None,
            }
            carousel_supported = True

    book_formats = (
        [] if public_view else build_book_formats(request.user, detail_item)
    )
    context = {
        "user": request.user,
        "media": media_metadata,
        "media_type": media_type,
        "match_item": detail_item,
        "book_formats": book_formats,
        "book_formats_replaces": "format" if book_formats else "",
        "authors_linked": authors_linked,
        "issue_extra_details": (
            _issue_extra_details(media_metadata, authors_linked)
            if media_type == MediaTypes.COMIC_ISSUE.value
            else []
        ),
        "author_detail_keys": author_detail_keys,
        "studios_linked": studios_linked,
        "studio_detail_keys": studio_detail_keys,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "music_artist": music_artist,
        "music_album": music_album,
        "public_view": public_view,
        "public_notes_view": public_notes_view,
        "show_notes": not public_view or public_notes_view,
        "play_stats": play_stats,
        "activity_subtitle": activity_subtitle,
        "trakt_score": trakt_score,
        "imdb_score": imdb_score,
        "mal_score": mal_score,
        "opencritic_score": opencritic_score,
        "game_lengths": game_lengths,
        "youtube_watch": (
            youtube_game_watch.watched_for_game(request.user, detail_item)
            if render_secondary_only
            and media_type == MediaTypes.GAME.value
            and detail_item
            and request.user.is_authenticated
            and not public_view
            else None
        ),
        "game_lengths_pending": game_lengths_refresh_pending
        and not (game_lengths and game_lengths.get("available")),
        "notes_entry": notes_entry,
        "notes_entries": notes_entries,
        "collection_entry": collection_entry,
        "collection_entries": collection_entries,
        "collection_stats": collection_stats,
        "has_collection_data": has_collection_data,
        "fetching_collection_data": fetching_collection_data
        if not public_view
        else False,
        "item_id_for_polling": item_id_for_polling if not public_view else None,
        "watch_providers": watch_providers,
        "watch_provider_region": request.user.watch_provider_region
        if request.user.is_authenticated
        else None,
        "detail_link_sections": _build_detail_link_sections(
            media_metadata,
            media_type,
            identity_provider,
            display_provider,
            item=detail_item,
        ),
        "detail_tag_sections": _build_detail_tag_sections(
            media_metadata,
            detail_item,
            request.user,
            genre_list_media_type=media_type,
        ),
        "detail_tag_preview_genres_json": json.dumps(
            _resolve_detail_tag_genres(media_metadata, detail_item)
        ),
        "display_provider": display_provider,
        "identity_provider": identity_provider,
        "provider_series_graph_data": (
            # Priority: episode-level DB data → full grid from season cache → season averages
            _build_series_graph_data(display_provider, media_id)
            or _build_episode_graph_from_season_cache(
                display_provider,
                media_id,
                (media_metadata.get("related") or {}).get("seasons"),
            )
            or _build_season_scores_graph(
                (media_metadata.get("related") or {}).get("seasons"),
                display_provider,
            )
            if graphs_rendered
            and display_provider in {Sources.TMDB.value, Sources.TVDB.value}
            else None
        ),
        "trakt_series_graph_data": (
            _build_series_graph_data(display_provider, media_id, use_trakt=True)
            or _build_stored_season_scores_graph(
                display_provider, media_id, use_trakt=True
            )
            if graphs_rendered
            else None
        ),
        "imdb_series_graph_data": (
            _build_series_graph_data(display_provider, media_id, use_imdb=True)
            or _build_stored_season_scores_graph(
                display_provider, media_id, use_imdb=True
            )
            if graphs_rendered
            else None
        ),
        "metadata_provider_options": metadata_provider_options,
        "metadata_provider_mapping_status": (
            metadata_resolution_result.mapping_status
            if metadata_resolution_result
            else "identity"
        ),
        "grouped_preview": grouped_preview,
        "grouped_preview_target": grouped_preview_target,
        "can_update_metadata_provider": can_update_metadata_provider,
        "can_migrate_grouped_anime": can_migrate_grouped_anime,
        "hardcover_edition_id": hardcover_edition_id,
        "migrated_grouped_item": migrated_grouped_item,
        "migrated_grouped_title": migrated_grouped_title,
        "episode_load_more": episode_load_more,
        "detail_persistence_deferred": detail_persistence_deferred,
        "detail_return_url": detail_return_url,
        "detail_secondary_fragment_url": detail_secondary_fragment_url,
        "defer_detail_secondary": defer_detail_secondary,
        "detail_secondary_skeleton_desktop": detail_skeleton_heights["desktop"],
        "detail_secondary_skeleton_mobile": detail_skeleton_heights["mobile"],
        "render_secondary_only": render_secondary_only,
        "carousel_supported": carousel_supported,
        "initial_carousel": initial_carousel,
        "detail_carousel_fragment_url": detail_carousel_fragment_url,
        **_build_detail_person_rows(media_metadata, item=detail_item),
    }
    logger.info(
        "detail_render_complete path=%s phase=%s media_type=%s source=%s duration_ms=%.2f",
        request.path,
        "secondary" if render_secondary_only else "shell",
        media_type,
        source,
        (time.perf_counter() - detail_view_started_at) * 1000,
    )
    if media_type == MediaTypes.COMIC_ISSUE.value:
        template = "app/comic_issue_details.html"
    elif render_secondary_only:
        template = "app/components/detail_secondary_content.html"
    else:
        template = "app/media_details.html"
    return render(
        request,
        template,
        context,
    )
