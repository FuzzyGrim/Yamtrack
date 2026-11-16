import json
import logging
from collections import defaultdict
from datetime import UTC
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext, ngettext
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import helpers
from app.discover import tab_cache as discover_tab_cache
from app.models import Item, MediaTypes, Sources
from app.templatetags import app_tags
from app.track_modal_views import _DummyPodcastWrapper, _render_podcast_show_track_modal

logger = logging.getLogger(__name__)


def episode_items_by_uuid(show, episodes, *, source, media_type):
    """Return ``{episode_uuid: Item}`` for ``episodes``, creating missing Items.

    Episodes are Items only once someone looks at them, so a page creates the
    ones it has not seen. The existing ones are read in a single query.
    """
    items = {
        item.media_id: item
        for item in Item.objects.filter(
            media_id__in=[episode.episode_uuid for episode in episodes],
            source=source,
            media_type=media_type,
        )
    }
    for episode in episodes:
        item = items.get(episode.episode_uuid)
        if item is None:
            item, _ = Item.objects.get_or_create(
                media_id=episode.episode_uuid,
                source=source,
                media_type=media_type,
                defaults={
                    "title": episode.title,
                    "image": episode.image or show.image or settings.IMG_NONE,
                },
            )
            items[episode.episode_uuid] = item
        if item.title != episode.title:
            item.title = episode.title
            item.save(update_fields=["title"])
    return items


def user_podcasts_by_episode(user, show, episodes):
    """Return ``{episode_id: [Podcast, ...]}``, latest ``end_date`` first."""
    from app.models import Podcast

    by_episode = defaultdict(list)
    for podcast in Podcast.objects.filter(
        user=user,
        show=show,
        episode__in=episodes,
    ).order_by("-end_date"):
        by_episode[podcast.episode_id].append(podcast)
    return by_episode


PODCAST_DETAIL_SOURCES = frozenset(
    {
        Sources.POCKETCASTS.value,
        Sources.GPODDER.value,
        Sources.AUDIOBOOKSHELF.value,
        Sources.MANUAL.value,
    },
)


def ordered_episodes(show):
    """Return the show's episodes newest first, undated ones last.

    The one ordering every podcast episode list uses: the show page's first
    page, the infinite-scroll pages and the API pages.
    """
    from datetime import datetime

    from django.db.models import DateTimeField, Value
    from django.db.models.functions import Coalesce

    from app.models import PodcastEpisode

    return (
        PodcastEpisode.objects.filter(show=show)
        .annotate(
            published_or_old=Coalesce(
                "published",
                Value(datetime(1970, 1, 1, tzinfo=UTC), output_field=DateTimeField()),
            ),
        )
        .order_by("-published_or_old", "-episode_number")
    )


def podcast_show_url(show):
    """Return the URL of a show's page.

    Every show has one page, the detail page that pages its episodes. A show
    from a source that page does not route by opens under Pocket Casts, which
    is how History and Statistics link to shows.
    """
    from django.urls import reverse

    source = show.source if show.source in PODCAST_DETAIL_SOURCES else Sources.POCKETCASTS.value
    return reverse(
        "media_details",
        kwargs={
            "source": source,
            "media_type": MediaTypes.PODCAST.value,
            "media_id": show.podcast_uuid,
            "title": app_tags.slug(show.title or "") or "podcast",
        },
    )


def podcast_show_totals(user, show):
    """Return the viewer's listen totals for a show, counted in SQL.

    A show with thousands of listens is summed by the database rather than
    loaded row by row. ``first_played`` and ``last_played`` span each
    listen's start, end or creation time, in that order of preference.
    """
    from django.db.models import Count, Max, Min, Q, Sum
    from django.db.models.functions import Coalesce

    from app.models import Podcast, Status

    totals = Podcast.objects.filter(user=user, show=show).aggregate(
        listened=Count("id"),
        minutes=Coalesce(Sum("progress"), 0),
        completed=Count(
            "id",
            filter=Q(end_date__isnull=False) | Q(status=Status.COMPLETED.value),
        ),
        first_played=Min(Coalesce("start_date", "end_date", "created_at")),
        last_played=Max(Coalesce("end_date", "start_date", "created_at")),
    )
    totals["minutes"] = int(totals["minutes"] or 0)
    return totals


