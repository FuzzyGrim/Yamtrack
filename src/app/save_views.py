import contextlib
import json
import logging
from datetime import datetime, time, timedelta
from urllib.parse import quote, urlparse
from uuid import uuid4

import requests
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.db.models import prefetch_related_objects
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext, pgettext
from django.views.decorators.http import require_GET, require_POST

from app import cache_utils, fork_services_episode, helpers, history_cache
from app.activity_builders import _build_detail_activity_state
from app.card_events import card_update_event
from app.discover import tab_cache as discover_tab_cache
from app.forms import EpisodeForm, get_form_class
from app.history_processor import USER_EDIT_REASON
from app.models import (
    TV,
    BasicMedia,
    CollectionEntry,
    Episode,
    Item,
    MediaTypes,
    RewatchAlreadyCompleteError,
    Season,
    Sources,
    Status,
)
from app.providers import services
from app.request_timing import boundary
from app.services import metadata_resolution
from app.services.completion import select_preferred_activity_entry
from app.services.episode_coordinates import (
    InvalidEpisodeCoordinateError,
    cleanup_episode_history_for_route,
    resolve_episode_coordinate,
)
from app.services.tracking_hydration import ensure_item_metadata
from app.templatetags.app_tags import media_url
from app.track_modal_views import (
    _render_standard_track_modal,
)
from integrations.state.ratings import rating_edit

logger = logging.getLogger(__name__)

DETAILS_ROUTE_MIN_SEGMENTS = 2  # "details", "<source>", ... in the URL path


def _prefetch_show_trees(shows):
    """Load each show's seasons, episodes and calendar once for the response.

    The save response reads progress, next episode and watch dates from every
    season, several times over. Unprefetched, each read reloads all of the
    show's episodes, which on a long show cost more than the save itself. This
    is the detail page's prefetch; anything already loaded is left as it is.
    """
    shows = [show for show in shows if isinstance(show, TV)]
    if shows:
        lookups = BasicMedia.objects._apply_prefetch_related(
            TV.objects.none(), MediaTypes.TV.value,
        )._prefetch_related_lookups
        prefetch_related_objects(shows, *lookups)


