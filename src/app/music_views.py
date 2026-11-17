import json
import logging
from urllib.parse import urlparse
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import IntegrityError, models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext, gettext_noop
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app.discover import tab_cache as discover_tab_cache
from app.forms import BulkEpisodeTrackForm, MusicTrackNotesForm
from app.log_safety import exception_summary
from app.models import (
    Album,
    AlbumTracker,
    Artist,
    CollectionEntry,
    Item,
    MediaTypes,
    Music,
    Sources,
    Track,
)
from app.services import bulk_music_tracking
from app.services import music as sync_services
from app.tag_views import _build_detail_tag_sections
from app.templatetags import app_tags

logger = logging.getLogger(__name__)

MINUTES_PER_HOUR = 60

# How often viewing an artist may call MusicBrainz on the viewer's time: an
# MBID search when the artist has none, and a discography sync.
ARTIST_VIEW_MBID_RESOLVE_SECONDS = 24 * 60 * 60
ARTIST_VIEW_SYNC_SECONDS = 60 * 60
# The artist page's cover and member-photo pollers run every 5 seconds; this
# bounds each to about two minutes per page view.
ARTIST_IMAGE_POLL_MAX_ATTEMPTS = 24

# Lengths of the partial-date strings MusicBrainz can return for a release
# date: "YYYY", "YYYY-MM", or a full "YYYY-MM-DD" (10+ chars).
DATE_STR_LEN_YEAR_ONLY = 4
DATE_STR_LEN_YEAR_MONTH = 7
DATE_STR_LEN_FULL_DATE = 10


def _play_counts_by_music_id(music_entries):
    """Return each album track's play count in one grouped history query."""
    return dict(
        Music.history.model.objects.filter(
            id__in=[music.id for music in music_entries if music.album_id],
        )
        .order_by()
        .values("id")
        .annotate(play_count=models.Count("history_id"))
        .values_list("id", "play_count"),
    )


def music_play_summaries(music_ids):
    """Return ``{music_id: (play_count, last_end_date)}`` in one grouped query.

    A track row shows how many times it was played and when last; reading
    each entry's full history for that cost one query per played track.
    """
    if not music_ids:
        return {}
    return {
        music_id: (play_count, last_end_date)
        for music_id, play_count, last_end_date in Music.history.model.objects.filter(
            id__in=list(music_ids),
        )
        .order_by()
        .values("id")
        .annotate(
            play_count=models.Count("history_id"),
            last_end_date=models.Max("end_date"),
        )
        .values_list("id", "play_count", "last_end_date")
    }


def music_track_play_fields(summary):
    """Return the track row's play fields from a ``music_play_summaries`` value."""
    play_count, last_end_date = summary or (0, None)
    return {"play_count": play_count, "last_played": last_end_date}


def _poll_attempt(request):
    """Return the poll attempt number an HTMX poller sent back."""
    try:
        return max(int(request.GET.get("attempt", 0)), 0)
    except (TypeError, ValueError):
        return 0


def _music_artist_detail_url(artist):
    """Return the canonical shared media-details URL for a music artist."""
    return app_tags.music_artist_url(artist)


def _music_album_detail_url(album):
    """Return the canonical shared media-details URL for a music album."""
    return app_tags.music_album_url(album)


def _music_track_detail_url(track):
    """Return the canonical shared media-details URL for a music track."""
    return app_tags.music_track_url(track)


def _nonempty_genre_names(genres):
    """Return genre labels that are not blank."""
    if not genres:
        return []
    names = []
    for genre in genres:
        text = str(genre).strip()
        if text:
            names.append(text)
    return names


def _track_page_genres(track):
    """Return track genres, or the album's genres when the track has none."""
    track_genres = _nonempty_genre_names(track.genres)
    if track_genres:
        return track_genres
    return _nonempty_genre_names(getattr(track.album, "genres", None))


def _safe_origin_url(value):
    """Return an http(s) origin URL, or an empty string."""
    text = (value or "").strip()
    if text.lower().startswith(("https://", "http://")):
        return text
    return ""


def _music_entry_for_track(user, track):
    """Resolve this user's Music row the same way the album track list does."""
    from app import fork_services_music

    return fork_services_music.music_entry_for_track(user, track)


def _lookup_text(value):
    """Return a comparable lowercase form of a title or artist."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _search_recording_id(track):
    """Return a MusicBrainz recording id from an artist and title search."""
    from app.providers import musicbrainz

    artist = track.album.artist
    artist_name = artist.name if artist else ""
    query = " ".join(part for part in (artist_name, track.title) if part).strip()
    if not query:
        return ""

    results = musicbrainz.search(query, page=1, skip_cover_art=True)
    expected_title = _lookup_text(track.title)
    for result in (results or {}).get("results") or []:
        if expected_title and expected_title in _lookup_text(result.get("title")):
            return result.get("media_id") or ""
    return ""


def _lookup_unlistened_recording(track):
    """Fetch MusicBrainz recording metadata when this user has no listen."""
    from app.providers import musicbrainz

    recording_id = (track.musicbrainz_recording_id or "").strip()
    try:
        if not recording_id:
            recording_id = _search_recording_id(track)
        if not recording_id:
            return None
        return musicbrainz.recording(recording_id)
    except Exception as exc:
        logger.debug(
            "Track %s recording lookup failed: %s",
            track.id,
            exception_summary(exc),
        )
        return None


def _recording_genre_names(recording):
    """Return title-cased genre labels from a MusicBrainz recording payload."""
    return [
        name.title() for name in _nonempty_genre_names((recording or {}).get("genres"))
    ]


def _recording_display(track, recording):
    """Return the recording fields the track page does not already show.

    Artist and album stay in the header. Ids and the minute-rounded duration
    stay off the page. Runtime is included only when the track has no duration.
    """
    if not recording:
        return {"release_date": "", "runtime": "", "image": ""}

    details = recording.get("details") or {}
    image = recording.get("image") or ""
    if image == settings.IMG_NONE:
        image = ""
    runtime = ""
    if details.get("runtime") and not track.duration_formatted:
        runtime = details["runtime"]
    return {
        "release_date": details.get("release_date") or "",
        "runtime": runtime,
        "image": image,
    }


def _track_play_history(music_entry):
    """Return completed listens for a Music row, newest first."""
    if music_entry is None:
        return []
    history = []
    rows = music_entry.history.all().order_by("-end_date")
    for row in rows:
        if not row.end_date:
            continue
        history.append(
            {
                "end_date": row.end_date,
                "origin_url": _safe_origin_url(getattr(row, "origin_url", "")),
            },
        )
    return history


def _album_display_genres(album):
    """Return album genres, or the artist's when the album has none."""
    if album is None:
        return []
    return sync_services._music_item_direct_genres(album)


def _play_link_label(url):
    """Return the play-link label for a SoundCloud or Spotify URL."""
    host = (urlparse(url or "").hostname or "").lower()
    if host == "soundcloud.com" or host.endswith(".soundcloud.com"):
        return "SoundCloud"
    if host == "spotify.com" or host.endswith(".spotify.com"):
        return "Spotify"
    return ""


def _external_play_links(tracks_with_data):
    """Return one play chip per distinct SoundCloud or Spotify URL on this album."""
    links = {}
    seen_urls = set()
    for track_data in tracks_with_data:
        url = track_data.get("origin_url") or ""
        label = _play_link_label(url)
        if not label or url in seen_urls:
            continue
        seen_urls.add(url)
        if label in links:
            label = track_data["track"].title or url
        base_label, suffix = label, 2
        while label in links:
            label = f"{base_label} ({suffix})"
            suffix += 1
        links[label] = url
    return links