class PodcastEpisodeAdapter:
    """A PodcastEpisode shaped like a music Track for the shared track modal."""

    def __init__(self, episode):
        """Copy the fields the track modal reads."""
        self.title = episode.title
        self.description = episode.description or ""
        self.track_number = episode.episode_number
        self.duration_formatted = episode.duration_formatted
        self.musicbrainz_recording_id = None
        self.id = episode.id
        self.published = episode.published
        self.episode_uuid = episode.episode_uuid


class PodcastShowAdapter:
    """A PodcastShow shaped like a music Album for the shared track modal."""

    def __init__(self, show):
        """Copy the fields the track modal reads."""
        self.image = show.image or settings.IMG_NONE
        self.release_date = None
        self.id = show.id


class _HistoryListProxy:
    """The slice of a history manager the episode templates call."""

    def __init__(self, history_list):
        self._history = history_list

    def all(self):
        return self._history

    def count(self):
        return len(self._history)


class PodcastHistoryWrapper:
    """One listen of an episode with its completed plays, for the track modal."""

    def __init__(self, podcast, item, history_list):
        """Wrap ``podcast`` with its ``item`` and newest-first plays."""
        self.item = item
        self.id = podcast.id
        self._history_list = history_list
        self.in_progress_instance_id = podcast.id if not podcast.end_date else None

    @property
    def completed_play_count(self):
        """Return how many completed plays the page loaded."""
        return len(self._history_list)

    @property
    def has_in_progress_entry(self):
        """Return whether this listen is still unfinished."""
        return bool(self.in_progress_instance_id)

    @property
    def history(self):
        """Return the plays behind a manager-like ``all()``/``count()``."""
        return _HistoryListProxy(self._history_list)


def completed_plays_by_podcast_id(podcast_ids, limit=None):
    """Return ``{podcast_id: [PodcastPlay, ...]}``, newest completed play first.

    ``limit`` keeps only that many plays per podcast, in SQL, for callers
    that display a few plays of an entry with a long history.
    """
    from django.db.models import F, Window
    from django.db.models.functions import RowNumber

    from app.models import PodcastPlay

    by_podcast = defaultdict(list)
    if not podcast_ids:
        return by_podcast
    plays = PodcastPlay.objects.filter(
        podcast_id__in=podcast_ids,
        end_date__isnull=False,
    )
    if limit is not None:
        plays = plays.annotate(
            play_rank=Window(
                RowNumber(),
                partition_by=F("podcast_id"),
                order_by=[F("end_date").desc(), F("id").desc()],
            ),
        ).filter(play_rank__lte=limit)
    for play in plays.order_by("-end_date", "-id"):
        by_podcast[play.podcast_id].append(play)
    return by_podcast


def manual_show_for_user_or_404(request, show_id):
    """Return a manual podcast show the viewer tracks, or 404.

    Provider-backed shows keep their feed-owned catalog, and a manual show is
    only manageable by someone who has it in their library.
    """
    from app.models import PodcastShow

    return get_object_or_404(
        PodcastShow,
        id=show_id,
        source=Sources.MANUAL.value,
        trackers__user=request.user,
    )


def manual_episode_for_user_or_404(request, episode_id):
    """Return a manual episode on a show the viewer tracks, or 404."""
    from app.models import PodcastEpisode

    return get_object_or_404(
        PodcastEpisode.objects.select_related("show"),
        id=episode_id,
        show__source=Sources.MANUAL.value,
        show__trackers__user=request.user,
    )


def episode_manager_props(request, show):
    """Return the manual episode manager model for a show, when it applies."""
    if show.source != Sources.MANUAL.value:
        return None
    if not show.trackers.filter(user=request.user).exists():
        return None

    from django.urls import reverse

    return {
        "listUrl": reverse("podcast_episodes_manage", args=[show.id]),
        "addUrl": reverse("podcast_episode_add", args=[show.id]),
        "csrfToken": get_token(request),
        "labels": {
            "trigger": gettext("Manage episodes"),
            "title": gettext("Manage episodes"),
            "add": gettext("Add episode"),
            "edit": gettext("Edit"),
            "delete": gettext("Delete"),
            "save": gettext("Save"),
            "cancel": gettext("Cancel"),
            "close": gettext("Close"),
            "back": gettext("Back to episodes"),
            "loading": gettext("Loading episodes…"),
            "loadFailed": gettext("Could not load episodes."),
            "retry": gettext("Try again"),
            "empty": gettext("No episodes yet."),
            "deleteConfirm": gettext("Delete this episode?"),
            "deleteNote": gettext("Its play history stays in your account."),
            "titleField": gettext("Episode title"),
            "descriptionField": gettext("Description"),
            "episodeNumberField": gettext("Episode number"),
            "seasonNumberField": gettext("Season number"),
            "publishedField": gettext("Publication date"),
            "durationField": gettext("Duration"),
            "durationHelp": gettext("Minutes, mm:ss or h:mm:ss."),
            "audioField": gettext("Audio URL"),
            "websiteField": gettext("Website URL"),
            "imageField": gettext("Artwork URL"),
            "saveFailed": gettext("Could not save the episode."),
            "deleteFailed": gettext("Could not delete the episode."),
        },
    }


