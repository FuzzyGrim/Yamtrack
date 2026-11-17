import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext, ngettext
from django.views.decorators.http import require_GET, require_POST

from app import fork_services_music, helpers, image_cache
from app.discover import tab_cache as discover_tab_cache
from app.forms import AlbumTrackerForm
from app.models import (
    Album,
    AlbumTracker,
    Artist,
    CollectionEntry,
    MediaTypes,
    Music,
    MusicReleasePreference,
    Track,
)
from app.music_views import (
    _build_music_album_activity_subtitle,
    _music_activity_date_range,
    _music_album_detail_url,
    _render_music_tracker_modal,
    music_play_summaries,
    music_track_play_fields,
)
from app.providers import musicbrainz
from app.services import bulk_music_tracking
from app.services.music import ensure_album_has_release_id, sync_album_release_tracks
from app.track_modal_views import _track_modal_release_date_shortcut

logger = logging.getLogger(__name__)


def _music_release_redirect(request, album, return_url):
    """Redirect to a safe caller URL or the canonical album page."""
    if return_url and url_has_allowed_host_and_scheme(
        return_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(return_url)
    return redirect(_music_album_detail_url(album))


def _music_release_search_text(release):
    """Return searchable text for one normalized MusicBrainz release."""
    return " ".join(
        str(release.get(field) or "")
        for field in (
            "title",
            "release_date",
            "country",
            "status",
            "packaging",
            "format",
            "label",
            "catalog_numbers",
            "barcode",
            "track_count",
        )
    ).casefold()


@login_required
@require_GET
def list_music_releases(request, album_id):
    """List MusicBrainz releases for an album's release group."""
    album = get_object_or_404(Album, id=album_id)
    query = (request.GET.get("q") or "").strip().casefold()
    releases = []
    if album.musicbrainz_release_group_id:
        try:
            releases = musicbrainz.get_release_group_releases(
                album.musicbrainz_release_group_id,
            )
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            logger.warning(
                "Failed to list releases for album %s: %s",
                album.id,
                exc,
            )

    if query:
        releases = [
            release
            for release in releases
            if query in _music_release_search_text(release)
        ]

    preference = MusicReleasePreference.objects.filter(
        user=request.user,
        album=album,
    ).first()
    if request.GET.get("format") == "json":
        return JsonResponse({
            "pickerType": "music",
            "results": [
                {**release, "image": image_cache.rewrite_image_url(release.get("image"))}
                for release in releases
            ],
        })
    return render(
        request,
        "app/components/hardcover_edition_results.html",
        {
            "picker_type": "music",
            "editions": releases,
            "query": query,
            "selection_url": reverse(
                "set_music_release",
                kwargs={"album_id": album.id},
            ),
            "selected_release_id": preference.release_id if preference else None,
            "return_url": helpers.normalize_navigation_url(
                request.GET.get("return_url"),
            ),
        },
    )


@login_required
@require_POST
def set_music_release(request, album_id):
    """Persist a validated per-user MusicBrainz release choice."""
    album = get_object_or_404(Album, id=album_id)
    return_url = helpers.normalize_navigation_url(request.POST.get("return_url"))
    release_id = (request.POST.get("release_id") or "").strip()

    if not release_id:
        messages.error(request, gettext("Select a release to use."))
    elif not album.musicbrainz_release_group_id:
        messages.error(request, gettext("This album has no MusicBrainz release group."))
    else:
        try:
            releases = musicbrainz.get_release_group_releases(
                album.musicbrainz_release_group_id,
            )
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            logger.warning(
                "Failed to validate release %s for album %s: %s",
                release_id,
                album.id,
                exc,
            )
            releases = []

        if not any(release.get("release_id") == release_id for release in releases):
            messages.error(request, gettext("That release is not part of this album."))
        else:
            MusicReleasePreference.objects.update_or_create(
                user=request.user,
                album=album,
                defaults={"release_id": release_id},
            )
            try:
                track_count = sync_album_release_tracks(album, release_id)
            except Exception as exc:  # pragma: no cover - defensive provider boundary
                logger.warning(
                    "Failed to sync release %s for album %s: %s",
                    release_id,
                    album.id,
                    exc,
                )
                messages.warning(
                    request,
                    gettext(
                        "Saved your release choice, but couldn't refresh the tracklist from MusicBrainz."
                    ),
                )
            else:
                messages.success(
                    request,
                    gettext("Release updated - synced %(value_1)s tracks.")
                    % {"value_1": track_count},
                )

    return _music_release_redirect(request, album, return_url)


def album_track_modal(request, album_id):
    """Return the shared tracking form modal for a music album."""
    album = get_object_or_404(Album, id=album_id)
    tracker = AlbumTracker.objects.filter(user=request.user, album=album).first()
    form = AlbumTrackerForm(
        instance=tracker,
        initial={"album_id": album.id},
        user=request.user,
    )
    return _render_music_tracker_modal(
        request,
        title=album.title,
        tracker=tracker,
        form=form,
        album=album,
        save_url=reverse("album_save"),
        delete_url=reverse("album_delete"),
        release_date_shortcut=_track_modal_release_date_shortcut(album.release_date),
        bulk_domain=bulk_music_tracking.build_album_play_domain(request.user, album),
    )


@require_POST
def album_save(request):
    """Save an album tracker - mirrors artist_save."""
    album_id = request.POST.get("album_id")
    album = get_object_or_404(Album, id=album_id)
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.MUSIC.value,
    )
    home_row_id = request.GET.get("home_row_id") or request.POST.get("home_row_id", "")

    tracker = AlbumTracker.objects.filter(user=request.user, album=album).first()
    old_status = getattr(tracker, "status", None)

    form = AlbumTrackerForm(request.POST, instance=tracker, user=request.user)
    if form.is_valid():
        tracker = form.save(commit=False)
        tracker.user = request.user
        tracker.album = album
        tracker.save()
        messages.success(
            request, gettext("Saved %(value_1)s") % {"value_1": album.title}
        )

        if request.headers.get("HX-Request"):
            htmx_trigger = {
                "closeModal": {},
                "showToast": {
                    "message": f"Saved {album.title}.",
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
            % {"value_1": album.title, "value_2": form.errors},
        )

    return _music_release_redirect(request, album, request.GET.get("next", ""))


@require_POST
def album_delete(request):
    """Delete an album tracker - mirrors artist_delete."""
    album_id = request.POST.get("album_id")
    album = get_object_or_404(Album, id=album_id)
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.MUSIC.value,
    )

    tracker = AlbumTracker.objects.filter(user=request.user, album=album).first()
    if tracker:
        tracker.delete()
        messages.success(
            request,
            gettext("Removed %(value_1)s from your library") % {"value_1": album.title},
        )

    return _music_release_redirect(request, album, request.GET.get("next", ""))