@require_POST
@rating_edit
def media_save(request):
    """Save or update media data to the database."""
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    identity_media_type = request.POST.get("identity_media_type") or None
    library_media_type = request.POST.get("library_media_type") or None
    season_number = request.POST.get("season_number")
    instance_id = request.POST.get("instance_id")
    tracking_media_type = metadata_resolution.get_tracking_media_type(
        media_type,
        source=source,
        identity_media_type=identity_media_type,
    )

    # A season already tracked under a different identity bucket (e.g. via
    # the show's TV identity) must resolve to the same row here as it does
    # on the detail page, otherwise this falls into the create path below
    # and collides with the existing row's unique constraint — see #623.
    if (
        not instance_id
        and tracking_media_type == MediaTypes.SEASON.value
        and season_number
    ):
        existing_season = metadata_resolution.find_tracked_season(
            request.user,
            media_id,
            source,
            int(season_number),
            library_media_type=library_media_type,
        )
        if existing_season is not None:
            instance_id = str(existing_season.id)

    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=library_media_type or media_type,
    )

    # Handle percentage conversion for books/comics/manga
    progress_value = request.POST.get("progress")
    if (
        progress_value
        and media_type
        in (
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MANGA.value,
        )
        and request.user.book_comic_manga_progress_percentage
    ):
        # Make POST mutable for modification
        mutable_post = request.POST.copy()
        max_progress = None
        item = None

        # Get item to determine max_progress
        if instance_id:
            instance = BasicMedia.objects.get_media(
                request.user,
                media_type,
                instance_id,
            )
            if instance:
                item = instance.item
        else:
            # For new entries, get metadata first to get/create item
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                [season_number],
                user=request.user,
            )
            if media_type == MediaTypes.BOOK.value:
                number_of_pages = metadata.get("max_progress") or metadata.get(
                    "details", {}
                ).get("number_of_pages")
            else:
                number_of_pages = None
            item, _ = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=tracking_media_type,
                season_number=season_number,
                defaults={
                    **Item.title_fields_from_metadata(metadata),
                    "library_media_type": (
                        library_media_type
                        or metadata.get("library_media_type")
                        or media_type
                    ),
                    "image": metadata["image"],
                    "number_of_pages": number_of_pages,
                },
            )

        if item:
            if media_type == MediaTypes.BOOK.value:
                max_progress = item.book_max_progress
                if not max_progress and item.format != "audiobook":
                    # Try to fetch from metadata
                    try:
                        metadata = services.get_media_metadata(
                            item.media_type,
                            item.media_id,
                            item.source,
                            user=request.user,
                        )
                        number_of_pages = metadata.get("max_progress") or metadata.get(
                            "details", {}
                        ).get("number_of_pages")
                        if number_of_pages:
                            item.number_of_pages = number_of_pages
                            item.save(update_fields=["number_of_pages"])
                            max_progress = number_of_pages
                    except Exception:  # noqa: S110  # deliberate best-effort; failure is non-fatal here
                        pass
            else:
                # For comics and manga, need to get max_progress from events
                from app.models import Comic, Manga

                model_class = Manga if media_type == MediaTypes.MANGA.value else Comic
                media_list = list(
                    model_class.objects.filter(
                        user=request.user, item=item
                    ).select_related("item")
                )
                if media_list:
                    BasicMedia.objects.annotate_max_progress(media_list, media_type)
                    if hasattr(media_list[0], "max_progress"):
                        max_progress = media_list[0].max_progress

            if max_progress:
                try:
                    percentage = float(progress_value)
                    converted_progress = round((percentage / 100) * max_progress)
                    mutable_post["progress"] = str(converted_progress)
                    request.POST = mutable_post
                except (ValueError, TypeError):
                    pass

    if instance_id:
        instance = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    else:
        try:
            with boundary("media_save_hydrate"):
                hydrated = ensure_item_metadata(
                    request.user,
                    media_type,
                    media_id,
                    source,
                    season_number,
                    identity_media_type=identity_media_type,
                    library_media_type=library_media_type,
                    edition_id=(request.POST.get("edition_id") or "").strip() or None,
                )
        except services.ProviderNotConfiguredError:
            # Setup guidance is rendered by the provider-error middleware.
            raise
        except services.ProviderAPIError as error:
            # A provider that no longer has the title, or is down, is a failed
            # save the user can read about, not a server error.
            logger.warning(
                "First-save metadata hydration hit a provider error for "
                "media_type=%s source=%s media_id=%s status=%s user_id=%s",
                media_type,
                source,
                media_id,
                error.status_code,
                request.user.id,
            )
            if error.status_code == requests.codes.not_found:
                message = gettext(
                    "%(provider)s no longer has this title, so it can't be saved."
                ) % {"provider": error.provider_label}
            else:
                message = gettext(
                    "%(provider)s did not respond. Please try saving again."
                ) % {"provider": error.provider_label}
            if request.headers.get("HX-Request"):
                # htmx follows a redirect and would swap the whole page in, and
                # the messages framework is never rendered for it, so answer
                # with a toast. It does not swap a non-2xx body.
                response = HttpResponse(status=502)
                response["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": message, "type": "error"}},
                )
                return response
            messages.error(request, message)
            return helpers.redirect_back(request)
        except Exception:
            logger.exception(
                "First-save metadata hydration failed for "
                "media_type=%s source=%s media_id=%s season_number=%s user_id=%s",
                media_type,
                source,
                media_id,
                season_number,
                request.user.id,
            )
            raise
        model = apps.get_model(app_label="app", model_name=tracking_media_type)
        instance = model(item=hydrated.item, user=request.user)

        if tracking_media_type == MediaTypes.MUSIC.value:
            instance.artist = hydrated.artist
            instance.album = hydrated.album
            instance.track = hydrated.track
        if (
            tracking_media_type == MediaTypes.PODCAST.value
            and hydrated.podcast_show is not None
        ):
            instance.show = hydrated.podcast_show

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(tracking_media_type)
    form = form_class(request.POST, instance=instance, user=request.user)
    media = instance
    is_htmx = bool(request.headers.get("HX-Request"))
    track_form_id = request.POST.get("track_form_id") or (f"track-form-{uuid4().hex}")
    return_url = quote(
        request.GET.get("next") or request.POST.get("return_url") or "",
        safe="",
    )
    home_row_id = request.GET.get("home_row_id") or ""
    old_status = getattr(instance, "status", None) if instance_id else None
    action_verb = "Added" if not instance_id else "Updated"
    action_label = (
        pgettext("saved action", "Added")
        if not instance_id
        else pgettext("saved action", "Updated")
    )
    with boundary("media_save_validate"):
        valid = form.is_valid()
    if valid:
        if isinstance(instance, (Season, TV)):
            media = form.save(commit=False)
            media._pending_end_date = form.cleaned_data.get("end_date")
            # Recorded in history so an automatic change can be told apart
            # from the user's own edit (#1133).
            media._change_reason = USER_EDIT_REASON
            with boundary("media_save_persist"):
                media.save()
            if (
                isinstance(media, Season)
                and old_status == Status.COMPLETED.value
                and media.status == Status.IN_PROGRESS.value
            ):
                # The status dropdown is the only "reopen" affordance there
                # is - treat it as starting a rewatch pass so a season with
                # historical repeat plays can still complete normally, see #929.
                # Deliberately bypasses start_rewatch's "a pass is already
                # open" no-op: an explicit Completed -> In progress reopen is
                # the user asking for a new pass from now, so the cutoff has to
                # move. Only reachable from that transition - any future caller
                # reaching this with an open pass would strand plays logged
                # against the original cutoff as pre-cutoff history.
                if media.rewatch_started_at is not None:
                    media.rewatch_started_at = None
                with contextlib.suppress(RewatchAlreadyCompleteError):
                    media.start_rewatch()
        else:
            with boundary("media_save_persist"):
                media = form.save()
        if (
            media_type == MediaTypes.BOOK.value
            and "koreader_document_id" in request.POST
        ):
            from django.db import IntegrityError

            from integrations.koreader_links import (
                normalize_document_hash,
                save_document_link,
            )

            raw_hash = request.POST.get("koreader_document_id", "")
            normalized = normalize_document_hash(raw_hash)
            if raw_hash.strip() and normalized is None:
                messages.error(
                    request,
                    gettext(
                        "KOReader document ID must be a 32-character hexadecimal hash."
                    ),
                )
            else:
                try:
                    save_document_link(request.user, media.item, normalized or "")
                except IntegrityError:
                    messages.error(
                        request,
                        gettext(
                            "That KOReader document ID is already linked to another book."
                        ),
                    )
        with boundary("media_save_progress"):
            BasicMedia.objects.annotate_max_progress([media], media_type)
        image_url = form.cleaned_data.get("image_url")
        if image_url and media.item.image != image_url:
            media.item.image = image_url
            media.item.save(update_fields=["image"])
        logger.info("%s saved successfully.", media)
        display_title = (
            media.item.get_display_title(request.user)
            if hasattr(media.item, "get_display_title")
            else media.item.title
        ) or "item"
        if is_htmx:
            _prefetch_show_trees([media])
            fragment_context = (
                f"{action_verb.lower()} save media_type={media_type} "
                f"source={source} media_id={media_id} "
                f"instance_id={instance_id} user_id={request.user.id}"
            )

            # The status pill is the response itself, not an OOB fragment, so
            # it stays outside the per-fragment isolation below: if it cannot
            # render there is nothing to return. This matches the old
            # behaviour, whose fallback rendered this same template and so
            # raised again anyway.
            response = render(
                request,
                "app/components/detail_track_action.html",
                {
                    "media": media.item,
                    "current_instance": media,
                    "return_url": return_url,
                    "track_action_update": True,
                    "swap_oob": True,
                },
            )

            def _activity_subtitle_fragment():
                user_medias = list(
                    media.__class__.objects.filter(
                        user=request.user, item=media.item
                    ).select_related(
                        "item",
                    ),
                )
                _prefetch_show_trees(user_medias)
                play_stats, activity_subtitle = _build_detail_activity_state(
                    media_type,
                    {"max_progress": getattr(media, "max_progress", None)},
                    current_instance=media,
                    user_medias=user_medias,
                    public_view=False,
                )
                return render_to_string(
                    "app/components/detail_activity_subtitle_slot.html",
                    {
                        "media": media.item,
                        "media_type": media_type,
                        "current_instance": media,
                        "activity_subtitle": activity_subtitle,
                        "play_stats": play_stats,
                        "user": request.user,
                        "activity_subtitle_slot_oob": True,
                    },
                    request=request,
                )

            def _score_chip_fragment():
                return render_to_string(
                    "app/components/detail_score_chip_slot.html",
                    {
                        "media": media.item,
                        "current_instance": media,
                        "media_type": media_type,
                        "user": request.user,
                        "user_medias": [media],
                        "public_view": False,
                        "csrf_token": request.META.get("CSRF_COOKIE", ""),
                        "score_chip_slot_oob": True,
                    },
                    request=request,
                )

            def _progress_card_fragment():
                if media_type not in (MediaTypes.TV.value, MediaTypes.SEASON.value):
                    return None
                return render_to_string(
                    "app/components/detail_progress_card_slot.html",
                    {
                        "media": media.item,
                        "media_type": media_type,
                        "current_instance": media,
                        "progress_card_slot_oob": True,
                    },
                    request=request,
                )

            def _card_rating_fragment():
                return render_to_string(
                    "app/components/media_card_rating_oob.html",
                    {
                        "item": media.item,
                        "rating_media_type": media.item.media_type,
                        "rating_value": media.formatted_score,
                        "rate_url": reverse(
                            "update_media_score",
                            args=[media.item.media_type, media.id],
                        ),
                        "user": request.user,
                    },
                    request=request,
                )

            def _status_chip_fragment():
                return render_to_string(
                    "app/components/media_card_status_chip.html",
                    {
                        "item": media.item,
                        "media": media,
                        "status_chip_oob": True,
                    },
                    request=request,
                )

            def _season_cascade_fragment():
                # A season completing (or reopening) can cascade to complete
                # (or reopen) its show — Season.save() already applies that
                # server-side, but nothing else refreshes the show's own
                # pill, e.g. when marking a season watched from the show
                # page rather than the season's own page.
                if media_type != MediaTypes.SEASON.value or not media.related_tv_id:
                    return None
                tv = (
                    TV.objects.filter(pk=media.related_tv_id)
                    .select_related("item")
                    .first()
                )
                if not tv:
                    return None
                return _render_track_action_oob(request, tv, media_url(tv.item))

            def _notes_section_fragment():
                if media_type not in (
                    MediaTypes.MOVIE.value,
                    MediaTypes.TV.value,
                    MediaTypes.SEASON.value,
                    MediaTypes.ANIME.value,
                ):
                    return None
                return _render_notes_section_oob(
                    request,
                    media.__class__.objects.filter(
                        user=request.user,
                        item=media.item,
                    ),
                    media=media.item,
                )

            for label, build in (
                ("activity subtitle", _activity_subtitle_fragment),
                ("score chip", _score_chip_fragment),
                ("progress card", _progress_card_fragment),
                ("card rating", _card_rating_fragment),
                ("status chip", _status_chip_fragment),
                ("season cascade pill", _season_cascade_fragment),
                ("notes section", _notes_section_fragment),
            ):
                _append_optional_fragment(
                    response,
                    label,
                    build,
                    context=fragment_context,
                )
            htmx_trigger = {
                "closeModal": {"formId": track_form_id},
                "showToast": {
                    "message": gettext("%(value_1)s %(value_2)s.")
                    % {
                        "value_1": action_label,
                        "value_2": display_title,
                    },
                    "type": "success",
                },
            }
            if home_row_id and instance_id and old_status != media.status:
                htmx_trigger["refreshHomeRow"] = {"rowId": int(home_row_id)}
            htmx_trigger["floppyCardUpdate"] = card_update_event(
                media.item,
                media,
                request.user,
            )
            response["HX-Trigger"] = json.dumps(
                htmx_trigger,
            )
            response["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
            return response
        messages.success(
            request,
            gettext("%(value_1)s %(value_2)s.")
            % {
                "value_1": action_label,
                "value_2": display_title,
            },
        )
    else:
        logger.error(form.errors.as_json())
        if is_htmx:
            modal_response = _render_standard_track_modal(
                request,
                source,
                media_type,
                media_id,
                season_number=season_number,
                form_override=form,
                track_form_id=track_form_id,
                return_url=return_url,
                track_action_update=True,
            )
            # A failed create has no saved row of its own; a movie or game
            # button belongs to the entry the page already shows.
            shown_instance = media
            if media.pk is None and media_type in (
                MediaTypes.MOVIE.value,
                MediaTypes.GAME.value,
            ):
                shown_instance = (
                    select_preferred_activity_entry(
                        BasicMedia.objects.filter_media_prefetch(
                            request.user,
                            media_id,
                            media_type,
                            source,
                        ),
                    )
                    or media
                )
            response = render(
                request,
                "app/components/detail_track_action.html",
                {
                    "media": media.item,
                    "current_instance": shown_instance,
                    "return_url": return_url,
                    "track_open": "edit" if instance_id else "create",
                    "track_modal_content": modal_response.content.decode(),
                    "track_action_update": True,
                    "swap_oob": True,
                },
            )
            response["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
            return response
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(
                    request,
                    gettext("%(value_1)s: %(value_2)s")
                    % {
                        "value_1": gettext(field.replace("_", " ").title()),
                        "value_2": error,
                    },
                )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=media_type,
    )
    model = apps.get_model(app_label="app", model_name=media_type)

    try:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
        if media_type == MediaTypes.EPISODE.value:
            episode_item = media.item
            related_season = media.related_season
        start_date = getattr(media, "start_date", None)
        end_date = getattr(media, "end_date", None)
        created_at = getattr(media, "created_at", None)
        media.delete()
        logger.info("%s deleted successfully.", media)

        if media_type == MediaTypes.EPISODE.value and request.headers.get(
            "HX-Request",
        ):
            related_season._sync_status_after_episode_change()
            cache_utils.clear_library_view_caches(
                related_season.user_id,
                media_types=(MediaTypes.EPISODE.value,),
            )
            history_day_key = history_cache.history_day_key(
                end_date or start_date or created_at,
            )
            if history_day_key:
                history_cache.invalidate_history_days(
                    request.user.id,
                    day_keys=[history_day_key],
                    logging_styles=("sessions", "repeats"),
                    reason="media_delete",
                    force=True,
                )

            episode_history = list(
                Episode.objects.filter(
                    related_season=related_season,
                    item=episode_item,
                )
                .select_related("item", "related_season")
                .order_by("-end_date", "-created_at"),
            )
            episode = episode_history[0] if episode_history else media
            episode.history = episode_history
            episode.collection_entry = (
                CollectionEntry.objects.filter(
                    item=episode_item,
                    user=request.user,
                )
                .select_related("item")
                .first()
            )

            response = HttpResponse()
            _write_episode_save_oob(
                response,
                request,
                episode=episode,
                related_season=related_season,
                media_id=episode_item.media_id,
                source=episode_item.source,
                season_number=episode_item.season_number,
                episode_number=episode_item.episode_number,
                next_path=request.GET.get("next") or "",
            )
            response["HX-Trigger"] = json.dumps(
                {
                    "closeModal": {},
                    "showToast": {
                        "message": f"Removed watch for episode {episode_item.episode_number}.",
                        "type": "success",
                    },
                },
            )
            response["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
            return response

        if media_type in (MediaTypes.GAME.value, MediaTypes.BOARDGAME.value):
            history_day_keys = history_cache.history_day_keys_for_range(
                start_date or end_date,
                end_date or start_date,
            )
        else:
            activity_dt = end_date or start_date or created_at
            history_day_key = history_cache.history_day_key(activity_dt)
            history_day_keys = [history_day_key] if history_day_key else []

        if history_day_keys:
            history_cache.invalidate_history_days(
                request.user.id,
                day_keys=history_day_keys,
                logging_styles=("sessions", "repeats"),
                reason="media_delete",
                force=True,
            )

    except model.DoesNotExist:
        logger.warning("The %s was already deleted before.", media_type)

    redirect_response = helpers.redirect_back(request)
    if request.headers.get("HX-Request"):
        return HttpResponse(status=204, headers={"HX-Redirect": redirect_response.url})
    return redirect_response


def _parse_rewatch_start(raw_value):
    """Return an aware datetime for a pass start, defaulting to now.

    Raises ValueError for anything unparseable or in the future, since a pass
    starting later than now would hide every play the user logs today.
    """
    value = (raw_value or "").strip()
    if not value:
        return timezone.now()

    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        if parsed_date is None:
            raise ValueError(value)
        parsed = datetime.combine(parsed_date, time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    if parsed > timezone.now():
        raise ValueError(value)
    return parsed


@require_POST
def media_rewatch(request):
    """Start or stop a rewatch pass for a show or season."""
    media_type = request.POST["media_type"]
    instance_id = request.POST["instance_id"]
    # The track-modal button carries the action in the query string, the same
    # way the other modal actions carry `next`.
    action = request.POST.get("action") or request.GET.get("action") or "start"

    if media_type not in {MediaTypes.TV.value, MediaTypes.SEASON.value}:
        return HttpResponseBadRequest("Only shows and seasons can be rewatched.")

    model = apps.get_model(app_label="app", model_name=media_type)
    media = get_object_or_404(model, pk=instance_id, user=request.user)

    if action == "stop":
        from app.services.progress_breakdown import build_progress_breakdown
        from app.services.rewatch_progress import cancel_run, resolve_rewatch

        tv = media if media_type == MediaTypes.TV.value else media.related_tv
        breakdown = build_progress_breakdown(tv=tv)
        inferred = resolve_rewatch(tv, breakdown.seasons, include_explicit=False, include_candidates=True)
        if inferred is not None:
            cancel_run(tv, inferred)
        # Each model resolves its own season(s) against their own episode
        # counts — a show's seasons can have different lengths.
        media.stop_rewatch()
        logger.info("Rewatch of %s ended.", media)
    else:
        try:
            started_at = _parse_rewatch_start(request.POST.get("rewatch_started_at"))
        except ValueError:
            return HttpResponseBadRequest("Enter a rewatch start date in the past.")
        try:
            skipped_seasons = media.start_rewatch(started_at=started_at) or []
        except RewatchAlreadyCompleteError as error:
            # htmx doesn't swap a non-2xx response, so without an explicit
            # trigger the button click would just silently do nothing.
            response = HttpResponseBadRequest(error.message)
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"message": error.message, "type": "error"}},
            )
            return response
        if skipped_seasons:
            # A show-level start only raises when *every* season would be a
            # no-op; a partial skip otherwise succeeds with no other signal
            # that some seasons were left untouched, so say so explicitly.
            # This uses the messages framework, not the showToast trigger
            # above, because the success response below causes a full page
            # navigation (HX-Redirect) that a same-request toast wouldn't
            # survive.
            skipped_numbers = sorted(
                season.item.season_number for season in skipped_seasons
            )
            season_word = "Season" if len(skipped_numbers) == 1 else "Seasons"
            skipped_list = ", ".join(str(number) for number in skipped_numbers)
            messages.warning(
                request,
                gettext(
                    "%(value_1)s %(value_2)s already fully watched from that date. Left as is."
                )
                % {"value_1": gettext(season_word), "value_2": skipped_list},
            )
        logger.info("Rewatch of %s started, from %s.", media, started_at)

    cache_utils.clear_library_view_caches(request.user.id, media_types=(media_type,))

    redirect_response = helpers.redirect_back(request)
    if request.headers.get("HX-Request"):
        return HttpResponse(status=204, headers={"HX-Redirect": redirect_response.url})
    return redirect_response