def episode_payload(episode):
    """Serialize one manual episode for the manager."""
    from django.urls import reverse

    from app.forms import episode_duration_input

    published = episode.published
    if published and timezone.is_aware(published):
        published = timezone.localtime(published)

    return {
        "id": episode.id,
        "title": episode.title,
        "description": episode.description or "",
        "episodeNumber": episode.episode_number,
        "seasonNumber": episode.season_number,
        "published": published.strftime("%Y-%m-%dT%H:%M") if published else "",
        "duration": episode_duration_input(episode.duration),
        "audioUrl": episode.audio_url or "",
        "websiteUrl": episode.website_url or "",
        "imageUrl": episode.image or "",
        "editUrl": reverse("podcast_episode_edit", args=[episode.id]),
        "deleteUrl": reverse("podcast_episode_delete", args=[episode.id]),
    }


def _wants_json(request):
    """Return whether a request asked for the manager's JSON response."""
    return (
        request.GET.get("format") == "json"
        or request.POST.get("format") == "json"
        or "application/json" in request.headers.get("Accept", "")
    )


def _form_error_payload(form):
    """Return Django form errors as ``{field: [message, ...]}``."""
    return {
        field: [entry["message"] for entry in entries]
        for field, entries in form.errors.get_json_data().items()
    }


@require_http_methods(["GET"])
def podcast_episodes_manage(request, show_id):
    """Show the fallback page, or return the manager's episode list as JSON."""
    from app.models import PodcastEpisode

    show = manual_show_for_user_or_404(request, show_id)
    episodes = list(
        PodcastEpisode.objects.filter(show=show).order_by(
            "-published",
            "-episode_number",
            "id",
        ),
    )

    if _wants_json(request):
        return JsonResponse(
            {"episodes": [episode_payload(episode) for episode in episodes]},
        )

    from app.forms import PodcastEpisodeForm

    edit_episode = None
    episode_id = request.GET.get("episode")
    if episode_id:
        edit_episode = manual_episode_for_user_or_404(request, episode_id)
        if edit_episode.show_id != show.id:
            edit_episode = None
    form = PodcastEpisodeForm(instance=edit_episode)

    from django.urls import reverse

    return render(
        request,
        "app/podcast_episodes_manage.html",
        {
            "show": show,
            "episodes": episodes,
            "form": form,
            "edit_episode": edit_episode,
            "show_url": reverse(
                "media_details",
                kwargs={
                    "source": show.source,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": app_tags.slug(show.title or "") or "podcast",
                },
            ),
        },
    )


@require_POST
def podcast_episode_add(request, show_id):
    """Create a manually managed episode on a manual show."""
    from app.forms import PodcastEpisodeForm

    show = manual_show_for_user_or_404(request, show_id)
    form = PodcastEpisodeForm(request.POST)

    if not form.is_valid():
        if _wants_json(request):
            return JsonResponse(
                {"ok": False, "errors": _form_error_payload(form)},
                status=400,
            )
        messages.error(request, gettext("Could not add the episode."))
        return redirect("podcast_episodes_manage", show_id=show.id)

    episode = form.save(commit=False)
    episode.show = show
    episode.episode_uuid = f"manual-{uuid4()}"
    episode.save()

    if _wants_json(request):
        return JsonResponse(
            {"ok": True, "episode": episode_payload(episode)},
        )

    messages.success(
        request,
        gettext("Added %(value_1)s") % {"value_1": episode.title},
    )
    return redirect("podcast_episodes_manage", show_id=show.id)


