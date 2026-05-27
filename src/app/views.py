import logging
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db import IntegrityError
from django.db import models as db_models
from django.db.models import prefetch_related_objects
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.utils.timezone import datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import config, helpers, history_processor
from app import statistics as stats
from app.forms import EpisodeForm, ManualItemForm, get_form_class
from app.models import (
    TV,
    BasicMedia,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
    UserMessage,
)
from app.providers import manual, services, tmdb
from app.templatetags import app_tags
from users.models import (
    DateFormatChoices,
    HomeSortChoices,
    MediaSortChoices,
    MediaStatusChoices,
    User,
)

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress and planning."""
    sort_by = request.user.update_preference("home_sort", request.GET.get("sort"))
    media_type_to_load = request.GET.get("load_media_type")
    status_to_load = request.GET.get("load_status", Status.IN_PROGRESS.value)
    items_limit = 14

    # If this is an HTMX request to load more items for a specific media type
    if request.headers.get("HX-Request") and media_type_to_load:
        list_by_type = BasicMedia.objects.get_home_status(
            user=request.user,
            status=status_to_load,
            sort_by=sort_by,
            items_limit=items_limit,
            specific_media_type=media_type_to_load,
        )
        context = {
            "media_list": list_by_type.get(media_type_to_load, []),
            "home_status": status_to_load,
        }
        return render(request, "app/components/home_grid.html", context)

    home_sections = []
    for status in (Status.IN_PROGRESS.value, Status.PLANNING.value):
        media_types = BasicMedia.objects.get_home_status(
            user=request.user,
            status=status,
            sort_by=sort_by,
            items_limit=items_limit,
        )
        home_sections.append(
            {
                "key": status,
                "id": slugify(status),
                "media_types": media_types,
                "count": sum(
                    media_list["total"] for media_list in media_types.values()
                ),
            },
        )

    context = {
        "home_sections": home_sections,
        "current_sort": sort_by,
        "sort_choices": HomeSortChoices.choices,
        "items_limit": items_limit,
    }
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    operation = request.POST["operation"]

    media = helpers.get_owned_media_or_404(
        request, media_type, instance_id, prefetch=True
    )

    if operation == "increase":
        media.increase_progress()
    elif operation == "decrease":
        media.decrease_progress()

    if media_type == MediaTypes.SEASON.value:
        # clear prefetch cache to get the updated episodes
        media.refresh_from_db()
        prefetch_related_objects([media], "episodes")

    context = {
        "media": media,
    }
    return render(
        request,
        "app/components/progress_changer.html",
        context,
    )


@login_not_required
@require_GET
def media_list(request, username, media_type):
    """Return the media list page."""
    target_user = get_object_or_404(User, username=username)

    # if user is looking at own page then update preferences
    if request.user == target_user:
        layout = target_user.update_preference(
            f"{media_type}_layout",
            request.GET.get("layout"),
        )
        sort_filter = target_user.update_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.update_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )
    else:
        # privacy check then media type check
        if target_user.profile_private:
            msg = "User not found"
            raise Http404(msg)

        enabled_media_types = target_user.get_enabled_media_types()
        if not enabled_media_types:
            msg = "User doesn't have any media types enabled"
            raise Http404(msg)

        if media_type not in enabled_media_types:
            return redirect(
                "medialist",
                username=target_user.username,
                media_type=enabled_media_types[0],
            )

        layout = target_user.get_valid_preference(
            f"{media_type}_layout",
            request.GET.get("layout"),
        )
        sort_filter = target_user.get_valid_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.get_valid_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )

    search_query = request.GET.get("search", "")
    page = request.GET.get("page", 1)

    # Prepare status filter for database query
    if not status_filter:
        status_filter = MediaStatusChoices.ALL

    # Get media list with filters applied
    media_queryset = BasicMedia.objects.get_media_list(
        user=target_user,
        media_type=media_type,
        status_filter=status_filter,
        sort_filter=sort_filter,
        search=search_query,
    )

    # Paginate results
    items_per_page = 32
    paginator = Paginator(media_queryset, items_per_page)
    media_page = paginator.get_page(page)

    BasicMedia.objects.annotate_max_progress(
        media_page.object_list,
        media_type,
    )

    context = {
        "media_type": media_type,
        "media_type_plural": app_tags.media_type_readable_plural(media_type).lower(),
        "media_list": media_page,
        "current_layout": layout,
        "layout_class": ".media-grid" if layout == "grid" else "tbody",
        "current_sort": sort_filter,
        "current_status": status_filter,
        "sort_choices": MediaSortChoices.choices,
        "status_choices": MediaStatusChoices.choices,
        "target_user": target_user,
    }

    # Handle HTMX requests for partial updates
    if request.headers.get("HX-Request"):
        # Filtering from empty list
        if request.headers.get("HX-Target") == "empty_list":
            # If still empty, keep user in the same page
            if not media_page.object_list:
                return HttpResponse(status=204)
            response = HttpResponse()
            response["HX-Redirect"] = reverse(
                "medialist", args=[target_user.username, media_type]
            )
            return response
        if layout == "grid":
            template_name = "app/components/media_grid_items.html"
        else:
            template_name = "app/components/media_table_items.html"
    else:
        template_name = "app/media_list.html"

    return render(request, template_name, context)


@require_GET
def media_search(request):
    """Return the media search page."""
    media_type = request.user.update_preference(
        "last_search_type",
        request.GET["media_type"],
    )
    query = request.GET["q"]
    page = int(request.GET.get("page", 1))
    layout = request.GET.get("layout", "grid")

    # only receives source when searching with secondary source
    source = request.GET.get(
        "source",
        config.get_default_source_name(media_type).value,
    )

    data = services.search(media_type, query, page, source)

    # Enrich search results with user tracking data
    if data.get("results"):
        data["results"] = helpers.enrich_items_with_user_data(
            request, data["results"], "search"
        )

    context = {
        "data": data,
        "source": source,
        "media_type": media_type,
        "layout": layout,
    }

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, source, media_type, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a media item."""
    media_metadata = services.get_media_metadata(media_type, media_id, source)
    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        media_type,
        source,
    )
    current_instance = user_medias[0] if user_medias else None

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item, media_metadata.get("image")
        )

    # Enrich related items with user tracking data
    if media_metadata.get("related"):
        for section_name, related_items in media_metadata["related"].items():
            if related_items:
                media_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request, related_items, section_name
                    )
                )

    if media_type in ["tv", "movie"]:
        watch_providers = tmdb.filter_providers(
            media_metadata.get("providers"), request.user.watch_provider_region
        )
    else:
        watch_providers = None

    context = {
        "media": media_metadata,
        "media_type": media_type,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "watch_providers": watch_providers,
        "watch_provider_region": request.user.watch_provider_region,
        "movable_media_types": MOVABLE_MEDIA_TYPES,
    }
    return render(request, "app/media_details.html", context)