def _append_optional_fragment(response, label, build, *, context):
    """Append one optional OOB fragment, or log and skip only that fragment.

    A save response is one required element - the status pill - followed by
    several independent OOB fragments appended to it. Wrapping all of them in a
    single try meant a failure in any one discarded the whole response: the
    handler rebound `response` to a bare confirmation, so the later a fragment
    failed, the more already-rendered work was thrown away, and the client saw
    a 200 with most of the page silently not updating.

    Each fragment is now isolated, so a broken one costs exactly itself.
    """
    try:
        html = build()
    except Exception:
        logger.exception(
            "Post-save OOB fragment %s failed for %s; the record was saved and "
            "the rest of the response is unaffected.",
            label,
            context,
        )
        return
    if html:
        response.write(html)


def _render_season_progress_oob(related_season):
    """Render the mobile+desktop season-progress OOB span pair as one string.

    `max_progress` is set as a side effect of `Episode.save()` (from freshly
    fetched provider metadata) rather than being a persisted field, so a
    `related_season` fetched independently of a just-saved Episode — as in
    the history poller — may not have it. Fall back to None like every other
    read site in the codebase (e.g. `helpers.py`, `home_screen.py`).
    """
    progress = related_season.completed_episode_count
    max_progress = getattr(related_season, "max_progress", None)
    if max_progress:
        progress = f"{progress}/{max_progress}"
    spans = ""
    for variant in ("mobile", "desktop"):
        spans += (
            f'<span id="season-progress-{variant}-{related_season.id}" '
            f'hx-swap-oob="true" class="text-sm font-medium text-gray-400">'
            f"Progress: {progress}</span>"
        )
    return spans