@require_POST
def podcast_episode_edit(request, episode_id):
    """Update a manually managed episode."""
    from app.forms import PodcastEpisodeForm

    episode = manual_episode_for_user_or_404(request, episode_id)
    form = PodcastEpisodeForm(request.POST, instance=episode)

    if not form.is_valid():
        if _wants_json(request):
            return JsonResponse(
                {"ok": False, "errors": _form_error_payload(form)},
                status=400,
            )
        messages.error(request, gettext("Could not update the episode."))
        return redirect(
            "podcast_episodes_manage",
            show_id=episode.show_id,
        )

    form.save()

    if _wants_json(request):
        return JsonResponse(
            {"ok": True, "episode": episode_payload(episode)},
        )

    messages.success(
        request,
        gettext("Saved %(value_1)s") % {"value_1": episode.title},
    )
    return redirect("podcast_episodes_manage", show_id=episode.show_id)


@require_POST
def podcast_episode_delete(request, episode_id):
    """Delete a manually managed episode."""
    episode = manual_episode_for_user_or_404(request, episode_id)
    show_id = episode.show_id
    title = episode.title
    episode.delete()

    if _wants_json(request):
        return JsonResponse({"ok": True})

    messages.success(
        request,
        gettext("Deleted %(value_1)s") % {"value_1": title},
    )
    return redirect("podcast_episodes_manage", show_id=show_id)


@require_GET
def podcast_show_detail(request, show_id):
    """Send the old show URL to the show's one page.

    This page used to list every episode and check each one against every
    listen, so a long show took many seconds. The detail page shows the
    same show a page of episodes at a time.
    """
    from app.models import PodcastShow

    show = get_object_or_404(PodcastShow, id=show_id)
    return redirect(podcast_show_url(show))


@require_GET
def podcast_show_track_modal(request, show_id):
    """Return the tracking form modal for a podcast show."""
    from app.models import PodcastShow

    show = get_object_or_404(PodcastShow, id=show_id)
    return _render_podcast_show_track_modal(request, show)


@require_GET
def podcast_episodes_api(request, show_id):
    """Return a page of podcast episodes.

    Returns HTML fragments for infinite scroll if format=html, otherwise JSON.
    """
    from app.models import Podcast, PodcastShow

    show = get_object_or_404(PodcastShow, id=show_id)
    format_type = request.GET.get("format", "json")

    try:
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
    except ValueError:
        page = 1
        page_size = 20

    episodes_qs = ordered_episodes(show)
    total_count = episodes_qs.count()

    start = (page - 1) * page_size
    end = start + page_size
    episodes = episodes_qs[start:end]

    episodes = list(episodes)
    user_podcasts = list(
        Podcast.objects.filter(
            user=request.user,
            show=show,
            episode__in=episodes,
        )
        .select_related("episode", "item")
        .order_by("episode_id", "-created_at")
    )

    episode_podcast_map = {}
    for podcast in user_podcasts:
        if podcast.episode_id and podcast.episode_id not in episode_podcast_map:
            episode_podcast_map[podcast.episode_id] = podcast
    completed_plays = completed_plays_by_podcast_id(
        {podcast.id for podcast in episode_podcast_map.values()},
        limit=10,
    )

    episode_items_map = episode_items_by_uuid(
        show,
        episodes,
        source=show.source,
        media_type=MediaTypes.PODCAST.value,
    )
    episode_items_data = [
        {
            "media_id": episode.episode_uuid,
            "source": show.source,
            "media_type": MediaTypes.PODCAST.value,
        }
        for episode in episodes
    ]

    enriched_by_media_id = {
        enriched["item"]["media_id"]: enriched
        for enriched in helpers.enrich_items_with_user_data(
            request,
            episode_items_data,
            user=request.user,
        )
    }

    has_more = end < total_count
    next_page = page + 1 if has_more else None

    if format_type == "html":
        from django.template.loader import render_to_string

        episode_list = []
        for episode_obj in episodes:
            enriched = enriched_by_media_id.get(episode_obj.episode_uuid)
            item = enriched["item"] if enriched else episode_items_map.get(
                episode_obj.episode_uuid,
            )
            duration_str = helpers.seconds_to_hm(episode_obj.duration)

            user_podcast = episode_podcast_map.get(episode_obj.id)
            all_history = []
            if user_podcast:
                all_history = completed_plays.get(user_podcast.id, [])
                podcast_wrapper = PodcastHistoryWrapper(user_podcast, item, all_history)
            else:
                podcast_wrapper = _DummyPodcastWrapper(item)

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
                    "history": all_history,
                    "media": enriched["media"] if enriched else None,
                    "item": item,
                    "media_id": episode_obj.episode_uuid,
                    "source": show.source,
                    "media_type": MediaTypes.PODCAST.value,
                    "track_adapter": PodcastEpisodeAdapter(episode_obj),
                    "album_adapter": PodcastShowAdapter(show),
                    "music_wrapper": podcast_wrapper,
                }
            )

        html = render_to_string(
            "app/components/podcast_episode_list.html",
            {
                "episodes": episode_list,
                "user": request.user,
                "show": show,
                "IMG_NONE": settings.IMG_NONE,
                "TRACK_TIME": True,
                "has_more": has_more,
                "next_page": next_page,
                "show_id": show_id,
            },
            request=request,
        )
        response = HttpResponse(html)
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response

    # Return JSON
    episode_list = []
    for episode_obj in episodes:
        enriched = enriched_by_media_id.get(episode_obj.episode_uuid)

        duration_str = helpers.seconds_to_hm(episode_obj.duration)

        user_podcast = episode_podcast_map.get(episode_obj.id)
        status = user_podcast.status if user_podcast else None

        episode_data = {
            "id": episode_obj.id,
            "title": episode_obj.title,
            "description": episode_obj.description or "",
            "image": episode_obj.image or show.image or settings.IMG_NONE,
            "published": episode_obj.published.isoformat()
            if episode_obj.published
            else None,
            "duration": duration_str,
            "duration_seconds": episode_obj.duration,
            "episode_number": episode_obj.episode_number,
            "website_url": episode_obj.website_url,
            "status": status,
            "has_history": enriched and enriched.get("media") is not None,
        }
        episode_list.append(episode_data)

    total_pages = (total_count + page_size - 1) // page_size

    return JsonResponse(
        {
            "episodes": episode_list,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_more": has_more,
            },
        }
    )