@require_GET
def season_details(request, source, media_id, title, season_number):  # noqa: ARG001 For URL
    """Return the details page for a season."""
    tv_with_seasons_metadata = services.get_media_metadata(
        "tv_with_seasons",
        media_id,
        source,
        [season_number],
    )
    season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        MediaTypes.SEASON.value,
        source,
        season_number=season_number,
    )

    current_instance = user_medias[0] if user_medias else None
    episodes_in_db = current_instance.episodes.all() if current_instance else []

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item, season_metadata.get("image")
        )

    if source == Sources.MANUAL.value:
        season_metadata["episodes"] = manual.process_episodes(
            season_metadata,
            episodes_in_db,
        )
    else:
        season_metadata["episodes"] = tmdb.process_episodes(
            season_metadata,
            episodes_in_db,
        )

    # Enrich related items with user tracking data
    if season_metadata.get("related"):
        for section_name, related_items in season_metadata["related"].items():
            if related_items:
                season_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request,
                        related_items,
                        section_name,
                    )
                )

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "watch_providers": tmdb.filter_providers(
            season_metadata.get("providers"), request.user.watch_provider_region
        ),
        "watch_provider_region": request.user.watch_provider_region,
    }
    return render(request, "app/media_details.html", context)


@require_POST
def update_media_score(request, media_type, instance_id):
    """Update the user's score for a media item."""
    media = helpers.get_owned_media_or_404(request, media_type, instance_id)

    score = float(request.POST.get("score"))
    media.score = score
    media.save()
    logger.info(
        "%s score updated to %s",
        media,
        score,
    )

    return JsonResponse(
        {
            "success": True,
            "score": score,
        },
    )