def _selected_music_release(user, album):
    """Return the user's valid release preference and its detailed metadata."""
    from app.models import MusicReleasePreference
    from app.providers import musicbrainz

    preference = MusicReleasePreference.objects.filter(
        user=user,
        album=album,
    ).first()
    selected_release = None
    if preference and album.musicbrainz_release_group_id:
        try:
            candidate_release = musicbrainz.get_release(preference.release_id)
            if (
                candidate_release.get("release_group_id")
                == album.musicbrainz_release_group_id
            ):
                selected_release = candidate_release
            else:
                logger.warning(
                    "Saved release %s does not belong to album %s release group",
                    preference.release_id,
                    album.id,
                )
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            logger.warning(
                "Failed to load saved release %s for album %s: %s",
                preference.release_id,
                album.id,
                exc,
            )
    return preference, selected_release


def _music_activity_date_range(entries):
    """Return the earliest and latest meaningful activity dates from music entries."""
    start_candidates = []
    end_candidates = []
    for entry in entries:
        if entry.start_date or entry.end_date:
            start_candidates.append(entry.start_date or entry.end_date)
            end_candidates.append(entry.end_date or entry.start_date)

    first_date = min(start_candidates) if start_candidates else None
    last_date = max(end_candidates) if end_candidates else None
    collapse_same_day = bool(
        first_date and last_date and first_date.date() == last_date.date()
    )
    return first_date, last_date, collapse_same_day


def _build_music_artist_activity_subtitle(
    artist_tracker,
    total_plays,
    first_date,
    last_date,
    collapse_same_day,
):
    """Return the shared subtitle payload for a music artist detail page."""
    if not artist_tracker and not total_plays and not first_date and not last_date:
        return None

    primary_text = None
    if total_plays:
        primary_text = (
            "Played once" if total_plays == 1 else f"Played {total_plays} times"
        )

    return {
        "primary_text": primary_text,
        "date_start": first_date or getattr(artist_tracker, "start_date", None),
        "date_end": last_date or getattr(artist_tracker, "end_date", None),
        "collapse_same_day": collapse_same_day,
    }


def _build_music_album_activity_subtitle(
    album,
    album_tracker,
    library_track_count,
    total_tracks,
    first_date,
    last_date,
    collapse_same_day,
):
    """Return the shared subtitle payload for a music album detail page."""
    if (
        not album
        and not album_tracker
        and not total_tracks
        and not first_date
        and not last_date
    ):
        return None

    progress_text = None
    if total_tracks:
        progress_text = f"Progress: {library_track_count}/{total_tracks}"

    return {
        "title": album.title if album else "",
        "progress_text": progress_text,
        "date_start": first_date or getattr(album_tracker, "start_date", None),
        "date_end": last_date or getattr(album_tracker, "end_date", None),
        "collapse_same_day": collapse_same_day,
    }


def _build_music_detail_secondary_actions(
    *,
    history_url,
    sync_url,
    sync_title,
    delete_url,
    delete_title,
    delete_confirm,
):
    """Return shared secondary action metadata for music detail pages."""
    return [
        {
            "kind": "link",
            "title": "View your activity history",
            "url": history_url,
            "icon": "history",
        },
        {
            "kind": "button",
            "title": sync_title,
            "hx_post": sync_url,
            "icon": "circle-spinning-clockwise",
        },
        {
            "kind": "button",
            "title": delete_title,
            "hx_post": delete_url,
            "icon": "trashcan",
            "confirm": delete_confirm,
            "button_classes": "border-red-500/20 bg-red-500/10 text-red-100 hover:bg-red-500/20",
        },
    ]


def _render_music_tracker_modal(
    request,
    *,
    title,
    tracker,
    form,
    album=None,
    save_url,
    delete_url,
    release_date_shortcut="",
    bulk_domain=None,
    bulk_form_override=None,
    initial_active_tab="general",
):
    """Render a non-item music tracker modal through the shared shell."""
    from app import views as view_barrel

    return_url = request.GET.get("return_url") or request.POST.get("return_url", "")
    home_row_id = (
        request.GET.get("home_row_id") or request.POST.get("home_row_id") or ""
    )
    track_form_id = f"track-form-{uuid4().hex}"
    field_groups = view_barrel._track_modal_field_groups(
        form,
        hidden_field_names={
            field_name for field_name in form.fields if field_name.endswith("_id")
        },
        metadata_field_names=set(),
    )
    episode_plays_tab_available = bool(bulk_domain)
    if episode_plays_tab_available:
        if bulk_form_override is not None:
            episode_plays_form = bulk_form_override
        else:
            bulk_initial = view_barrel._bulk_episode_form_initial_data(
                return_url,
                bulk_domain,
            )
            bulk_initial["instance_id"] = tracker.id if tracker else ""
            episode_plays_form = BulkEpisodeTrackForm(
                initial=bulk_initial,
                domain=bulk_domain,
            )
        episode_plays_tab_label = bulk_domain.get("tab_label") or gettext("Track Plays")
        episode_plays_submit_label = bulk_domain.get("submit_label") or "Save plays"
    else:
        episode_plays_form = None
        episode_plays_tab_label = gettext("Track Plays")
        episode_plays_submit_label = "Save plays"

    music_release_preference = None
    selected_music_release = None
    music_release_picker = {"available": False, "list_url": ""}
    if album is not None:
        music_release_preference, selected_music_release = _selected_music_release(
            request.user,
            album,
        )
        music_release_picker = {
            "available": bool(album.musicbrainz_release_group_id),
            "list_url": reverse(
                "list_music_releases",
                kwargs={"album_id": album.id},
            ),
            "selection_url": reverse(
                "set_music_release",
                kwargs={"album_id": album.id},
            ),
        }

    date_suggestion = view_barrel._track_modal_date_suggestion(
        "Release Date",
        release_date_shortcut,
    )
    context = {
        "user": request.user,
        "title": title,
        "media_type": MediaTypes.MUSIC.value,
        "form": form,
        "media": tracker,
        "return_url": return_url,
        "metadata_tab_available": music_release_picker["available"],
        "metadata_fields": [],
        "can_manage_hardcover_edition": False,
        "music_release_preference": music_release_preference,
        "selected_music_release": selected_music_release,
        "music_release_picker": music_release_picker,
        "general_hidden_fields": field_groups["hidden_fields"],
        "general_fields": field_groups["general_fields"],
        "general_submit_formaction": (
            f"{save_url}?next={return_url}"
            + (f"&home_row_id={home_row_id}" if home_row_id else "")
        ),
        "general_delete_formaction": f"{delete_url}?next={return_url}",
        "general_existing_instance": tracker,
        "image_field": None,
        "image_save_item_id": None,
        "date_suggestion": date_suggestion,
        "track_form_id": track_form_id,
        "initial_active_tab": initial_active_tab,
        "episode_plays_tab_available": episode_plays_tab_available,
        "episode_plays_form": episode_plays_form,
        "episode_plays_formaction": reverse("music_bulk_save"),
        "episode_plays_tab_label": episode_plays_tab_label,
        "episode_plays_submit_label": episode_plays_submit_label,
        "episode_plays_domain": view_barrel._episode_domain_template_payload(
            bulk_domain,
        ),
        "episode_plays_mode_notice": (
            bulk_domain.get("mode_notice", "") if bulk_domain else ""
        ),
        "episode_plays_domain_script_id": f"{track_form_id}-episode-domain",
    }
    context["track_form_props_id"] = f"{track_form_id}-shared-form"
    presentation = view_barrel._track_form_presentation(
        request,
        form,
        track_form_id=track_form_id,
        title=title,
        title_subtitle="",
        media_type=MediaTypes.MUSIC.value,
        media=tracker,
        action=context["general_submit_formaction"],
        delete_action=context["general_delete_formaction"],
        hidden_fields=field_groups["hidden_fields"],
        general_fields=field_groups["general_fields"],
        rewatch_action=None,
        rewatch_url="",
        save_as_new=False,
        date_suggestion=date_suggestion,
    )
    presentation["tabs"] = view_barrel._track_tabs_presentation(context)
    presentation["model"]["labels"].update(
        view_barrel._track_tab_labels(context),
    )
    context["track_form_presentation"] = presentation
    response = render(
        request,
        "app/components/fill_track.html",
        context,
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def _music_bulk_redirect_url(request, *, artist=None, album=None):
    """Return the destination after a successful music bulk-play save."""
    next_url = request.GET.get("next") or request.POST.get("return_url", "")
    if next_url:
        return next_url
    if album is not None:
        return _music_album_detail_url(album)
    if artist is not None:
        return _music_artist_detail_url(artist)
    return reverse("medialist", args=[MediaTypes.MUSIC.value])


def _build_artist_relations(user, artist):
    """Return normalized band-member / also-in relation lists for an artist.

    Each relation is a dict with `artist`, `role`, `is_current`, and `tracker`
    (the viewing user's ArtistTracker for that related artist, or None) so a
    single card template can render both "Members" and "Also In" sections.
    """
    from app.models import ArtistTracker

    band_memberships = list(
        artist.members.select_related("member").order_by("-is_current", "member__name"),
    )
    band_of_memberships = list(
        artist.bands.select_related("band").order_by("-is_current", "band__name"),
    )

    related_artists = {membership.member for membership in band_memberships}
    related_artists.update(membership.band for membership in band_of_memberships)
    related_artist_ids = [related_artist.id for related_artist in related_artists]

    trackers_by_artist_id = {}
    if related_artist_ids:
        trackers_by_artist_id = {
            tracker.artist_id: tracker
            for tracker in ArtistTracker.objects.filter(
                user=user,
                artist_id__in=related_artist_ids,
            )
        }

    def _to_relations(memberships, attr):
        relations = []
        for membership in memberships:
            related_artist = getattr(membership, attr)
            relations.append(
                {
                    "artist": related_artist,
                    "role": membership.role,
                    "is_current": membership.is_current,
                    "tracker": trackers_by_artist_id.get(related_artist.id),
                }
            )
        return relations

    band_members = _to_relations(band_memberships, "member")
    member_of_bands = _to_relations(band_of_memberships, "band")

    missing_relation_image_count = sum(
        1 for related_artist in related_artists if not related_artist.image
    )

    return band_members, member_of_bands, missing_relation_image_count


def _queue_artist_relation_image_prefetch(artist, band_members, member_of_bands):
    """Debounced enqueue of the async photo backfill for missing relation images."""
    from app.tasks import prefetch_artist_images_batch

    missing_ids = [
        relation["artist"].id
        for relation in band_members + member_of_bands
        if not relation["artist"].image
    ]
    if not missing_ids:
        return

    cache_key = f"music:artist-image-prefetch:{artist.id}"
    try:
        if cache.add(cache_key, True, 60 * 10):
            try:
                prefetch_artist_images_batch.delay(missing_ids)
            except Exception:  # pragma: no cover - defensive
                cache.delete(cache_key)
                raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Artist relation image prefetch queue failed for artist %s: %s",
            artist.id,
            exception_summary(exc),
        )