@require_POST
def podcast_show_save(request):
    """Save a podcast show tracker - mirrors artist_save."""
    from app.forms import PodcastShowTrackerForm
    from app.models import Podcast, PodcastShow, PodcastShowTracker

    show_id = request.POST.get("show_id")
    show = get_object_or_404(PodcastShow, id=show_id)
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.PODCAST.value,
    )
    home_row_id = request.GET.get("home_row_id") or request.POST.get("home_row_id", "")

    tracker = PodcastShowTracker.objects.filter(user=request.user, show=show).first()
    old_status = getattr(tracker, "status", None)

    form = PodcastShowTrackerForm(request.POST, instance=tracker, user=request.user)
    if form.is_valid():
        tracker = form.save(commit=False)
        tracker.user = request.user
        tracker.show = show
        tracker.save()
        messages.success(
            request, gettext("Saved %(value_1)s") % {"value_1": show.title}
        )

        if old_status != tracker.status:
            # Home/medialist read episode-level Podcast.status, not the
            # show tracker, so keep existing listens in sync with it.
            updated = Podcast.objects.filter(user=request.user, show=show).update(
                status=tracker.status,
            )
            if updated:
                from app import cache_utils

                cache_utils.clear_library_view_caches(
                    request.user.id,
                    media_types=(MediaTypes.PODCAST.value,),
                )

        if request.headers.get("HX-Request"):
            htmx_trigger = {
                "closeModal": {},
                "showToast": {
                    "message": f"Saved {show.title}.",
                    "type": "success",
                },
            }
            if home_row_id and old_status != tracker.status:
                htmx_trigger["refreshHomeRow"] = {"rowId": int(home_row_id)}
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(htmx_trigger)
            return response
    else:
        messages.error(
            request,
            gettext("Error saving %(value_1)s: %(value_2)s")
            % {"value_1": show.title, "value_2": form.errors},
        )

    next_url = helpers.safe_return_url(request, request.GET.get("next", ""), "")
    if next_url:
        redirect_response = redirect(next_url)
    else:
        redirect_response = redirect(podcast_show_url(show))
    if request.headers.get("HX-Request"):
        return HttpResponse(status=204, headers={"HX-Redirect": redirect_response.url})
    return redirect_response