@require_POST
def sync_metadata(request, source, media_type, media_id, season_number=None):
    """Refresh the metadata for a media item."""
    if source == Sources.MANUAL.value:
        msg = "Manual items cannot be synced."
        messages.error(request, msg)
        return HttpResponse(
            msg,
            status=400,
            headers={"HX-Redirect": request.POST.get("next", "/")},
        )

    cache_key = f"{source}_{media_type}_{media_id}"
    if media_type == MediaTypes.SEASON.value:
        cache_key += f"_{season_number}"

    ttl = cache.ttl(cache_key)
    logger.debug("%s - Cache TTL for: %s", cache_key, ttl)

    if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
        msg = "The data was recently synced, please wait a few seconds."
        messages.error(request, msg)
        logger.error(msg)
    else:
        deleted = cache.delete(cache_key)
        logger.debug("%s - Old cache deleted: %s", cache_key, deleted)

        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.update_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        title = metadata["title"]
        if season_number:
            title += f" - Season {season_number}"

        if media_type == MediaTypes.SEASON.value:
            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )

            # Create a dictionary of existing episodes keyed by episode number
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                )
            }

            episodes_to_update = []
            episode_count = 0

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)
                    episode_count += 1

            logger.info(
                "Found %s existing episodes to update for %s",
                episode_count,
                title,
            )

            if episodes_to_update:
                updated_count = Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )
                logger.info(
                    "Successfully updated %s episodes for %s",
                    updated_count,
                    title,
                )

        item.fetch_releases(delay=False)

        msg = f"{title} was synced to {Sources(source).label} successfully."
        messages.success(request, msg)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            status=204,
            headers={
                "HX-Redirect": request.POST["next"],
            },
        )
    return helpers.redirect_back(request)


@require_GET
def track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
):
    """Return the tracking form for a media item."""
    instance_id = request.GET.get("instance_id")
    if instance_id:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    elif request.GET.get("is_create"):
        media = None
    else:
        # no specific instance, try to find the first one
        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
            season_number=season_number,
        )
        media = user_medias.first()
        if media:
            instance_id = media.id

    initial_data = {
        "media_id": media_id,
        "source": source,
        "media_type": media_type,
        "season_number": season_number,
        "instance_id": instance_id,
    }

    if media:
        title = media.item
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
    else:
        title = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )["title"]
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"

    form = get_form_class(media_type)(instance=media, initial=initial_data)

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
    """Save or update media data to the database."""
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    season_number = request.POST.get("season_number")
    instance_id = request.POST.get("instance_id")

    if instance_id:
        instance = helpers.get_owned_media_or_404(request, media_type, instance_id)
    else:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item, user=request.user)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        logger.info("%s saved successfully.", form.instance)
    else:
        logger.error(form.errors.as_json())
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(
                    request,
                    f"{field.replace('_', ' ').title()}: {error}",
                )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]
    media = helpers.get_owned_media_or_404(request, media_type, instance_id)
    media.delete()
    logger.info("%s deleted successfully.", media)

    return helpers.redirect_back(request)


def _compute_image_hash(image_url, hash_size=8, retries=3):
    """Compute average hash of an image from URL. Returns a list of bits."""
    for attempt in range(1 + retries):
        try:
            response = services.session.get(image_url, timeout=5)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content)).convert("L").resize(
                (hash_size, hash_size), Image.Resampling.LANCZOS
            )
            pixels = list(img.getdata())
            avg = sum(pixels) / len(pixels)
            return [1 if p > avg else 0 for p in pixels]
        except (requests.RequestException, UnidentifiedImageError, OSError):
            if attempt < retries:
                continue
            return None