def _render_music_artist_details(request, artist):
    """Render a music artist through the shared media details template."""
    from app import views as view_barrel
    from app.helpers import get_artist_collection_stats
    from app.models import AlbumTracker, ArtistTracker
    from app.providers import musicbrainz
    from app.services.music import (
        build_discography_groups,
        canonicalize_albums,
        needs_discography_sync,
        sync_artist_discography,
        sync_artist_members,
    )
    from app.services.music_scrobble import dedupe_artist_albums

    # Resolving an MBID searches MusicBrainz under several name variants, each
    # a rate-limited call. An artist with no match would repeat that search on
    # every view, so a view tries at most once a day.
    if not artist.musicbrainz_id and cache.add(
        f"music:artist-mbid-resolve:{artist.id}",
        True,
        ARTIST_VIEW_MBID_RESOLVE_SECONDS,
    ):
        try:
            mbid, cand_count, variant = sync_services.resolve_artist_mbid(
                artist.name or "",
                artist.sort_name or "",
            )
            if mbid:
                try:
                    artist.musicbrainz_id = mbid
                    artist.discography_synced_at = None
                    artist.save(
                        update_fields=["musicbrainz_id", "discography_synced_at"],
                    )
                    logger.info(
                        "Attached MBID %s to artist %s on view via '%s' (candidates=%d)",
                        mbid,
                        artist.name,
                        variant,
                        cand_count,
                    )
                except IntegrityError:
                    from app.services.music import merge_artist_records

                    existing = Artist.objects.filter(musicbrainz_id=mbid).first()
                    if existing:
                        artist = merge_artist_records(artist, existing)
                        logger.info(
                            "Merged artist %s into existing MBID %s via '%s'",
                            artist.name,
                            mbid,
                            variant,
                        )
                        return redirect(_music_artist_detail_url(artist))
            else:
                logger.debug(
                    "No MBID attached on view for %s after searching variants (candidates=%d)",
                    artist.name,
                    cand_count,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "Artist MBID attach failed on view for %s: %s",
                artist.name,
                exception_summary(exc),
            )

    dedupe_artist_albums(artist)

    albums_qs = Album.objects.filter(
        models.Q(artist=artist) | models.Q(artist_credits__artist=artist),
    ).distinct()
    existing_album_count = albums_qs.count()
    missing_mbids = albums_qs.filter(
        musicbrainz_release_id__isnull=True,
        musicbrainz_release_group_id__isnull=True,
    ).exists()
    should_sync = (
        needs_discography_sync(artist, max_age_days=1)
        or existing_album_count == 0
        or missing_mbids
    )
    force_sync = existing_album_count == 0 or missing_mbids

    # A forced sync re-reads the whole discography from MusicBrainz. Albums the
    # provider cannot match keep ``missing_mbids`` true forever, so without a
    # gate every view of such an artist paid for a full sync. The page's sync
    # button still forces one on demand.
    synced_count = 0
    if (
        should_sync
        and artist.musicbrainz_id
        and cache.add(
            f"music:artist-view-sync:{artist.id}",
            True,
            ARTIST_VIEW_SYNC_SECONDS,
        )
    ):
        synced_count = sync_artist_discography(artist, force=force_sync)
        if synced_count:
            dedupe_artist_albums(artist)
    elif should_sync and not artist.musicbrainz_id:
        logger.debug(
            "Skipping discography sync for %s due to missing MBID",
            artist.name,
        )

    raw_albums = list(
        Album.objects.filter(
            models.Q(artist=artist) | models.Q(artist_credits__artist=artist),
        )
        .select_related("artist")
        .prefetch_related("artist_credits__artist")
        .distinct()
        .order_by("-release_date", "title"),
    )
    all_albums_by_id = {}
    for canonical_album in canonicalize_albums(raw_albums, user=request.user):
        all_albums_by_id[canonical_album.id] = canonical_album
    all_albums = list(all_albums_by_id.values())

    user_music_entries = list(
        Music.objects.filter(
            models.Q(album__artist=artist)
            | models.Q(album__artist_credits__artist=artist),
            user=request.user,
        ).select_related("album", "item"),
    )

    play_counts_by_music_id = _play_counts_by_music_id(user_music_entries)
    album_play_counts = {}
    total_plays = 0
    for music in user_music_entries:
        if music.album_id:
            play_count = play_counts_by_music_id.get(music.id, 0)
            album_play_counts[music.album_id] = (
                album_play_counts.get(music.album_id, 0) + play_count
            )
            total_plays += play_count

    album_trackers = AlbumTracker.objects.filter(
        user=request.user,
        album__in=all_albums,
    ).select_related("album")
    album_scores = {
        tracker.album_id: tracker.score
        for tracker in album_trackers
        if tracker.score is not None
    }

    for album in all_albums:
        album.play_count = album_play_counts.get(album.id, 0)
        album.score = album_scores.get(album.id)

    discography_groups = build_discography_groups(all_albums)
    missing_cover_count = sum(1 for album in all_albums if not album.image)

    artist_tracker = ArtistTracker.objects.filter(
        user=request.user,
        artist=artist,
    ).first()

    first_listened, last_listened, collapse_same_day = _music_activity_date_range(
        user_music_entries,
    )
    artist_activity_subtitle = _build_music_artist_activity_subtitle(
        artist_tracker,
        total_plays,
        first_listened,
        last_listened,
        collapse_same_day,
    )

    artist_metadata = {}
    genres = []
    tags = []
    mb_rating = None
    mb_rating_count = 0
    bio = ""

    if artist.musicbrainz_id:
        try:
            mb_data = musicbrainz.get_artist(artist.musicbrainz_id)
            artist_metadata = {
                "type": mb_data.get("type", ""),
                "country": mb_data.get("country", ""),
                "area": mb_data.get("area", ""),
                "begin_date": mb_data.get("begin_date", ""),
                "end_date": mb_data.get("end_date", ""),
                "ended": mb_data.get("ended", False),
                "disambiguation": mb_data.get("disambiguation", ""),
            }
            genres = mb_data.get("genres", [])
            tags = mb_data.get("tags", [])
            mb_rating = mb_data.get("rating")
            mb_rating_count = mb_data.get("rating_count", 0)
            bio = mb_data.get("bio", "")

            updated_fields = []
            if mb_data.get("type") and mb_data.get("type") != artist.artist_type:
                artist.artist_type = mb_data.get("type", "")
                updated_fields.append("artist_type")
            if mb_data.get("country") and mb_data.get("country") != artist.country:
                artist.country = mb_data.get("country", "")
                updated_fields.append("country")
            if mb_data.get("genres"):
                genre_names = [
                    g.get("name") for g in mb_data.get("genres") if g.get("name")
                ]
                if genre_names != artist.genres:
                    artist.genres = genre_names
                    updated_fields.append("genres")
            if updated_fields:
                artist.save(update_fields=updated_fields)

            wiki_image = mb_data.get("image")
            if wiki_image and (not artist.image or artist.image == settings.IMG_NONE):
                artist.image = wiki_image
                artist.save(update_fields=["image"])
        except Exception as exc:
            logger.debug("Failed to fetch artist metadata from MusicBrainz: %s", exc)

        try:
            sync_artist_members(artist)
        except Exception as exc:
            logger.debug("Failed to sync artist members from MusicBrainz: %s", exc)

    band_members, member_of_bands, missing_relation_image_count = (
        _build_artist_relations(
            request.user,
            artist,
        )
    )
    poll_for_relation_images = missing_relation_image_count > 0
    if poll_for_relation_images:
        _queue_artist_relation_image_prefetch(artist, band_members, member_of_bands)

    genre_chips = []
    if genres:
        genre_chips = [g["name"].title() for g in genres[:6]]
    elif tags:
        genre_chips = [t["name"].title() for t in tags[:6]]
    else:
        from app.providers import musicbrainz

        genre_chips = musicbrainz._normalize_musicbrainz_genre_names(artist.genres)

    collection_stats = get_artist_collection_stats(request.user, artist)
    notes_entry = artist_tracker if artist_tracker and artist_tracker.notes else None
    detail_link_sections = view_barrel._build_detail_link_sections(
        {
            "source_url": (
                f"https://musicbrainz.org/artist/{artist.musicbrainz_id}"
                if artist.musicbrainz_id
                else ""
            ),
        },
        MediaTypes.MUSIC.value,
        Sources.MUSICBRAINZ.value,
        Sources.MUSICBRAINZ.value,
    )
    detail_primary_action = {
        "label": artist_tracker.status_readable
        if artist_tracker
        else gettext_noop("Add to Library"),
        "modal_url": reverse("artist_track_modal", args=[artist.id]),
        "target_id": f"artist-track-modal-{artist.id}",
        "active": bool(artist_tracker),
    }
    detail_history_url = f"{reverse('history')}?artist={artist.id}"

    context = {
        "user": request.user,
        "music_detail_kind": "artist",
        "media_type": MediaTypes.MUSIC.value,
        "artist": artist,
        "media": {
            "media_type": MediaTypes.MUSIC.value,
            "source": Sources.MUSICBRAINZ.value,
            "media_id": artist.musicbrainz_id or f"artist-{artist.id}",
            "title": artist.name,
            "image": artist.image or settings.IMG_NONE,
            "synopsis": bio or artist_metadata.get("disambiguation", ""),
            "details": {},
            "related": {},
        },
        "artist_tracker": artist_tracker,
        "notes_entry": notes_entry,
        "detail_notes_modal_url": reverse("artist_track_modal", args=[artist.id]),
        "detail_notes_target_id": f"artist-track-modal-{artist.id}",
        "detail_history_url": detail_history_url,
        "detail_primary_action": detail_primary_action,
        "detail_secondary_actions": _build_music_detail_secondary_actions(
            history_url=detail_history_url,
            sync_url=reverse("sync_artist_discography", args=[artist.id]),
            sync_title="Sync discography from MusicBrainz",
            delete_url=reverse("delete_all_artist_plays", args=[artist.id]),
            delete_title="Delete all plays for this artist",
            delete_confirm="Are you sure you want to delete all plays for this artist? This cannot be undone.",
        ),
        "detail_collection_mode": "music_artist",
        "detail_link_sections": detail_link_sections,
        "discography_groups": discography_groups,
        "band_members": band_members,
        "member_of_bands": member_of_bands,
        "missing_relation_image_count": missing_relation_image_count,
        "poll_for_relation_images": poll_for_relation_images,
        "collection_stats": collection_stats,
        "music_artist_metadata": artist_metadata,
        "music_artist_rating": {
            "rating": mb_rating,
            "rating_count": mb_rating_count,
        },
        "music_artist_activity_subtitle": artist_activity_subtitle,
        "genre_chips": genre_chips,
        "total_plays": total_plays,
        "total_releases": len(all_albums),
        "missing_cover_count": missing_cover_count,
        "poll_for_covers": missing_cover_count > 0,
        "bio": bio,
    }
    return render(request, "app/media_details.html", context)