def _render_notes_section_oob(
    request,
    entries,
    *,
    media=None,
    detail_notes_target_id="",
    detail_notes_modal_url="",
    detail_return_url="",
):
    """Render the detail notes section as an OOB swap after a watch save.

    The notes section lists one block per watch (see detail_notes_section),
    so a note edited in the track modal must be pushed back into the page
    without a full reload.

    The caller supplies the watches rather than this building the queryset:
    Episode has no `user` field (it is scoped through related_season), so a
    single `filter(user=...)` here cannot serve both the media and episode
    save paths. The modal ids are caller-supplied for the same reason — the
    episode page namespaces its modal targets differently from the movie,
    season and show pages.
    """
    return render_to_string(
        "app/components/detail_notes_section.html",
        {
            "notes_entries": [
                entry for entry in entries if entry.notes and entry.notes.strip()
            ],
            "media": media,
            "user": request.user,
            "public_notes_view": False,
            "public_view": False,
            "detail_return_url": detail_return_url,
            "detail_notes_modal_url": detail_notes_modal_url,
            "detail_notes_target_id": detail_notes_target_id,
            "notes_section_oob": True,
        },
        request=request,
    )


def _render_track_action_oob(request, instance, return_url, *, polled=False):
    """Render a tracked instance's own status pill as an OOB swap.

    Watching an episode (or a webhook/scrobbler writing one with no open
    response to attach to) can complete the season — or completing the
    season directly fills in the episodes it missed, and can in turn
    complete the show — and none of that has an open response of its own
    to refresh this pill, so callers on any side of that need to push it
    explicitly. Works for any tracked instance with an `.item` (Season,
    TV, ...), not just seasons.

    `polled` marks a background refresh: the page skips that swap while the
    tracking dialog is open, so polling never closes it or drops the form.
    """
    return render_to_string(
        "app/components/detail_track_action.html",
        {
            "media": instance.item,
            "current_instance": instance,
            "return_url": return_url,
            "track_action_update": True,
            "swap_oob": True,
            "polled": polled,
        },
        request=request,
    )