def _image_similarity(hash1, hash2):
    """Return similarity (0-1) between two image hashes."""
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 0.0
    matching = sum(a == b for a, b in zip(hash1, hash2))
    return matching / len(hash1)


# Media types that support moving (no Season/Episode)
MOVABLE_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
}


def _compute_image_hashes(seasons, search_results):
    """Compute image hashes for seasons and search results in parallel."""
    hash_tasks = {}
    with ThreadPoolExecutor() as executor:
        for season in seasons:
            if season.item.image:
                hash_tasks[("season", season.item.season_number)] = (
                    executor.submit(_compute_image_hash, season.item.image)
                )
        for result in search_results:
            if result.get("image"):
                hash_tasks[("result", str(result["media_id"]))] = (
                    executor.submit(_compute_image_hash, result["image"])
                )

    season_hashes = {}
    result_hashes = {}
    for (kind, key), future in hash_tasks.items():
        if kind == "season":
            season_hashes[key] = future.result()
        else:
            result_hashes[key] = future.result()
    return season_hashes, result_hashes


def _score_result_for_season(result, season_num, season_title, season_hash, result_hashes):
    """Compute a combined similarity score for a search result against a season."""
    title_ratio = SequenceMatcher(
        None, season_title, result["title"].lower()
    ).ratio()

    # Boost results that contain the season number
    season_indicators = [
        f"season {season_num}",
        f"s{season_num}",
        f"part {season_num}",
    ]
    ordinals = {1: "1st", 2: "2nd", 3: "3rd"}
    ordinal = ordinals.get(season_num, f"{season_num}th")
    season_indicators.append(f"{ordinal} season")

    result_lower = result["title"].lower()
    if any(ind in result_lower for ind in season_indicators):
        title_ratio = min(title_ratio + 0.2, 1.0)
    if season_num == 1 and title_ratio > 0.7:
        title_ratio = min(title_ratio + 0.1, 1.0)

    # Image similarity (0-1)
    result_hash = result_hashes.get(str(result["media_id"]))
    img_ratio = _image_similarity(season_hash, result_hash)

    return (title_ratio * 0.3) + (img_ratio * 0.7)


def _compute_season_recommendations(seasons, search_results, season_hashes, result_hashes):
    """Compute best matching search result for each season."""
    recommendations = {}
    for season in seasons:
        season_num = season.item.season_number
        season_title = season.item.title.lower()
        season_hash = season_hashes.get(season_num)
        best_score = 0
        best_id = None
        for result in search_results:
            combined = _score_result_for_season(
                result, season_num, season_title, season_hash, result_hashes
            )
            if combined > best_score:
                best_score = combined
                best_id = str(result["media_id"])
        if best_score >= 0.3:
            recommendations[season_num] = best_id
    return recommendations


@require_GET
def media_move_search(request):
    """Search the target source for matching media to move to."""
    instance_id = request.GET.get("instance_id")
    media_type = request.GET.get("media_type")
    target_type = request.GET.get("target_type")

    if not all([instance_id, media_type, target_type]):
        return HttpResponseBadRequest("Missing required parameters.")

    if media_type not in MOVABLE_MEDIA_TYPES or target_type not in MOVABLE_MEDIA_TYPES:
        return HttpResponseBadRequest("Invalid media type.")

    old_instance = BasicMedia.objects.get_media(request.user, media_type, instance_id)
    query = request.GET.get("query") or old_instance.item.title

    # For TV shows with tracked seasons, show per-season search
    if media_type == MediaTypes.TV.value:
        seasons = (
            old_instance.seasons.select_related("item")
            .exclude(item__season_number=0)
            .order_by("item__season_number")
        )
        if seasons.exists():
            results = services.search(target_type, query, 1)
            search_results = results["results"]

            season_hashes, result_hashes = _compute_image_hashes(
                seasons, search_results
            )
            recommendations = _compute_season_recommendations(
                seasons, search_results, season_hashes, result_hashes
            )

            context = {
                "results": search_results,
                "seasons": seasons,
                "recommendations": recommendations,
                "instance_id": instance_id,
                "media_type": media_type,
                "target_type": target_type,
                "is_tv_move": True,
            }
            return render(
                request, "app/components/move_search_results.html", context
            )

    # Standard search for flat types
    results = services.search(target_type, query, 1)

    context = {
        "results": results["results"],
        "instance_id": instance_id,
        "media_type": media_type,
        "target_type": target_type,
    }
    return render(request, "app/components/move_search_results.html", context)