@require_POST
def podcast_show_delete(request):
    """Delete a podcast show tracker - mirrors artist_delete."""
    from app.models import PodcastShow, PodcastShowTracker

    show_id = request.POST.get("show_id")
    show = get_object_or_404(PodcastShow, id=show_id)
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.PODCAST.value,
    )

    tracker = PodcastShowTracker.objects.filter(user=request.user, show=show).first()
    if tracker:
        tracker.delete()
        messages.success(
            request,
            gettext("Removed %(value_1)s from your library") % {"value_1": show.title},
        )

    next_url = helpers.safe_return_url(request, request.GET.get("next", ""), "")
    if next_url:
        redirect_response = redirect(next_url)
    else:
        redirect_response = redirect(podcast_show_url(show))
    if request.headers.get("HX-Request"):
        return HttpResponse(status=204, headers={"HX-Redirect": redirect_response.url})
    return redirect_response


@require_POST
def podcast_mark_all_played(request, show_id):
    """Mark all episodes of this podcast currently in the library as completed on their release date.

    Episodes not yet imported from Pocket Casts are not included — run a Pocket Casts
    import first to fetch the full episode list.
    """
    from app.models import PodcastShow

    show = get_object_or_404(PodcastShow, id=show_id)

    # FORK: mark-all-played core shared with the REST API.
    from app import fork_services_podcast

    created_count = fork_services_podcast.mark_all_episodes_played(
        request.user,
        show,
    )

    if created_count == 0:
        messages.info(
            request,
            gettext("All episodes of %(value_1)s are already marked as played")
            % {"value_1": show.title},
        )
    else:
        messages.success(
            request,
            ngettext(
                "Marked %(count)s episode of %(title)s as played",
                "Marked %(count)s episodes of %(title)s as played",
                created_count,
            )
            % {"count": created_count, "title": show.title},
        )

    return redirect(
        "media_details",
        source=show.source,
        media_type=MediaTypes.PODCAST.value,
        media_id=show.podcast_uuid,
        title=show.slug or show.title,
    )