def _write_episode_save_oob(
    response,
    request,
    *,
    episode,
    related_season,
    media_id,
    source,
    season_number,
    episode_number,
    next_path,
):
    """Write the OOB fragments for an episode watch/drop response.

    The season-details episode list and the standalone episode page render
    different markup (small round button + history line + season-progress spans
    vs. a hero pill button + an always-present rating-chip slot), so each needs
    its own OOB targets. We detect the standalone episode page from the `next`
    path and emit the matching variant; sending the wrong one just no-ops since
    HTMX silently drops OOB swaps with no matching element.
    """
    parsed_next = urlparse(next_path).path
    path_parts = [segment for segment in parsed_next.split("/") if segment]
    is_episode_page = (
        len(path_parts) >= DETAILS_ROUTE_MIN_SEGMENTS
        and path_parts[0] == "details"
        and "episode" in path_parts
    )

    if is_episode_page:
        response.write(
            render_to_string(
                "app/components/detail_episode_hero_track_button.html",
                {
                    "episode": episode,
                    "source": source,
                    "media_id": media_id,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "track_button_oob": True,
                    # request.path here is /episode_save/, not the page the
                    # button lives on, so the button would send the wrong
                    # `next` on its second use.
                    "track_return_url": parsed_next,
                },
                request=request,
            ),
        )
        response.write(
            render_to_string(
                "app/components/detail_episode_rating_chip.html",
                {
                    "episode": episode,
                    "current_instance": related_season,
                    "user": request.user,
                    "source": source,
                    "media_id": media_id,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "public_view": False,
                    "rating_chip_oob": True,
                },
                request=request,
            ),
        )
        response.write(
            _render_track_action_oob(request, related_season, parsed_next),
        )
        # The episode page lists every watch note, same as the movie/season
        # pages, so an edited note has to be pushed back the same way. Episode
        # rows are scoped by season rather than by user, and the page namespaces
        # its modal targets per episode, so both are passed in explicitly.
        if episode.item_id:
            response.write(
                _render_notes_section_oob(
                    request,
                    Episode.objects.filter(
                        related_season=related_season,
                        item=episode.item,
                    ).select_related("item"),
                    media=episode.item,
                    detail_notes_target_id=(
                        f"episode-notes-modal-{source}-{media_id}"
                        f"-{season_number}-{episode_number}"
                    ),
                    detail_notes_modal_url=reverse(
                        "track_modal",
                        kwargs={
                            "source": source,
                            "media_type": MediaTypes.EPISODE.value,
                            "media_id": media_id,
                            "season_number": season_number,
                        },
                    ),
                    detail_return_url=parsed_next,
                ),
            )
        # Season-progress spans only exist on the season page — nothing to target here.
        return

    for target_suffix in ("", "-list"):
        response.write(
            render_to_string(
                "app/components/detail_episode_track_button.html",
                {
                    "episode": episode,
                    "target_suffix": target_suffix,
                    "track_button_oob": True,
                },
                request=request,
            ),
        )
    response.write(
        render_to_string(
            "app/components/detail_episode_collection_button.html",
            {
                "episode": episode,
                "collection_button_oob": True,
                "detail_return_url": parsed_next,
            },
            request=request,
        ),
    )
    response.write(
        render_to_string(
            "app/components/detail_episode_history_line.html",
            {
                "episode": episode,
                "user": request.user,
                "history_oob": True,
            },
            request=request,
        ),
    )
    response.write(_render_season_progress_oob(related_season))
    response["HX-Trigger-After-Swap"] = json.dumps(
        {
            "detail-progress-updated": {
                "id": related_season.id,
                "tv_id": related_season.related_tv_id,
                "completed": related_season.completed_episode_count,
                "watched_once": len(related_season.watched_once_numbers),
                "rewatching": related_season.is_rewatching,
            },
        },
    )
    response.write(
        _render_track_action_oob(request, related_season, parsed_next),
    )