def _move_tv_seasons(request, old_instance, target_type, target_model):
    """Move individual seasons from a TV show to the target type."""
    seasons = (
        old_instance.seasons.select_related("item")
        .exclude(item__season_number=0)
        .order_by("item__season_number")
    )
    last_item = None
    moved_seasons = []
    for season in seasons:
        season_key = f"target_media_id_{season.item.season_number}"
        target_media_id = request.POST.get(season_key)
        if not target_media_id:
            continue

        source_key = f"target_source_{season.item.season_number}"
        target_source = request.POST.get(source_key, "")

        new_item, _ = Item.objects.get_or_create(
            media_id=target_media_id,
            source=target_source,
            media_type=target_type,
            defaults={
                "title": season.item.title,
                "image": season.item.image,
            },
        )

        if target_model.objects.filter(item=new_item, user=request.user).exists():
            messages.warning(
                request,
                f"Skipped S{season.item.season_number}: \"{new_item.title}\" already in your {MediaTypes(target_type).label} list.",
            )
            continue

        new_instance = target_model(
            item=new_item,
            user=request.user,
            score=season.score,
            status=season.status,
            notes=season.notes,
            progress=season.progress,
            start_date=season.start_date,
            end_date=season.end_date,
        )
        new_instance.save()
        moved_seasons.append(season)
        last_item = new_item

    # Only delete moved seasons; delete TV entry only if all seasons were moved
    if moved_seasons:
        Season.objects.filter(id__in=[s.id for s in moved_seasons]).delete()
        remaining_seasons = old_instance.seasons.exclude(item__season_number=0)
        if not remaining_seasons.exists():
            old_instance.delete()

    return last_item


def _move_to_tv(request, new_item, old_instance):
    """Move a flat media entry to a TV show, creating season and episodes."""
    existing_tv = TV.objects.filter(
        item=new_item, user=request.user
    ).first()
    if existing_tv:
        tv_instance = existing_tv
    else:
        tv_instance = TV(
            item=new_item,
            user=request.user,
            score=old_instance.score,
            status=old_instance.status,
            notes=old_instance.notes,
        )
        db_models.Model.save(tv_instance)

    # Use user-selected season number, or determine next available
    selected_season = request.POST.get("target_season_number")
    if selected_season:
        season_number = int(selected_season)
    else:
        existing_season_nums = list(
            Season.objects.filter(related_tv=tv_instance)
            .values_list("item__season_number", flat=True)
        )
        season_number = max(existing_season_nums, default=0) + 1

    if old_instance.progress:
        season_item, _ = Item.objects.get_or_create(
            media_id=new_item.media_id,
            source=new_item.source,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": new_item.title,
                "image": new_item.image,
            },
        )
        new_season = Season(
            item=season_item,
            user=request.user,
            status=old_instance.status,
            related_tv=tv_instance,
        )
        db_models.Model.save(new_season)

        now = timezone.now().replace(second=0, microsecond=0)
        episodes_to_create = []
        for ep_num in range(1, old_instance.progress + 1):
            ep_item, _ = Item.objects.get_or_create(
                media_id=new_item.media_id,
                source=new_item.source,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=ep_num,
                defaults={
                    "title": new_item.title,
                    "image": new_item.image,
                },
            )
            episodes_to_create.append(Episode(
                related_season=new_season,
                item=ep_item,
                end_date=old_instance.end_date or now,
            ))
        Episode.objects.bulk_create(episodes_to_create)