@require_POST
def song_save(request):
    """Handle adding a listen for a song - mirrors episode_save for episodes."""
    recording_id = request.POST.get("recording_id")
    album_id = request.POST.get("album_id")
    track_id = request.POST.get("track_id")
    end_date_str = request.POST.get("end_date")
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.MUSIC.value,
    )

    album = get_object_or_404(Album, id=album_id)
    track = get_object_or_404(Track, id=track_id) if track_id else None

    # A blank date means "now" and is resolved in the service. A date that was
    # supplied but cannot be read is a different case: recording it as "now"
    # would store a time the user did not choose and never tell them.
    end_date = None
    if end_date_str:
        try:
            end_date = helpers.parse_completion_datetime(end_date_str)
        except (TypeError, ValueError):
            message = gettext(
                'Floppy cannot read the date "%(value_1)s". Enter the date as YYYY-MM-DD. To record this listen at the current time, keep the date empty.'
            ) % {"value_1": end_date_str}
            if request.headers.get("HX-Request"):
                # Do not swap and do not close the modal. The date the user
                # typed stays on screen for them to correct.
                response = HttpResponse(status=200)
                response["HX-Reswap"] = "none"
                response["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": message, "type": "error"}},
                )
                return response
            messages.error(request, message)
            # Return to the album so the invalid listen can be entered again.
            return redirect(_music_album_detail_url(album))

    existed_before = Music.objects.filter(
        user=request.user,
        album=album,
        track=track,
    ).exists()

    # FORK: play-recording core shared with the REST API.
    fork_services_music.record_song_play(
        request.user,
        album,
        track=track,
        recording_id=recording_id,
        end_date=end_date,
    )

    if existed_before:
        messages.success(
            request,
            gettext("Added listen for %(value_1)s")
            % {"value_1": track.title if track else gettext("track")},
        )
    else:
        messages.success(
            request,
            gettext("Added %(value_1)s to your library")
            % {"value_1": track.title if track else gettext("track")},
        )

    if request.headers.get("HX-Request"):
        music = (
            Music.objects.filter(
                user=request.user,
                album=album,
                track=track,
            )
            .select_related("item", "track", "album")
            .order_by("-created_at")
            .first()
        )
        if music is None:
            return HttpResponse("Music entry not found", status=404)

        track_data = {
            "track": track,
            "music": music,
            **music_track_play_fields(music_play_summaries([music.id]).get(music.id)),
            "collection_entry": CollectionEntry.objects.filter(
                user=request.user,
                item=music.item,
            )
            .select_related("item")
            .first(),
        }
        user_music_entries = list(
            Music.objects.filter(
                user=request.user,
                album=album,
            ).select_related("item", "track"),
        )
        album_tracker = AlbumTracker.objects.filter(
            user=request.user, album=album
        ).first()
        first_listened, last_listened, collapse_same_day = _music_activity_date_range(
            user_music_entries,
        )
        music_album_activity_subtitle = _build_music_album_activity_subtitle(
            album,
            album_tracker,
            len(user_music_entries),
            Track.objects.filter(album=album).count(),
            first_listened,
            last_listened,
            collapse_same_day,
        )

        response = HttpResponse()
        response.write(
            render_to_string(
                "app/components/detail_music_track_button.html",
                {
                    "track_data": track_data,
                    "track_button_oob": True,
                },
                request=request,
            ),
        )
        response.write(
            render_to_string(
                "app/components/detail_music_track_history_line.html",
                {
                    "track_data": track_data,
                    "history_oob": True,
                    "user": request.user,
                },
                request=request,
            ),
        )
        if music_album_activity_subtitle:
            response.write(
                render_to_string(
                    "app/components/detail_music_album_activity_subtitle.html",
                    {
                        "album": album,
                        "music_album_activity_subtitle": music_album_activity_subtitle,
                        "subtitle_oob": True,
                        "user": request.user,
                    },
                    request=request,
                ),
            )
        response.write(
            render_to_string(
                "app/components/detail_music_track_actions.html",
                {
                    "track_data": track_data,
                    "track_actions_oob": True,
                    "user": request.user,
                },
                request=request,
            ),
        )
        response["HX-Trigger"] = json.dumps(
            {
                "closeModal": {},
                "showToast": {
                    "message": f"Added listen for {track.title if track else 'track'}.",
                    "type": "success",
                },
            },
        )
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response

    return _music_release_redirect(request, album, request.GET.get("next", ""))