@require_POST
@rating_edit
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    media_id = request.POST["media_id"]
    season_number = int(request.POST["season_number"])
    episode_number = int(request.POST["episode_number"])
    source = request.POST["source"]
    library_media_type = (request.POST.get("library_media_type") or "").strip()

    next_path = request.GET.get("next") or ""
    if source == Sources.TMDB.value and next_path:
        parsed_next_path = urlparse(next_path).path
        path_parts = [segment for segment in parsed_next_path.split("/") if segment]
        if len(path_parts) >= DETAILS_ROUTE_MIN_SEGMENTS and path_parts[0] == "details":
            route_source = path_parts[1]
            if route_source in {choice[0] for choice in Sources.choices}:
                source = route_source

    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.TV.value,
    )

    save_as_new_entry = request.POST.get("save_as_new_entry") == "1"
    instance_id = None if save_as_new_entry else request.POST.get("instance_id")
    episode_instance = None
    if instance_id:
        episode_instance = BasicMedia.objects.get_media(
            request.user,
            MediaTypes.EPISODE.value,
            instance_id,
        )
        media_id = episode_instance.item.media_id
        source = episode_instance.item.source
        season_number = episode_instance.item.season_number
        episode_number = episode_instance.item.episode_number

    form = EpisodeForm(request.POST, instance=episode_instance, user=request.user)
    if not form.is_valid():
        logger.warning("Episode form validation failed fields=%s", sorted(form.errors))
        return HttpResponseBadRequest("Invalid form data")

    if episode_instance:
        related_season = episode_instance.related_season
        episode = form.save(commit=False)
        episode.dropped = episode.status == Status.DROPPED.value
        episode.save()
    else:
        try:
            resolve_episode_coordinate(
                media_id,
                source,
                season_number,
                episode_number,
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except InvalidEpisodeCoordinateError:
            cleanup_episode_history_for_route(
                request.user,
                media_id,
                source,
                season_number,
                episode_number,
                library_media_type=library_media_type,
            )
            return HttpResponse("Episode not found", status=404)
        related_season = fork_services_episode.resolve_or_create_season(
            request.user,
            media_id,
            source,
            season_number,
            library_media_type=library_media_type,
        )
        try:
            watch_operation_id = form.cleaned_data.get("watch_operation_id")
            if save_as_new_entry and not watch_operation_id:
                watch_operation_id = uuid4()
            result = related_season.watch(
                episode_number,
                form.cleaned_data.get("end_date"),
                watch_operation_id=watch_operation_id,
                score=form.cleaned_data.get("score"),
                status=form.cleaned_data.get("status") or Status.COMPLETED.value,
                start_date=form.cleaned_data.get("start_date"),
                notes=form.cleaned_data.get("notes") or "",
            )
        except fork_services_episode.EpisodeWatchConflictError as error:
            return HttpResponse(error.code, status=409)
        episode = result.episode
    if episode_instance and hasattr(related_season, "_episode_stats_cache"):
        delattr(related_season, "_episode_stats_cache")
    if episode_instance:
        related_season._sync_status_after_episode_change()
        cache_utils.clear_library_view_caches(
            related_season.user_id,
            media_types=(MediaTypes.EPISODE.value,),
        )

    if request.headers.get("HX-Request"):
        episode_history = list(
            Episode.objects.filter(
                related_season=related_season,
                item__media_id=media_id,
                item__source=source,
                item__episode_number=episode_number,
            )
            .select_related("item", "related_season")
            .order_by("-end_date", "-created_at")
        )
        if not episode_history:
            return HttpResponse("Episode not found", status=404)

        episode.history = episode_history
        episode.collection_entry = (
            CollectionEntry.objects.filter(
                item=episode.item,
                user=request.user,
            )
            .select_related("item")
            .first()
        )

        response = HttpResponse()
        _write_episode_save_oob(
            response,
            request,
            episode=episode,
            related_season=related_season,
            media_id=media_id,
            source=source,
            season_number=season_number,
            episode_number=episode_number,
            next_path=next_path,
        )
        next_watch_operation_id = str(uuid4())
        response["HX-Trigger"] = json.dumps(
            {
                "closeModal": {
                    "watchOperationId": next_watch_operation_id,
                },
                "showToast": {
                    "message": (
                        f"Updated episode {episode_number}."
                        if instance_id
                        else f"Added watch for episode {episode_number}."
                    ),
                    "type": "success",
                },
            },
        )
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response

    return helpers.redirect_back(request)


@require_POST
def episode_drop(request):
    """Mark an episode as dropped — advances progress without adding to watch history."""
    media_id = request.POST["media_id"]
    season_number = int(request.POST["season_number"])
    episode_number = int(request.POST["episode_number"])
    source = request.POST["source"]
    library_media_type = (request.POST.get("library_media_type") or "").strip()

    next_path = request.GET.get("next") or ""
    if source == Sources.TMDB.value and next_path:
        parsed_next_path = urlparse(next_path).path
        path_parts = [segment for segment in parsed_next_path.split("/") if segment]
        if len(path_parts) >= DETAILS_ROUTE_MIN_SEGMENTS and path_parts[0] == "details":
            route_source = path_parts[1]
            if route_source in {choice[0] for choice in Sources.choices}:
                source = route_source

    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=MediaTypes.TV.value,
    )

    try:
        resolve_episode_coordinate(
            media_id,
            source,
            season_number,
            episode_number,
            language=metadata_resolution.metadata_language_default(request.user),
        )
    except InvalidEpisodeCoordinateError:
        cleanup_episode_history_for_route(
            request.user,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=library_media_type,
        )
        return HttpResponse("Episode not found", status=404)

    related_season = fork_services_episode.resolve_or_create_season(
        request.user,
        media_id,
        source,
        season_number,
        library_media_type=library_media_type,
    )

    fork_services_episode.drop_episode(related_season, episode_number)

    if request.headers.get("HX-Request"):
        episode_history = list(
            Episode.objects.filter(
                related_season=related_season,
                item__media_id=media_id,
                item__source=source,
                item__episode_number=episode_number,
            )
            .select_related("item", "related_season")
            .order_by("-end_date", "-created_at")
        )
        if not episode_history:
            return HttpResponse("Episode not found", status=404)

        episode = episode_history[0]
        episode.history = episode_history
        episode.collection_entry = (
            CollectionEntry.objects.filter(
                item=episode.item,
                user=request.user,
            )
            .select_related("item")
            .first()
        )

        response = HttpResponse()
        _write_episode_save_oob(
            response,
            request,
            episode=episode,
            related_season=related_season,
            media_id=media_id,
            source=source,
            season_number=season_number,
            episode_number=episode_number,
            next_path=next_path,
        )
        response["HX-Trigger"] = json.dumps(
            {
                "closeModal": {},
                "showToast": {
                    "message": f"Dropped episode {episode_number}.",
                    "type": "success",
                },
            },
        )
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response

    return helpers.redirect_back(request)