@require_POST
@transaction.atomic
def media_move(request):
    """Move a media entry from one type to another, preserving tracking data."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]
    target_type = request.POST["target_type"]

    if media_type not in MOVABLE_MEDIA_TYPES or target_type not in MOVABLE_MEDIA_TYPES:
        messages.error(request, "Cannot move this media type.")
        return helpers.redirect_back(request)

    if media_type == target_type:
        return helpers.redirect_back(request)

    old_instance = BasicMedia.objects.get_media(request.user, media_type, instance_id)
    old_item = old_instance.item
    target_model = apps.get_model(app_label="app", model_name=target_type)

    # Handle TV shows with per-season move
    if media_type == MediaTypes.TV.value:
        last_item = _move_tv_seasons(request, old_instance, target_type, target_model)

        logger.info("Moved %s (TV) to %s.", old_item.title, target_type)
        messages.success(
            request,
            f"Moved \"{old_item.title}\" from {MediaTypes(media_type).label} to {MediaTypes(target_type).label}.",
        )

        if last_item:
            return redirect(
                "media_details",
                source=last_item.source,
                media_type=target_type,
                media_id=last_item.media_id,
                title=slugify(last_item.title) or "untitled",
            )
        return helpers.redirect_back(request)

    # Standard move for flat types
    target_media_id = request.POST["target_media_id"]
    target_source = request.POST["target_source"]

    new_item, _ = Item.objects.get_or_create(
        media_id=target_media_id,
        source=target_source,
        media_type=target_type,
        defaults={
            "title": request.POST.get("target_title", old_item.title),
            "image": request.POST.get("target_image", old_item.image),
        },
    )

    if target_type == MediaTypes.TV.value:
        _move_to_tv(request, new_item, old_instance)
    else:
        # Check if the user already has this media tracked in the target type
        if target_model.objects.filter(item=new_item, user=request.user).exists():
            messages.error(
                request,
                f"You already have \"{new_item.title}\" in your {MediaTypes(target_type).label} list.",
            )
            return helpers.redirect_back(request)

        # Create new tracking entry, bypassing save() hooks
        new_instance = target_model(
            item=new_item,
            user=request.user,
            score=old_instance.score,
            status=old_instance.status,
            notes=old_instance.notes,
            progress=old_instance.progress,
            start_date=old_instance.start_date,
            end_date=old_instance.end_date,
        )
        db_models.Model.save(new_instance)

    # Delete the old entry
    old_instance.delete()
    logger.info("Moved %s from %s to %s.", old_item.title, media_type, target_type)
    messages.success(
        request,
        f"Moved \"{old_item.title}\" from {MediaTypes(media_type).label} to {MediaTypes(target_type).label}.",
    )

    return redirect(
        "media_details",
        source=new_item.source,
        media_type=target_type,
        media_id=new_item.media_id,
        title=slugify(new_item.title) or "untitled",
    )


@require_GET
def media_relink_search(request):
    """Search the current media type's source for relinking."""
    instance_id = request.GET["instance_id"]
    media_type = request.GET["media_type"]
    query = request.GET.get("query", "").strip()

    if not query:
        return render(request, "app/components/relink_search_results.html", {"results": []})

    results = services.search(media_type, query, 1)

    context = {
        "results": results["results"],
        "instance_id": instance_id,
        "media_type": media_type,
    }
    return render(request, "app/components/relink_search_results.html", context)