ALBUM_TRACK_PAGE_SIZE = 50


def _album_display_image(album, selected_release):
    """Return the chosen release's artwork, else the album's, else IMG_NONE."""
    image = (selected_release or {}).get("image")
    if image and image != settings.IMG_NONE:
        return image
    return album.image or settings.IMG_NONE


def album_track_rows(user, album, *, offset=0, limit=None):
    """Return ``(rows, user_music_entries)`` for ``album``'s tracks.

    Each row carries the track, the viewer's Music entry (matched by track id
    or recording id), its play count and latest play, collection entry and
    origin link. The cost is a fixed handful of queries whatever the album's
    length or how often its tracks were played. With ``limit``, only that
    slice of tracks (and the entries that can match them) is loaded, so a
    scrolled-in page costs the same on a 20-track album as on a box set.
    """
    # The album (and its artist) is already in hand; joining it per track row
    # re-read the same columns once per track.
    all_tracks = Track.objects.filter(album=album).order_by(
        "disc_number",
        "track_number",
        "title",
    )
    entries = Music.objects.filter(user=user, album=album).select_related("item")
    if limit is not None:
        all_tracks = list(all_tracks[offset : offset + limit])
        recording_ids = [
            track.musicbrainz_recording_id
            for track in all_tracks
            if track.musicbrainz_recording_id
        ]
        entries = entries.filter(
            models.Q(track_id__in=[track.id for track in all_tracks])
            | models.Q(item__media_id__in=recording_ids),
        )
    user_music_entries = list(entries)

    user_music_by_track = {}
    for music in user_music_entries:
        if music.track_id:
            user_music_by_track[music.track_id] = music
        if music.item and music.item.media_id:
            user_music_by_track[f"recording_{music.item.media_id}"] = music

    collection_entries_by_item_id = {}
    music_item_ids = [music.item_id for music in user_music_entries if music.item_id]
    if music_item_ids:
        collection_entries = CollectionEntry.objects.filter(
            user=user,
            item_id__in=music_item_ids,
        ).order_by("-collected_at", "-id")
        for collection_entry in collection_entries:
            collection_entries_by_item_id.setdefault(
                collection_entry.item_id,
                collection_entry,
            )

    play_summaries = music_play_summaries(music.id for music in user_music_entries)

    tracks_with_data = []
    for track in all_tracks:
        track.album = album
        music_entry = user_music_by_track.get(track.id)
        if not music_entry and track.musicbrainz_recording_id:
            music_entry = user_music_by_track.get(
                f"recording_{track.musicbrainz_recording_id}",
            )

        collection_entry = None
        if music_entry and music_entry.item_id:
            collection_entry = collection_entries_by_item_id.get(music_entry.item_id)

        origin_url = _safe_origin_url(
            getattr(music_entry, "origin_url", "") if music_entry else "",
        )
        tracks_with_data.append(
            {
                "track": track,
                "music": music_entry,
                "origin_url": origin_url,
                "origin_label": _play_link_label(origin_url),
                **music_track_play_fields(
                    play_summaries.get(music_entry.id) if music_entry else None,
                ),
                "collection_entry": collection_entry,
            },
        )

    return tracks_with_data, user_music_entries