POLL_LOOKBACK_SECONDS = 45  # > poll interval, tolerates jitter/delay
POLL_MAX_GAP_SECONDS = 24 * 60 * 60  # hidden-tab catch-up cap


@require_GET
def episode_history_poll(request, season_id):
    """Push OOB updates for episodes whose history changed since the last poll.

    Plays recorded by webhooks/scrobblers (Plex, generic scrobble API) write
    directly to the DB with no open HTTP response to attach an OOB swap to,
    so the season page polls this endpoint to catch up.
    """
    related_season = get_object_or_404(Season, pk=season_id, user=request.user)
    try:
        gap = min(max(int(request.GET.get("gap", 0)), 0), POLL_MAX_GAP_SECONDS)
    except ValueError:
        gap = 0
    lookback = gap + POLL_LOOKBACK_SECONDS
    since = timezone.now() - timedelta(seconds=lookback)

    recent_item_ids = (
        Episode.objects.filter(related_season=related_season, created_at__gte=since)
        .values_list("item_id", flat=True)
        .distinct()
    )

    response = HttpResponse()
    for item_id in recent_item_ids:
        episode_history = list(
            Episode.objects.filter(related_season=related_season, item_id=item_id)
            .select_related("item", "related_season")
            .order_by("-end_date", "-created_at"),
        )
        if not episode_history:
            continue

        episode = episode_history[0]
        episode.history = episode_history
        episode.collection_entry = (
            CollectionEntry.objects.filter(
                item_id=item_id,
                user=request.user,
            )
            .select_related("item")
            .first()
        )

        for target_suffix in ("", "-list"):
            response.write(
                render_to_string(
                    "app/components/detail_episode_track_button.html",
                    {
                        "episode": episode,
                        "target_suffix": target_suffix,
                        "track_button_oob": True,
                    },
                    request=request,
                ),
            )
        response.write(
            render_to_string(
                "app/components/detail_episode_collection_button.html",
                {
                    "episode": episode,
                    "collection_button_oob": True,
                    "detail_return_url": request.GET.get("return_url", ""),
                },
                request=request,
            ),
        )
        response.write(
            render_to_string(
                "app/components/detail_episode_history_line.html",
                {"episode": episode, "user": request.user, "history_oob": True},
                request=request,
            ),
        )

    response.write(_render_season_progress_oob(related_season))
    response["HX-Trigger-After-Swap"] = json.dumps(
        {
            "detail-progress-updated": {
                "id": related_season.id,
                "tv_id": related_season.related_tv_id,
                "completed": related_season.completed_episode_count,
                "watched_once": len(related_season.watched_once_numbers),
                "rewatching": related_season.is_rewatching,
            },
        },
    )
    response.write(
        _render_track_action_oob(
            request,
            related_season,
            media_url(related_season.item),
            polled=True,
        ),
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@require_POST
def episode_bulk_save(request):
    """Dispatch a bulk episode play range as a background task and return immediately."""
    from app.tasks import bulk_episode_plays_task

    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    fallback_media_type = request.POST.get("library_media_type") or media_type
    discover_tab_cache.mark_active_from_request(
        request,
        fallback_media_type=fallback_media_type,
    )

    start_date_str = (request.POST.get("start_date") or "").strip()
    end_date_str = (request.POST.get("end_date") or "").strip()

    if not start_date_str or not end_date_str:
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=422)
            response["HX-Trigger"] = json.dumps(
                {
                    "showToast": {
                        "message": gettext("Start and end dates are required."),
                        "type": "error",
                    },
                }
            )
            return response
        messages.error(request, gettext("Start and end dates are required."))
        return redirect(helpers.safe_return_url(request, request.POST.get("return_url")))

    try:
        first_season_number = int(request.POST["first_season_number"])
        first_episode_number = int(request.POST["first_episode_number"])
        last_season_number = int(request.POST["last_season_number"])
        last_episode_number = int(request.POST["last_episode_number"])
    except (KeyError, ValueError):
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=422)
            response["HX-Trigger"] = json.dumps(
                {
                    "showToast": {
                        "message": gettext("Invalid episode range."),
                        "type": "error",
                    },
                }
            )
            return response
        messages.error(request, gettext("Invalid episode range."))
        return redirect(helpers.safe_return_url(request, request.POST.get("return_url")))

    episode_count = max(int(request.POST.get("episode_count") or 0), 0)
    write_mode = request.POST.get("write_mode", "add")
    distribution_mode = request.POST.get("distribution_mode", "even")
    identity_media_type = request.POST.get("identity_media_type") or None
    library_media_type = request.POST.get("library_media_type") or None

    task = bulk_episode_plays_task.apply_async(
        kwargs={
            "user_id": request.user.id,
            "media_type": media_type,
            "source": source,
            "media_id": media_id,
            "first_season_number": first_season_number,
            "first_episode_number": first_episode_number,
            "last_season_number": last_season_number,
            "last_episode_number": last_episode_number,
            "write_mode": write_mode,
            "distribution_mode": distribution_mode,
            "start_date_str": start_date_str,
            "end_date_str": end_date_str,
            "identity_media_type": identity_media_type,
            "library_media_type": library_media_type,
        },
        priority=settings.CELERY_TASK_PRIORITY_INTERACTIVE,
    )
    logger.info(
        "bulk_episode_plays_task_dispatched task_id=%s user_id=%d media_id=%s",
        task.id,
        request.user.id,
        media_id,
    )

    if request.headers.get("HX-Request"):
        plural = "s" if episode_count != 1 else ""
        response = HttpResponse(status=204)
        response["HX-Trigger"] = json.dumps(
            {
                "closeModal": {},
                "showToast": {
                    "message": f"Adding plays to {episode_count} episode{plural}.",
                    "type": "info",
                },
            }
        )
        return response

    messages.info(
        request,
        gettext("Adding plays to %(value_1)s episodes.") % {"value_1": episode_count},
    )
    return redirect(helpers.safe_return_url(request, request.POST.get("return_url")))