@require_POST
def podcast_save(request):
    """Handle adding a play for a podcast episode - mirrors song_save for music."""
    from app.models import Podcast, PodcastEpisode, PodcastShow

    episode_uuid = request.POST.get("episode_uuid")
    show_id = request.POST.get("show_id")
    episode_id = request.POST.get("episode_id")
    end_date_str = request.POST.get("end_date")
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.PODCAST.value,
    )

    show = get_object_or_404(PodcastShow, id=show_id)
    episode = get_object_or_404(PodcastEpisode, id=episode_id) if episode_id else None

    # A blank date means "now" and is resolved in the service. A date that was
    # supplied but cannot be read is a different case: recording it as "now"
    # would store a time the user did not choose and never tell them.
    end_date = None
    if end_date_str:
        try:
            end_date = helpers.parse_completion_datetime(end_date_str)
        except (TypeError, ValueError):
            message = gettext(
                'Floppy cannot read the date "%(value_1)s". Enter the date as YYYY-MM-DD. To record this play at the current time, keep the date empty.'
            ) % {"value_1": end_date_str}
            if request.headers.get("HX-Request"):
                # Do not swap and do not close the modal. The date the user
                # typed stays on screen for them to correct. Closing it would
                # make them find the episode and type everything again.
                response = HttpResponse(status=200)
                response["HX-Reswap"] = "none"
                response["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": message, "type": "error"}},
                )
                return response
            messages.error(request, message)
            return redirect(
                "media_details",
                source=show.source,
                media_type=MediaTypes.PODCAST.value,
                media_id=show.podcast_uuid,
                title=show.slug or app_tags.slug(show.title or "") or "podcast",
            )

    # FORK: play-recording core shared with the REST API.
    from app import fork_services_podcast

    recorded_podcast, duplicate = fork_services_podcast.record_podcast_play(
        request.user,
        show,
        episode=episode,
        episode_uuid=episode_uuid,
        end_date=end_date,
    )
    item = recorded_podcast.item
    episode_title = episode.title if episode else "episode"
    if duplicate:
        # Say that nothing was added. A plain "already recorded" reads as
        # success, and the user then finds the play count unchanged.
        messages.info(
            request,
            gettext(
                "Floppy recorded a play for %(value_1)s less than five minutes ago. Floppy did not add a second play."
            )
            % {"value_1": episode_title},
        )
    else:
        messages.success(
            request, gettext("Added play for %(value_1)s") % {"value_1": episode_title}
        )

    if request.headers.get("HX-Request"):
        from django.template.loader import render_to_string

        episode_obj = episode
        if not episode_obj:
            return HttpResponse("Episode not found", status=404)

        user_podcast = (
            Podcast.objects.filter(
                user=request.user,
                show=show,
                episode=episode_obj,
            )
            .order_by("-created_at")
            .first()
        )

        episode_items_data = [
            {
                "media_id": episode_obj.episode_uuid,
                "source": show.source,
                "media_type": MediaTypes.PODCAST.value,
            }
        ]
        enriched_episodes_raw = helpers.enrich_items_with_user_data(
            request,
            episode_items_data,
            user=request.user,
        )
        enriched = (
            enriched_episodes_raw[0]
            if enriched_episodes_raw
            else {"item": {"media_id": episode_obj.episode_uuid}, "media": None}
        )

        duration_str = helpers.seconds_to_hm(episode_obj.duration)

        all_history = []
        if user_podcast:
            all_history = list(
                user_podcast.plays.filter(end_date__isnull=False).order_by(
                    "-end_date",
                    "-id",
                )[:10]
            )

            class PodcastHistoryWrapper:
                def __init__(self, podcast, item, history_list):
                    self.item = item
                    self.id = podcast.id
                    self._history_list = history_list
                    self.in_progress_instance_id = (
                        podcast.id if not podcast.end_date else None
                    )

                @property
                def completed_play_count(self):
                    return len(self._history_list)

                @property
                def history(self):
                    class HistoryProxy:
                        def __init__(self, history_list):
                            self._history = history_list

                        def all(self):
                            return self._history

                        def count(self):
                            return len(self._history)

                    return HistoryProxy(self._history_list)

                @property
                def has_in_progress_entry(self):
                    return bool(self.in_progress_instance_id)

            podcast_wrapper = PodcastHistoryWrapper(user_podcast, item, all_history)
        else:
            podcast_wrapper = _DummyPodcastWrapper(item)

        class PodcastEpisodeAdapter:
            def __init__(self, episode):
                self.title = episode.title
                self.track_number = episode.episode_number
                self.duration_formatted = (
                    self._format_duration(episode.duration)
                    if episode.duration
                    else None
                )
                self.musicbrainz_recording_id = None
                self.id = episode.id
                self.published = episode.published
                self.episode_uuid = episode.episode_uuid

            def _format_duration(self, seconds):
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                secs = seconds % 60
                if hours > 0:
                    return f"{hours}:{minutes:02d}:{secs:02d}"
                return f"{minutes}:{secs:02d}"

        class PodcastShowAdapter:
            def __init__(self, show):
                self.image = show.image or settings.IMG_NONE
                self.id = show.id

        episode_data = {
            "title": episode_obj.title,
            "episode_number": episode_obj.episode_number or 0,
            "image": show.image or settings.IMG_NONE,
            "air_date": episode_obj.published,
            "runtime": duration_str,
            "overview": "",
            "website_url": episode_obj.website_url,
            "history": all_history,
            "media": enriched["media"] if enriched else None,
            "item": item,
            "media_id": episode_obj.episode_uuid,
            "source": show.source,
            "media_type": MediaTypes.PODCAST.value,
            "track_adapter": PodcastEpisodeAdapter(episode_obj),
            "album_adapter": PodcastShowAdapter(show),
            "music_wrapper": podcast_wrapper,
        }

        html = render_to_string(
            "app/components/podcast_episode_list.html",
            {
                "episodes": [episode_data],
                "user": request.user,
                "show": show,
                "IMG_NONE": settings.IMG_NONE,
                "TRACK_TIME": True,
                "has_more": False,
                "show_id": show.id,
            },
            request=request,
        )
        response = HttpResponse(html)
        response["HX-Trigger"] = "closeModal"
        return response

    return redirect(
        "media_details",
        source=show.source,
        media_type=MediaTypes.PODCAST.value,
        media_id=show.podcast_uuid,
        title=show.slug or app_tags.slug(show.title or "") or "podcast",
    )