def _render_music_album_details(request, artist, album):
    """Render a music album through the shared media details template."""
    from app import views as view_barrel
    from app.helpers import get_album_collection_metadata
    from app.models import AlbumTracker
    from app.providers import musicbrainz
    from app.services.music import (
        album_has_musicbrainz_id,
        ensure_album_has_release_id,
        sync_artist_discography,
    )
    from app.services.music_scrobble import (
        _choose_primary_album,
        _normalize,
        dedupe_artist_albums,
        is_incomplete_album,
    )

    original_artist = album.artist
    original_title = album.title
    original_norm = _normalize(original_title)

    if album.artist_id and artist and album.artist_id != artist.id:
        return redirect(_music_album_detail_url(album))

    if original_artist:
        dedupe_artist_albums(original_artist)
        try:
            album.refresh_from_db()
        except Album.DoesNotExist:
            replacement = (
                Album.objects.filter(
                    artist=original_artist,
                    title__iexact=original_title,
                )
                .order_by("id")
                .first()
            )
            if not replacement:
                for cand in Album.objects.filter(artist=original_artist):
                    if _normalize(cand.title) == original_norm:
                        replacement = cand
                        break
            if replacement:
                return redirect(_music_album_detail_url(replacement))
            raise

        if is_incomplete_album(album) and original_artist.musicbrainz_id:
            try:
                sync_artist_discography(original_artist, force=True)
                dedupe_artist_albums(original_artist)
                candidates = [
                    candidate
                    for candidate in Album.objects.filter(artist=original_artist)
                    if _normalize(candidate.title) == original_norm
                ]
                if candidates:
                    best = _choose_primary_album(candidates, album)
                    if best.id != album.id:
                        return redirect(_music_album_detail_url(best))
                    album = best
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "Failed to heal album %s via discography: %s",
                    album.id,
                    exception_summary(exc),
                )

    if not album.musicbrainz_release_id and album.musicbrainz_release_group_id:
        ensure_album_has_release_id(album)

    has_mb_identity = album_has_musicbrainz_id(album)
    album_release_data = None
    if album.musicbrainz_release_id and sync_services.album_artist_credits_need_sync(
        album
    ):
        try:
            album_release_data = musicbrainz.get_release(album.musicbrainz_release_id)
            sync_services.sync_album_artist_credits(album, album_release_data)
        except Exception as exc:
            logger.warning(
                "Failed to sync artist credits for album %s: %s",
                album.title,
                exc,
            )

    album_metadata_updated = False
    if album.musicbrainz_release_id or album.musicbrainz_release_group_id:
        try:
            album_metadata_updated = sync_services.populate_album_implied_genres(album)
        except Exception as exc:
            logger.warning(
                "Failed to refresh album genres for %s: %s",
                album.title,
                exc,
            )

    if not album.tracks_populated and has_mb_identity:
        try:
            if album.musicbrainz_release_id:
                if album_release_data is not None:
                    sync_services.sync_album_artist_credits(album, album_release_data)
                sync_services.populate_album_tracks(album)
            else:
                logger.warning(
                    "Album %s has release_group but no release found for tracks",
                    album.title,
                )
        except Exception as exc:
            logger.warning(
                "Failed to populate tracks for album %s: %s",
                album.title,
                exc,
            )
    elif album_metadata_updated:
        album.refresh_from_db(fields=["genres", "implied_genres"])

    tracks_with_data, user_music_entries = album_track_rows(request.user, album)
    total_duration_ms = sum(
        track_data["track"].duration_ms or 0 for track_data in tracks_with_data
    )

    library_track_count = sum(
        1 for track_data in tracks_with_data if track_data["music"]
    )
    first_listened, last_listened, collapse_same_day = _music_activity_date_range(
        user_music_entries,
    )

    album_tracker = AlbumTracker.objects.filter(
        user=request.user,
        album=album,
    ).first()

    music_release_preference, selected_music_release = _selected_music_release(
        request.user,
        album,
    )

    album_display_image = _album_display_image(album, selected_music_release)

    total_runtime = None
    if total_duration_ms:
        total_minutes = total_duration_ms // 60000
        if total_minutes >= MINUTES_PER_HOUR:
            hours = total_minutes // 60
            minutes = total_minutes % 60
            total_runtime = f"{hours}h {minutes}m"
        else:
            total_runtime = f"{total_minutes}m"

    album_activity_subtitle = _build_music_album_activity_subtitle(
        album,
        album_tracker,
        library_track_count,
        len(tracks_with_data),
        first_listened,
        last_listened,
        collapse_same_day,
    )

    album_details = {
        "format": album.release_type or "Album",
        "release_date": album.release_date,
        "tracks": len(tracks_with_data),
        "runtime": total_runtime,
    }
    if album.musicbrainz_release_id:
        album_details["musicbrainz_id"] = album.musicbrainz_release_id
        album_details["musicbrainz_url"] = (
            f"https://musicbrainz.org/release/{album.musicbrainz_release_id}"
        )
    elif album.musicbrainz_release_group_id:
        album_details["musicbrainz_id"] = album.musicbrainz_release_group_id
        album_details["musicbrainz_url"] = (
            f"https://musicbrainz.org/release-group/{album.musicbrainz_release_group_id}"
        )

    collection_metadata = get_album_collection_metadata(request.user, album)
    notes_entry = album_tracker if album_tracker and album_tracker.notes else None
    detail_link_sections = view_barrel._build_detail_link_sections(
        {
            "source_url": album_details.get("musicbrainz_url", ""),
            "external_links": _external_play_links(tracks_with_data),
        },
        MediaTypes.MUSIC.value,
        Sources.MUSICBRAINZ.value,
        Sources.MUSICBRAINZ.value,
    )
    detail_primary_action = {
        "label": album_tracker.status_readable
        if album_tracker
        else gettext_noop("Add to Library"),
        "modal_url": reverse("album_track_modal", args=[album.id]),
        "target_id": f"album-track-modal-{album.id}",
        "active": bool(album_tracker),
    }
    detail_history_url = f"{reverse('history')}?album={album.id}"
    detail_item = Item.objects.filter(
        media_type=MediaTypes.MUSIC.value,
        source=Sources.MUSICBRAINZ.value,
        media_id=(
            album.musicbrainz_release_id
            or album.musicbrainz_release_group_id
            or f"album-{album.id}"
        ),
    ).first()
    album_genres = _album_display_genres(album)
    detail_tag_sections = _build_detail_tag_sections(
        {},
        detail_item,
        request.user,
        fallback_genres=album_genres,
        fallback_implied_genres=album.implied_genres,
        genre_list_media_type=MediaTypes.MUSIC.value,
    )

    context = {
        "user": request.user,
        "music_detail_kind": "album",
        "media_type": MediaTypes.MUSIC.value,
        "artist": artist or album.artist,
        "album": album,
        "album_genres": album_genres,
        "album_display_image": album_display_image,
        "media": {
            "media_type": MediaTypes.MUSIC.value,
            "source": Sources.MUSICBRAINZ.value,
            "media_id": (
                album.musicbrainz_release_id
                or album.musicbrainz_release_group_id
                or f"album-{album.id}"
            ),
            "title": album.title,
            "image": album_display_image,
            "synopsis": "",
            "details": {},
            "related": {},
        },
        # A long album renders its first page of rows; the rest load as the
        # list scrolls (music_album_track_rows). Totals above cover every track.
        "tracks": tracks_with_data[:ALBUM_TRACK_PAGE_SIZE],
        "tracks_next_offset": (
            ALBUM_TRACK_PAGE_SIZE
            if len(tracks_with_data) > ALBUM_TRACK_PAGE_SIZE
            else None
        ),
        "has_mb_identity": has_mb_identity,
        "album_tracker": album_tracker,
        "total_tracks": len(tracks_with_data),
        "library_track_count": library_track_count,
        "total_runtime": total_runtime,
        "music_album_metadata": album_details,
        "music_release_preference": music_release_preference,
        "selected_music_release": selected_music_release,
        "music_release_picker": {
            "available": bool(album.musicbrainz_release_group_id),
            "list_url": reverse(
                "list_music_releases",
                kwargs={"album_id": album.id},
            ),
            "selection_url": reverse(
                "set_music_release",
                kwargs={"album_id": album.id},
            ),
        },
        "album_collection_metadata": collection_metadata,
        "album_collection_stats": None,
        "music_album_activity_subtitle": album_activity_subtitle,
        "detail_notes_modal_url": reverse("album_track_modal", args=[album.id]),
        "detail_notes_target_id": f"album-track-modal-{album.id}",
        "detail_history_url": detail_history_url,
        "detail_primary_action": detail_primary_action,
        "detail_secondary_actions": _build_music_detail_secondary_actions(
            history_url=detail_history_url,
            sync_url=reverse("sync_album_metadata", args=[album.id]),
            sync_title="Sync album metadata and tracks from MusicBrainz",
            delete_url=reverse("delete_all_album_plays", args=[album.id]),
            delete_title="Delete all plays for this album",
            delete_confirm="Are you sure you want to delete all plays for this album? This cannot be undone.",
        ),
        "detail_collection_mode": "music_album",
        "detail_link_sections": detail_link_sections,
        "detail_tag_sections": detail_tag_sections,
        "detail_tag_preview_genres_json": json.dumps(album.genres or []),
        "detail_tag_preview_implied_genres_json": json.dumps(
            album.implied_genres or []
        ),
        "notes_entry": notes_entry,
    }
    return render(request, "app/media_details.html", context)