@require_POST
def media_relink(request):
    """Change the linked Item for a tracking entry."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]
    new_media_id = request.POST["new_media_id"]
    new_source = request.POST["new_source"]

    old_instance = BasicMedia.objects.get_media(request.user, media_type, instance_id)

    # Get or create the new Item
    new_item, _ = Item.objects.get_or_create(
        media_id=new_media_id,
        source=new_source,
        media_type=media_type,
        defaults={
            "title": old_instance.item.title,
            "image": old_instance.item.image,
        },
    )

    # Update the tracking entry to point to the new Item
    old_instance.item = new_item
    old_instance.save()

    logger.info("Relinked %s to %s/%s.", old_instance, new_source, new_media_id)
    messages.success(request, "Media link updated successfully.")

    return redirect(
        "media_details",
        source=new_item.source,
        media_type=media_type,
        media_id=new_item.media_id,
        title=slugify(new_item.title) or "untitled",
    )


@require_POST
def mark_user_messages_shown(request):
    """Mark all unseen persistent messages for the user as shown."""
    message_ids = [
        int(message_id)
        for message_id in request.POST.getlist("message_ids")
        if message_id.isdigit()
    ]
    if not message_ids:
        return HttpResponse(status=204)

    UserMessage.objects.filter(
        id__in=message_ids,
        user=request.user,
        shown_at__isnull=True,
    ).update(shown_at=timezone.now())
    return HttpResponse(status=204)


@require_POST
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    media_id = request.POST["media_id"]
    season_number = int(request.POST["season_number"])
    episode_number = int(request.POST["episode_number"])
    source = request.POST["source"]

    form = EpisodeForm(request.POST)
    if not form.is_valid():
        logger.error("Form validation failed: %s", form.errors)
        return HttpResponseBadRequest("Invalid form data")

    try:
        related_season = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__season_number=season_number,
            item__episode_number=None,
            user=request.user,
        )
    except Season.DoesNotExist:
        tv_with_seasons_metadata = services.get_media_metadata(
            "tv_with_seasons",
            media_id,
            source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_with_seasons_metadata["title"],
                "image": season_metadata["image"],
            },
        )
        related_season = Season.objects.create(
            item=item,
            user=request.user,
            score=None,
            status=Status.IN_PROGRESS.value,
            notes="",
        )

        logger.info("%s did not exist, it was created successfully.", related_season)

    related_season.watch(episode_number, form.cleaned_data["end_date"])

    return helpers.redirect_back(request)


@require_http_methods(["GET", "POST"])
def create_entry(request):
    """Return the form for manually adding media items."""
    if request.method == "GET":
        media_types = MediaTypes.values
        return render(request, "app/create_entry.html", {"media_types": media_types})

    # Process the form submission
    form = ManualItemForm(request.POST, user=request.user)
    if not form.is_valid():
        # Handle form validation errors
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
        return redirect("create_entry")

    # Try to save the item
    try:
        item = form.save()
    except IntegrityError:
        # Handle duplicate item
        media_name = form.cleaned_data["title"]
        if form.cleaned_data.get("season_number"):
            media_name += f" - Season {form.cleaned_data['season_number']}"
        if form.cleaned_data.get("episode_number"):
            media_name += f" - Episode {form.cleaned_data['episode_number']}"

        logger.exception("%s already exists in the database.", media_name)
        messages.error(request, f"{media_name} already exists in the database.")
        return redirect("create_entry")

    # Prepare and validate the media form
    updated_request = request.POST.copy()
    updated_request.update({"source": item.source, "media_id": item.media_id})
    media_form = get_form_class(item.media_type)(updated_request)

    if not media_form.is_valid():
        # Handle media form validation errors
        logger.error(media_form.errors.as_json())
        helpers.form_error_messages(media_form, request)

        # Delete the item since the media creation failed
        item.delete()
        logger.info("%s was deleted due to media form validation failure", item)
        return redirect("create_entry")

    # Save the media instance
    media_form.instance.user = request.user
    media_form.instance.item = item

    # Handle relationships based on media type
    if item.media_type == MediaTypes.SEASON.value:
        media_form.instance.related_tv = form.cleaned_data["parent_tv"]
    elif item.media_type == MediaTypes.EPISODE.value:
        media_form.instance.related_season = form.cleaned_data["parent_season"]

    media_form.save()

    # Success message
    msg = f"{item} added successfully."
    messages.success(request, msg)
    logger.info(msg)

    return redirect("create_entry")


@require_GET
def search_parent_tv(request):
    """Return the search results for parent TV shows."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for TV shows with query: %s",
        request.user.username,
        query,
    )

    parent_tvs = TV.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.TV.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_tv.html",
        {"results": parent_tvs, "query": query},
    )