@require_POST
def delete_all_album_plays_view(request, album_id):
    """Delete all music plays (listens) for an album."""
    album = get_object_or_404(Album, id=album_id)

    music_entries = Music.objects.filter(
        user=request.user,
        album=album,
    )

    count = music_entries.count()
    if count > 0:
        music_entries.delete()
        messages.success(
            request,
            ngettext(
                "Deleted %(count)s play for %(title)s",
                "Deleted %(count)s plays for %(title)s",
                count,
            )
            % {"count": count, "title": album.title},
        )
    else:
        messages.info(
            request,
            gettext("No plays found for %(value_1)s") % {"value_1": album.title},
        )

    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


@require_POST
def delete_all_artist_plays_view(request, artist_id):
    """Delete all music plays (listens) for an artist."""
    artist = get_object_or_404(Artist, id=artist_id)

    music_entries = Music.objects.filter(
        user=request.user,
        album__artist=artist,
    )

    count = music_entries.count()
    if count > 0:
        music_entries.delete()
        messages.success(
            request,
            ngettext(
                "Deleted %(count)s play for %(title)s",
                "Deleted %(count)s plays for %(title)s",
                count,
            )
            % {"count": count, "title": artist.name},
        )
    else:
        messages.info(
            request,
            gettext("No plays found for %(value_1)s") % {"value_1": artist.name},
        )

    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


@require_POST
def sync_album_metadata_view(request, album_id):
    """Manually trigger metadata sync for an album."""
    from app.services.music import sync_album_metadata

    album = get_object_or_404(Album, id=album_id)
    result = sync_album_metadata(
        album,
        get_release=musicbrainz.get_release,
        ensure_release_id=ensure_album_has_release_id,
    )

    if result["merged_into"] is not None:
        messages.success(
            request,
            gettext("Merged duplicate into %(value_1)s")
            % {"value_1": result["album"].title},
        )
        # Use HX-Redirect so HTMX performs a full browser navigation
        # (plain redirect() returns 302, which HTMX follows internally
        # but doesn't update the browser URL or stop the spinner).
        response = HttpResponse(status=204)
        response["HX-Redirect"] = _music_album_detail_url(result["album"])
        return response
    if result["missing_release"]:
        messages.warning(
            request, gettext("Could not find a MusicBrainz release for this album")
        )
    elif result["error"]:
        messages.error(
            request,
            gettext("Failed to sync album: %(value_1)s") % {"value_1": result["error"]},
        )
    else:
        messages.success(
            request,
            gettext("Synced %(value_1)s tracks for %(value_2)s")
            % {
                "value_1": result["synced_tracks"],
                "value_2": result["album"].title,
            },
        )

    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response