@require_GET
def music_artist_details(request, artist_id, artist_slug):
    """Return the canonical shared music artist detail page."""
    artist = get_object_or_404(Artist, id=artist_id)
    return _render_music_artist_details(request, artist)


@require_GET
def music_album_details(request, artist_id, artist_slug, album_id, album_slug):
    """Return the canonical shared music album detail page."""
    from app.services.music import canonicalize_album

    album = get_object_or_404(
        Album.objects.select_related("artist").prefetch_related(
            "artist_credits__artist"
        ),
        id=album_id,
    )
    canonical_album = canonicalize_album(album, user=request.user)
    if canonical_album.id != album.id:
        return redirect(_music_album_detail_url(canonical_album))
    album = canonical_album
    if album.artist_id and album.artist_id != artist_id:
        return redirect(_music_album_detail_url(album))
    artist = album.artist
    return _render_music_album_details(request, artist, album)


@require_GET
def music_album_track_rows(request, album_id):
    """Return the next page of an album's track rows for the infinite scroll."""
    album = get_object_or_404(Album.objects.select_related("artist"), id=album_id)
    try:
        offset = max(int(request.GET.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0
    tracks_with_data, _entries = album_track_rows(
        request.user,
        album,
        offset=offset,
        limit=ALBUM_TRACK_PAGE_SIZE,
    )
    end = offset + ALBUM_TRACK_PAGE_SIZE
    has_more = Track.objects.filter(album=album).count() > end
    _preference, selected_release = _selected_music_release(request.user, album)
    return render(
        request,
        "app/components/detail_music_album_track_rows.html",
        {
            "user": request.user,
            "album": album,
            "album_display_image": _album_display_image(album, selected_release),
            "tracks": tracks_with_data,
            "tracks_next_offset": end if has_more else None,
            # Links in these rows return to the album page, not this fragment.
            "return_url": _music_album_detail_url(album),
        },
    )


def _render_music_track_details(request, track):
    """Render a music track through the shared media details template."""
    album = track.album
    artist = album.artist
    music_entry = _music_entry_for_track(request.user, track)
    track_genres = _track_page_genres(track)
    recording_display = {"release_date": "", "runtime": "", "image": ""}
    if music_entry is None:
        recording = _lookup_unlistened_recording(track)
        if recording and not track_genres:
            track_genres = _recording_genre_names(recording)
        recording_display = _recording_display(track, recording)
    album_display_image = album.image or settings.IMG_NONE
    if recording_display["image"] and album_display_image in ("", settings.IMG_NONE):
        album_display_image = recording_display["image"]
    context = {
        "user": request.user,
        "music_detail_kind": "track",
        "media_type": MediaTypes.MUSIC.value,
        "artist": artist,
        "album": album,
        "track": track,
        "notes_entry": music_entry
        if music_entry and music_entry.notes.strip()
        else None,
        "detail_notes_modal_url": reverse("music_track_notes", args=[track.id]),
        "detail_notes_target_id": f"music-track-notes-{track.id}",
        "detail_return_url": request.get_full_path(),
        "album_display_image": album_display_image,
        "track_genres": track_genres,
        "recording_release_date": recording_display["release_date"],
        "recording_runtime": recording_display["runtime"],
        "track_history": _track_play_history(music_entry),
        "media": {
            "media_type": MediaTypes.MUSIC.value,
            "source": Sources.MUSICBRAINZ.value,
            "media_id": track.musicbrainz_recording_id or f"track-{track.id}",
            "title": track.title,
            "image": album_display_image,
            "synopsis": "",
            "details": {},
            "related": {},
        },
    }
    return render(request, "app/media_details.html", context)


@require_http_methods(["GET", "POST"])
def music_track_notes(request, track_id):
    """Edit private track notes without recording a listen."""
    from app import helpers

    track = get_object_or_404(
        Track.objects.select_related("album__artist"), id=track_id
    )
    music = _music_entry_for_track(request.user, track)
    form = MusicTrackNotesForm(
        request.POST if request.method == "POST" else None, instance=music
    )
    return_url = helpers.normalize_navigation_url(
        request.POST.get("return_url") or request.GET.get("return_url"),
    )
    if request.method == "POST" and form.is_valid():
        from app import fork_services_music

        fork_services_music.set_track_notes(
            request.user,
            track,
            form.cleaned_data["notes"],
        )
        messages.success(request, gettext("Notes saved."))
        if return_url and url_has_allowed_host_and_scheme(
            return_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(return_url)
        return redirect(_music_track_detail_url(track))
    response = render(
        request,
        "app/components/music_track_notes.html",
        {"form": form, "track": track, "return_url": return_url},
    )
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def music_track_details(
    request,
    artist_id,
    artist_slug,
    album_id,
    album_slug,
    track_id,
    track_slug,
):
    """Return the canonical shared music track detail page."""
    track = get_object_or_404(
        Track.objects.select_related("album__artist"),
        id=track_id,
    )
    if track.album_id != album_id:
        return redirect(_music_track_detail_url(track))
    album = track.album
    if album.artist_id and album.artist_id != artist_id:
        return redirect(_music_track_detail_url(track))
    return _render_music_track_details(request, track)


@require_GET
def track_detail(request, track_id):
    """Redirect a short track URL to the canonical shared detail page."""
    track = get_object_or_404(
        Track.objects.select_related("album__artist"),
        id=track_id,
    )
    return redirect(_music_track_detail_url(track))


@require_GET
def create_artist_from_search(request, musicbrainz_artist_id):
    """Create an Artist from MusicBrainz search and redirect to artist page."""
    # FORK: creation core shared with the REST API.
    from app import fork_services_music

    artist = fork_services_music.get_or_create_artist_from_musicbrainz(
        musicbrainz_artist_id,
    )
    return redirect(_music_artist_detail_url(artist))


@require_GET
def create_album_from_search(request, musicbrainz_release_id):
    """Create an Album from MusicBrainz search and redirect to album page."""
    from app.providers import musicbrainz

    release_data = musicbrainz.get_release(musicbrainz_release_id)
    release_group_id = release_data.get("release_group_id")
    release_type = release_data.get("release_type", "")

    album = Album.objects.filter(
        musicbrainz_release_id=musicbrainz_release_id,
    ).first()
    artist_credits = release_data.get("artist_credits", [])

    if not album:
        artist = None
        created_artists = []
        artist_id = release_data.get("artist_id")
        artist_name = release_data.get("artist_name")

        if artist_credits:
            for position, credit in enumerate(artist_credits):
                credit_artist_id = credit.get("artist_id")
                credit_artist_name = credit.get("name")
                if not credit_artist_name:
                    continue

                credit_artist = None
                if credit_artist_id:
                    credit_artist = Artist.objects.filter(
                        musicbrainz_id=credit_artist_id,
                    ).first()
                if not credit_artist:
                    credit_artist = Artist.objects.filter(
                        name=credit_artist_name,
                    ).first()
                if not credit_artist:
                    credit_artist = Artist.objects.create(
                        name=credit_artist_name,
                        sort_name=credit.get("sort_name", ""),
                        musicbrainz_id=credit_artist_id,
                    )

                if artist is None:
                    artist = credit_artist
                created_artists.append(
                    (
                        credit_artist,
                        position,
                        credit.get("join_phrase", ""),
                    )
                )
        else:
            if artist_id:
                artist = Artist.objects.filter(musicbrainz_id=artist_id).first()
                if not artist and artist_name:
                    artist = Artist.objects.create(
                        name=artist_name,
                        musicbrainz_id=artist_id,
                        country=release_data.get("country", "") or "",
                    )
            elif artist_name:
                artist = Artist.objects.filter(name=artist_name).first()
                if not artist:
                    artist = Artist.objects.create(name=artist_name)

            if artist:
                created_artists.append((artist, 0, ""))

        # If a discography sync already created this album via release-group,
        # reuse it rather than creating a duplicate with an unknown type.
        if release_group_id and artist:
            album = Album.objects.filter(
                artist=artist,
                musicbrainz_release_group_id=release_group_id,
            ).first()
            if album:
                update_fields = []
                if not album.musicbrainz_release_id:
                    album.musicbrainz_release_id = musicbrainz_release_id
                    update_fields.append("musicbrainz_release_id")
                if update_fields:
                    album.save(update_fields=update_fields)

        if not album:
            release_date = None
            date_str = release_data.get("release_date", "")
            if date_str:
                try:
                    from datetime import datetime

                    if len(date_str) == DATE_STR_LEN_YEAR_ONLY:
                        release_date = datetime.strptime(date_str, "%Y").date()  # noqa: DTZ007  # date-only value; no timezone applies
                    elif len(date_str) == DATE_STR_LEN_YEAR_MONTH:
                        release_date = datetime.strptime(date_str, "%Y-%m").date()  # noqa: DTZ007  # date-only value; no timezone applies
                    elif len(date_str) >= DATE_STR_LEN_FULL_DATE:
                        release_date = datetime.strptime(  # noqa: DTZ007  # date-only value; no timezone applies
                            date_str[:10],
                            "%Y-%m-%d",
                        ).date()
                except ValueError:
                    pass

            album = Album.objects.create(
                title=release_data.get("title", "Unknown Album"),
                musicbrainz_release_id=musicbrainz_release_id,
                musicbrainz_release_group_id=release_group_id,
                release_type=release_type,
                artist=artist,
                release_date=release_date,
                image=release_data.get("image", ""),
                genres=release_data.get("genres", []),
            )
            from app.models import AlbumArtist

            for credit_artist, position, join_phrase in created_artists:
                AlbumArtist.objects.get_or_create(
                    album=album,
                    artist=credit_artist,
                    defaults={"position": position, "join_phrase": join_phrase},
                )
            logger.info("Created album %s from MusicBrainz", album.title)
    else:
        if artist_credits:
            from app.services import music as sync_services

            sync_services.sync_album_artist_credits(album, release_data)
        elif not album.artist_credits.exists() and album.artist:
            from app.models import AlbumArtist

            AlbumArtist.objects.get_or_create(
                album=album,
                artist=album.artist,
                defaults={"position": 0, "join_phrase": ""},
            )

        new_image = release_data.get("image", "")
        if (
            new_image
            and new_image != settings.IMG_NONE
            and (not album.image or album.image == settings.IMG_NONE)
        ):
            album.image = new_image
            album.save(update_fields=["image"])
            logger.info("Updated album %s image", album.title)
        if release_data.get("genres"):
            album.genres = release_data.get("genres", [])
            album.save(update_fields=["genres"])
        sync_services.populate_album_implied_genres(album)

    return redirect(_music_album_detail_url(album))


@require_GET
def artist_detail(request, artist_id):
    """Redirect legacy music artist URLs to the canonical shared detail page."""
    artist = get_object_or_404(Artist, id=artist_id)
    return redirect(_music_artist_detail_url(artist))


@require_GET
def prefetch_artist_covers(request, artist_id):
    """HTMX endpoint to asynchronously fetch album covers for an artist.

    This runs after the artist page loads to avoid blocking the initial render.
    Returns the updated album grid HTML.
    """
    from app.services.music import build_discography_groups
    from app.tasks import prefetch_album_covers_batch

    artist = get_object_or_404(Artist, id=artist_id)

    all_albums = list(
        Album.objects.filter(
            models.Q(artist=artist) | models.Q(artist_credits__artist=artist),
        )
        .distinct()
        .select_related("artist")
        .prefetch_related("artist_credits__artist")
        .order_by("-release_date", "title"),
    )

    user_music_entries = (
        Music.objects.filter(
            user=request.user,
        )
        .filter(
            models.Q(album__artist=artist)
            | models.Q(album__artist_credits__artist=artist),
        )
        .select_related("album")
    )

    play_counts_by_music_id = _play_counts_by_music_id(user_music_entries)
    album_play_counts = {}
    for music in user_music_entries:
        if music.album_id:
            play_count = play_counts_by_music_id.get(music.id, 0)
            album_play_counts[music.album_id] = (
                album_play_counts.get(music.album_id, 0) + play_count
            )

    album_trackers = AlbumTracker.objects.filter(
        user=request.user,
        album__in=all_albums,
    ).select_related("album")
    album_scores = {
        tracker.album_id: tracker.score
        for tracker in album_trackers
        if tracker.score is not None
    }

    for album in all_albums:
        album.play_count = album_play_counts.get(album.id, 0)
        album.score = album_scores.get(album.id)

    discography_groups = build_discography_groups(all_albums)
    missing_cover_count = sum(1 for album in all_albums if not album.image)

    poll_for_covers = missing_cover_count > 0
    if missing_cover_count:
        cache_key = f"music:cover-prefetch:{artist.id}"
        try:
            if cache.add(cache_key, True, 60 * 10):
                try:
                    prefetch_album_covers_batch.delay(
                        [artist.id], limit_per_artist=None
                    )
                except Exception:  # pragma: no cover - defensive
                    cache.delete(cache_key)
                    raise
            poll_for_covers = bool(cache.get(cache_key))
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "Cover prefetch queue failed for artist %s: %s",
                artist.id,
                exception_summary(exc),
            )

    attempt = _poll_attempt(request)
    return render(
        request,
        "app/components/artist_discography_container.html",
        {
            "discography_groups": discography_groups,
            "artist": artist,
            "missing_cover_count": missing_cover_count,
            # Covers the provider cannot find never arrive, so polling stops
            # after a bounded number of tries rather than for the prefetch
            # marker's full ten minutes.
            "poll_for_covers": poll_for_covers
            and attempt < ARTIST_IMAGE_POLL_MAX_ATTEMPTS,
            "next_attempt": attempt + 1,
            "user": request.user,
        },
    )


@require_GET
def prefetch_artist_relation_images(request, artist_id):
    """HTMX endpoint to asynchronously fetch band-member/related-artist photos.

    This runs after the artist page loads to avoid blocking the initial render.
    Returns the updated members/also-in grid HTML.
    """
    artist = get_object_or_404(Artist, id=artist_id)

    band_members, member_of_bands, missing_relation_image_count = (
        _build_artist_relations(
            request.user,
            artist,
        )
    )

    poll_for_relation_images = missing_relation_image_count > 0
    if poll_for_relation_images:
        _queue_artist_relation_image_prefetch(artist, band_members, member_of_bands)
        poll_for_relation_images = bool(
            cache.get(f"music:artist-image-prefetch:{artist.id}"),
        )

    attempt = _poll_attempt(request)
    return render(
        request,
        "app/components/artist_relations_container.html",
        {
            "artist": artist,
            "band_members": band_members,
            "member_of_bands": member_of_bands,
            "missing_relation_image_count": missing_relation_image_count,
            "poll_for_relation_images": poll_for_relation_images
            and attempt < ARTIST_IMAGE_POLL_MAX_ATTEMPTS,
            "next_attempt": attempt + 1,
            "user": request.user,
        },
    )


@require_GET
def album_detail(request, album_id):
    """Redirect legacy music album URLs to the canonical shared detail page."""
    album = get_object_or_404(Album.objects.select_related("artist"), id=album_id)
    return redirect(_music_album_detail_url(album))


@require_POST
def sync_artist_discography_view(request, artist_id):
    """Manually trigger discography sync for an artist."""
    from app.services.music import prefetch_album_covers, sync_artist_discography
    from app.services.music_scrobble import dedupe_artist_albums
    from app.tasks import prefetch_album_covers_batch

    artist = get_object_or_404(Artist, id=artist_id)

    count = sync_artist_discography(artist, force=True)
    if count:
        dedupe_artist_albums(artist)

    cover_task_id = None
    try:
        result = prefetch_album_covers_batch.delay([artist.id], limit_per_artist=None)
        cover_task_id = result.id
        cache.set(f"music:cover-prefetch:{artist.id}", True, 60 * 10)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Cover prefetch queue failed for artist %s: %s",
            artist.id,
            exception_summary(exc),
        )
        try:
            prefetch_album_covers(artist, limit=None)
            cache.set(f"music:cover-prefetch:{artist.id}", True, 60 * 10)
        except Exception as inner_exc:  # pragma: no cover - defensive
            logger.debug(
                "Cover prefetch failed for artist %s: %s",
                artist.id,
                inner_exc,
            )

    if cover_task_id:
        messages.success(
            request,
            gettext(
                "Synced %(value_1)s albums for %(value_2)s. Cover art refresh queued."
            )
            % {"value_1": count, "value_2": artist.name},
        )
    else:
        messages.success(
            request,
            gettext("Synced %(value_1)s albums for %(value_2)s")
            % {"value_1": count, "value_2": artist.name},
        )

    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


def artist_track_modal(request, artist_id):
    """Return the shared tracking form modal for a music artist."""
    from app.forms import ArtistTrackerForm
    from app.models import ArtistTracker
    from app.tasks_music import populate_album_tracks_batch

    artist = get_object_or_404(Artist, id=artist_id)

    # Queue background population for albums whose tracks haven't been fetched yet
    albums_needing_tracks = list(
        Album.objects.filter(artist=artist, tracks_populated=False)
        .exclude(
            musicbrainz_release_id__isnull=True,
            musicbrainz_release_group_id__isnull=True,
        )
        .values_list("id", flat=True)
    )
    if albums_needing_tracks:
        populate_album_tracks_batch.delay(albums_needing_tracks)

    tracker = ArtistTracker.objects.filter(user=request.user, artist=artist).first()
    form = ArtistTrackerForm(
        instance=tracker,
        initial={"artist_id": artist.id},
        user=request.user,
    )
    return _render_music_tracker_modal(
        request,
        title=artist.name,
        tracker=tracker,
        form=form,
        save_url=reverse("artist_save"),
        delete_url=reverse("artist_delete"),
        bulk_domain=bulk_music_tracking.build_artist_play_domain(
            request.user, artist, fetch_missing=False
        ),
    )


@require_POST
def artist_save(request):
    """Save an artist tracker - mirrors media_save for TV."""
    from app.forms import ArtistTrackerForm
    from app.models import ArtistTracker

    artist_id = request.POST.get("artist_id")
    artist = get_object_or_404(Artist, id=artist_id)
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.MUSIC.value,
    )
    home_row_id = request.GET.get("home_row_id") or request.POST.get("home_row_id", "")

    tracker = ArtistTracker.objects.filter(user=request.user, artist=artist).first()
    old_status = getattr(tracker, "status", None)

    form = ArtistTrackerForm(request.POST, instance=tracker, user=request.user)
    if form.is_valid():
        tracker = form.save(commit=False)
        tracker.user = request.user
        tracker.artist = artist
        tracker.save()
        messages.success(
            request, gettext("Saved %(value_1)s") % {"value_1": artist.name}
        )

        if request.headers.get("HX-Request"):
            htmx_trigger = {
                "closeModal": {},
                "showToast": {
                    "message": f"Saved {artist.name}.",
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
            % {"value_1": artist.name, "value_2": form.errors},
        )

    next_url = request.GET.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(_music_artist_detail_url(artist))


@require_POST
def artist_delete(request):
    """Delete an artist tracker - mirrors media_delete for TV."""
    from app.models import ArtistTracker

    artist_id = request.POST.get("artist_id")
    artist = get_object_or_404(Artist, id=artist_id)
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.MUSIC.value,
    )

    tracker = ArtistTracker.objects.filter(user=request.user, artist=artist).first()
    if tracker:
        tracker.delete()
        messages.success(
            request,
            gettext("Removed %(value_1)s from your library") % {"value_1": artist.name},
        )

    next_url = request.GET.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(_music_artist_detail_url(artist))