@require_GET
def search_parent_season(request):
    """Return the search results for parent seasons."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for seasons with query: %s",
        request.user.username,
        query,
    )

    parent_seasons = Season.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.SEASON.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_season.html",
        {"results": parent_seasons, "query": query},
    )


@require_GET
def history_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the history page for a media item."""
    user_medias = BasicMedia.objects.filter_media(
        request.user,
        media_id,
        media_type,
        source,
        season_number=season_number,
        episode_number=episode_number,
    )

    total_medias = user_medias.count()
    timeline_entries = []
    for index, media in enumerate(user_medias, start=1):
        if history := media.history.all():
            media_entry_number = total_medias - index + 1
            timeline_entries.extend(
                history_processor.process_history_entries(
                    history,
                    media_type,
                    media_entry_number,
                    request.user,
                ),
            )
    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "timeline": timeline_entries,
            "total_medias": total_medias,
            "return_url": request.GET["return_url"],
        },
    )


@require_http_methods(["DELETE"])
def delete_history_record(request, media_type, history_id):
    """Delete a specific history record."""
    try:
        historical_model = apps.get_model(
            app_label="app",
            model_name=f"historical{media_type.lower()}",
        )

        historical_model.objects.get(
            history_id=history_id,
            history_user=request.user,
        ).delete()

        logger.info(
            "Deleted history record %s",
            str(history_id),
        )

        # Return empty 200 response - the element will be removed by HTMX
        return HttpResponse()

    except historical_model.DoesNotExist:
        logger.exception(
            "History record %s not found for user %s",
            str(history_id),
            str(request.user),
        )
        return HttpResponse("Record not found", status=404)


@require_GET
def statistics(request):
    """Return the statistics page."""
    # Set default date range to last year
    timeformat = "%Y-%m-%d"
    today = timezone.localdate()
    one_year_ago = today.replace(year=today.year - 1)

    # Get date parameters with defaults
    start_date_str = request.GET.get("start-date") or one_year_ago.strftime(timeformat)
    end_date_str = request.GET.get("end-date") or today.strftime(timeformat)

    if start_date_str == "all" and end_date_str == "all":
        start_date = None
        end_date = None
    else:
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if start_date and end_date:
            # Convert to datetime with timezone awareness
            start_date = timezone.make_aware(
                datetime.combine(start_date, datetime.min.time()),
            )

            # End date should be end of day
            end_date = timezone.make_aware(
                datetime.combine(end_date, datetime.max.time()),
            )

    # Get all user media data in a single operation
    user_media, media_count = stats.get_user_media(
        request.user,
        start_date,
        end_date,
    )

    # Calculate all statistics from the retrieved data
    media_type_distribution = stats.get_media_type_distribution(
        media_count,
    )
    score_distribution, top_rated = stats.get_score_distribution(user_media)
    status_distribution = stats.get_status_distribution(user_media)
    status_pie_chart_data = stats.get_status_pie_chart_data(
        status_distribution,
    )
    timeline = stats.get_timeline(user_media)

    activity_data = stats.get_activity_data(request.user, start_date, end_date)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "media_count": media_count,
        "activity_data": activity_data,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution,
        "top_rated": top_rated,
        "status_distribution": status_distribution,
        "status_pie_chart_data": status_pie_chart_data,
        "timeline": timeline,
        "date_format_values": DateFormatChoices.values,
    }

    return render(request, "app/statistics.html", context)


@require_GET
def service_worker():
    """Serve the service worker file."""
    sw_path = Path(settings.STATICFILES_DIRS[0]) / "js" / "serviceworker.js"
    with sw_path.open() as f:
        response = HttpResponse(f.read(), content_type="application/javascript")
        response["Service-Worker-Allowed"] = "/"
        return response
