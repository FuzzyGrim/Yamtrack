import logging

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import prefetch_related_objects
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, Http404
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import helpers, history_processor
from app import statistics as stats
from app.forms import EpisodeForm, ManualItemForm, get_form_class, BookProgressForm, BookLogForm, BookStartReadingForm
from app.models import TV, BasicMedia, Item, MediaTypes, Season, Sources, Status, Movie, Episode, Book, BookSession

from app.providers import manual, mdblist, services, tmdb
from app.templatetags import app_tags
from users.models import HomeSortChoices, MediaSortChoices, MediaStatusChoices
from app.forms import DiaryEntryForm
from app.models import DiaryEntry
from app.utils.color import (
    build_accent_palette,
    compute_and_store_poster_accent,
    get_poster_accent_from_url,
)

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress."""
    sort_by = request.user.update_preference("home_sort", request.GET.get("sort"))
    media_type_to_load = request.GET.get("load_media_type")
    items_limit = 14

    list_by_type = BasicMedia.objects.get_in_progress(
        request.user,
        sort_by,
        items_limit,
        media_type_to_load,
    )

    # If this is an HTMX request to load more items for a specific media type
    if request.headers.get("HX-Request") and media_type_to_load:
        context = {
            "media_list": list_by_type.get(media_type_to_load, []),
        }
        return render(request, "app/components/home_grid.html", context)

    context = {
        "list_by_type": list_by_type,
        "current_sort": sort_by,
        "sort_choices": HomeSortChoices.choices,
        "items_limit": items_limit,
    }
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    operation = request.POST["operation"]

    media = BasicMedia.objects.get_media_prefetch(
        request.user,
        media_type,
        instance_id,
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


@require_GET
def media_list(request, media_type):
    """Return the media list page."""
    layout = request.user.update_preference(
        f"{media_type}_layout",
        request.GET.get("layout"),
    )
    sort_filter = request.user.update_preference(
        f"{media_type}_sort",
        request.GET.get("sort"),
    )
    status_filter = request.user.update_preference(
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
        user=request.user,
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
    }

    # Handle HTMX requests for partial updates
    if request.headers.get("HX-Request"):
        # Changing from empty list to a status with items
        if request.headers.get("HX-Target") == "empty_list":
            response = HttpResponse()
            response["HX-Redirect"] = reverse("medialist", args=[media_type])
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
    source = request.GET.get("source")

    data = services.search(media_type, query, page, source)

    context = {
        "data": data,
        "source": source,
        "media_type": media_type,
        "layout": layout,
    }

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, source, media_type, media_id, title):
    """Return the details page for a media item."""
    media_metadata = services.get_media_metadata(media_type, media_id, source)

    poster_accent = None
    accent_item = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=media_type,
    ).first()
    if accent_item:
        poster_accent = compute_and_store_poster_accent(
            accent_item,
            poster_url=media_metadata.get("image"),
        )
    else:
        poster_accent = get_poster_accent_from_url(media_metadata.get("image"))

    accent_palette = build_accent_palette(poster_accent)
    poster_accent = accent_palette["accent"]
    poster_accent_contrast = accent_palette["contrast"]

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        media_type,
        source,
    )
    current_instance = user_medias[0] if user_medias else None

    # Get diary entries for this media (movies, TV, and books)
    diary_entries = []
    if media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.BOOK.value]:
        try:
            item = Item.objects.get(source=source, media_type=media_type, media_id=media_id)
            diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).prefetch_related('tags').order_by('-consumed_at')
        except Item.DoesNotExist:
            pass

    # Get MDBList ratings for movies and TV shows from TMDB
    mdblist_ratings = None
    if source == Sources.TMDB.value and media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        try:
            mdblist_ratings = mdblist.get_media_ratings(media_id, media_type)
            logging.getLogger(__name__).info(f"MDBList ratings for {media_type} {media_id}: {mdblist_ratings}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to fetch MDBList ratings for {media_type} {media_id}: {e}")

    # Check if there are any seasons in progress for TV shows
    has_seasons_in_progress = False
    if media_type == MediaTypes.TV.value and current_instance:
        # Use prefetched seasons to avoid N+1 query
        has_seasons_in_progress = any(season.status == Status.IN_PROGRESS.value for season in current_instance.seasons.all())

    context = {
        "media": media_metadata,
        "media_type": media_type,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "diary_entries": diary_entries,
        "mdblist_ratings": mdblist_ratings,
        "poster_accent_color": poster_accent,
        "poster_accent_contrast": poster_accent_contrast,
        "has_seasons_in_progress": has_seasons_in_progress,
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

    poster_accent = None
    # First check if there's a season item with custom poster
    season_item = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
    ).first()
    
    if season_item:
        # Use the season item's image (which may be custom) for accent color
        poster_accent = compute_and_store_poster_accent(
            season_item,
            poster_url=season_item.image,
        )
    else:
        # Fall back to TV show item
        accent_item = Item.objects.filter(
            media_id=media_id,
            source=source,
            media_type=MediaTypes.TV.value,
        ).first()
        if accent_item:
            poster_accent = compute_and_store_poster_accent(
                accent_item,
                poster_url=season_metadata.get("image"),
            )
        else:
            poster_accent = get_poster_accent_from_url(season_metadata.get("image"))

    accent_palette = build_accent_palette(poster_accent)
    poster_accent = accent_palette["accent"]
    poster_accent_contrast = accent_palette["contrast"]

    # Update season metadata with custom poster if available
    if season_item:
        season_metadata["image"] = season_item.image

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        MediaTypes.SEASON.value,
        source,
        season_number=season_number,
    )

    current_instance = user_medias[0] if user_medias else None
    episodes_in_db = current_instance.episodes.all() if current_instance else []

    # Get diary entries for this season
    diary_entries = []
    try:
        item = Item.objects.get(source=source, media_type=MediaTypes.SEASON.value, media_id=media_id, season_number=season_number)
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).prefetch_related('tags').order_by('-consumed_at')
    except Item.DoesNotExist:
        pass

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

    # Get MDBList ratings for seasons from TMDB (use TV show ratings)
    mdblist_ratings = None
    if source == Sources.TMDB.value:
        try:
            # Get TV show ratings for seasons (seasons don't have separate ratings)
            mdblist_ratings = mdblist.get_media_ratings(media_id, "tv")
            logging.getLogger(__name__).info(f"MDBList TV ratings for season {media_id}: {mdblist_ratings}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to fetch MDBList ratings for season {media_id}: {e}")

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "episodes_in_db": episodes_in_db,
        "diary_entries": diary_entries,
        "mdblist_ratings": mdblist_ratings,
        "poster_accent_color": poster_accent,
        "poster_accent_contrast": poster_accent_contrast,
    }
    return render(request, "app/media_details.html", context)


@require_POST
def update_media_score(request, media_type, instance_id):
    """Update the user's score for a media item."""
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )

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
        instance = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
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

    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    if media:
        media.delete()
        logger.info("%s deleted successfully.", media)
    else:
        logger.warning("The %s was already deleted before.", media_type)

    return helpers.redirect_back(request)


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
    }

    return render(request, "app/statistics.html", context)


@require_GET
def hof_search(request):
    """Return search results for Hall of Fame selection."""
    try:
        media_type = request.GET["media_type"]  # Use square brackets like working search
        query = request.GET["q"]  # Use square brackets like working search
    except KeyError as e:
        print(f"HOF Search - Missing parameter: {e}")
        print(f"HOF Search - Available params: {list(request.GET.keys())}")
        return render(request, "app/components/hof_search_results.html", {
            "data": None,
            "query": "",
            "media_type": ""
        })
    
    page = int(request.GET.get("page", 1))
    source = request.GET.get("source")

    # Debug logging
    print(f"HOF Search - Media Type: '{media_type}', Query: '{query}', Page: {page}")

    # Don't search if query is too short
    if len(query.strip()) <= 1:
        print("HOF Search - Query too short")
        return render(request, "app/components/hof_search_results.html", {
            "data": None,
            "query": query,
            "media_type": media_type
        })

    try:
        # Use exact same call as working search
        data = services.search(media_type, query, page, source)
        print(f"HOF Search - Success: {len(data.get('items', []))} items found")
        print(f"HOF Search - Data keys: {list(data.keys()) if data else 'None'}")
    except Exception as e:
        print(f"HOF Search - Error: {e}")
        import traceback
        traceback.print_exc()
        data = None

    return render(request, "app/components/hof_search_results.html", {
        "data": data,
        "source": source,
        "media_type": media_type,
        "query": query
    })

@require_POST
def toggle_hof(request):
    """Toggle an item's Hall of Fame status."""
    print(f"HOF Toggle - POST data: {request.POST}")
    print(f"HOF Toggle - User: {request.user}")
    
    try:
        media_type = request.POST["media_type"]
        media_id = request.POST["media_id"]
        source = request.POST["source"]
        
        print(f"HOF Toggle - Media Type: {media_type}, ID: {media_id}, Source: {source}")
        
        # Get metadata for the media item
        metadata = services.get_media_metadata(media_type, media_id, source)
        print(f"HOF Toggle - Metadata: {metadata.get('title', 'No title')}")
        
        # Create or get the Item instance
        item, created = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
        },
        )
        
        # Toggle the HOF status
        user = request.user
        hof_field = f"hof_{media_type}"
        
        if hasattr(user, hof_field):
            current_hof_item = getattr(user, hof_field)
            if current_hof_item == item:
                # Remove from hall of fame
                setattr(user, hof_field, None)
                user.save()
                added = False
            else:
                # Add to hall of fame
                setattr(user, hof_field, item)
                user.save()
                added = True
            
            # Trigger HTMX update
            from django.template.loader import render_to_string
            from django.http import HttpResponse
            
            # Return a response that triggers the hofUpdated event
            response = HttpResponse()
            response['HX-Trigger'] = 'hofUpdated'
            return response
    except Exception as e:
        print(f"HOF Toggle - Error: {e}")
        return JsonResponse({"error": str(e)}, status=400)


@require_GET
def book_cover_selection_modal(request, source, media_id):
    """Return the cover selection modal with available book covers."""
    media_type = MediaTypes.BOOK.value
    
    try:
        # Get or create the item
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
        except Item.DoesNotExist:
            # Item doesn't exist, so we need to create it
            from app.providers import services
            metadata = services.get_media_metadata(media_type, media_id, source)
            
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                title=metadata["title"],
                image=metadata["image"],
            )
        
        # Get ISBNs from the appropriate provider
        if source == Sources.HARDCOVER.value:
            from app.providers import hardcover
            isbns = hardcover.get_book_isbns(media_id)
        elif source == Sources.OPENLIBRARY.value:
            from app.providers import openlibrary
            # Get book metadata to extract ISBNs
            metadata = services.get_media_metadata(media_type, media_id, source)
            isbns = metadata.get("details", {}).get("isbn", []) or []
        else:
            isbns = []
        
        # Get reliable covers (editions with cover ids; backfill via Books API)
        import asyncio
        from app.providers import openlibrary
        covers_list = []
        if source == Sources.OPENLIBRARY.value:
            try:
                covers_list = asyncio.run(openlibrary.get_reliable_covers_for_book(media_id, isbns, cap=20))
            except Exception as e:
                logger.warning("Reliable cover fetch failed, falling back to ISBN covers: %s", e)
                # Fallback to basic ISBN-based covers
                covers_list = openlibrary.get_book_cover_images(isbns)
        else:
            # For non-Open Library sources, resolve work from ISBNs to fetch editions covers
            try:
                covers_list = asyncio.run(openlibrary.get_reliable_covers_by_isbns(isbns, cap=20))
            except Exception as e:
                logger.warning("ISBN->work reliable cover fetch failed, falling back to ISBN covers: %s", e)
                covers_list = openlibrary.get_book_cover_images(isbns)
        
        # Create the original cover entry
        original_cover = {
            "url": item.image,
            "thumbnail_url": item.image,
            "isbn": isbns[0] if isbns else "N/A",
            "width": 0,
            "height": 0,
            "aspect_ratio": 0.667,
            "language": None,
            "is_current": True,
            "is_original": True
        }
        
        # Combine original with fetched covers, ensuring original is first
        # and not duplicated if it's already in the results
        covers = [original_cover]
        for cover in covers_list:
            if cover["url"] != item.image:
                covers.append(cover)

        try:
            # Telemetry: sizes
            logger.info(
                "Book cover modal: isbns=%d final_covers=%d source=%s media_id=%s",
                len(isbns),
                len(covers),
                source,
                media_id,
            )
        except Exception:
            pass
        
        # Get current custom cover if exists
        from app.models import CustomPosterPreference
        try:
            current_preference = CustomPosterPreference.objects.get(
                user=request.user,
                item=item
            )
            current_cover = current_preference.custom_image_url
        except CustomPosterPreference.DoesNotExist:
            current_cover = item.image
            
        context = {
            "item": item,
            "posters": covers,  # Reuse 'posters' variable name for template compatibility
            "current_poster": current_cover,
            "is_book": True,
        }
        
        return render(request, "app/components/poster_selection_modal.html", context)
        
    except Exception as e:
        logger.error("Error in book cover selection modal: %s", e)
        return HttpResponseBadRequest("Error loading book covers")


@require_GET
def poster_selection_modal(request, media_type, media_id, source):
    """Return the poster selection modal with available posters."""
    if source != Sources.TMDB.value or media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        return HttpResponseBadRequest("Poster selection only available for TMDB movies and TV shows")
        
    try:
        # Get or create the item
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
        except Item.DoesNotExist:
            # Item doesn't exist, so we need to create it
            # First get the metadata from TMDB
            from app.providers import services
            metadata = services.get_media_metadata(media_type, media_id, source)
            
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                title=metadata["title"],
                image=metadata["image"],
            )
        
        # Get available posters from TMDB
        tmdb_posters = tmdb.get_poster_images(media_id, "movie" if media_type == MediaTypes.MOVIE.value else "tv")
        
        # Create the original poster entry
        original_poster = {
            "url": item.image,
            "thumbnail_url": item.image,
            "width": 0,
            "height": 0,
            "aspect_ratio": 0.667,
            "vote_average": 0,
            "vote_count": 0,
            "language": None,
            "is_current": True
        }
        
        # Combine original with TMDB posters, ensuring original is first
        # and not duplicated if it's already in the TMDB results
        posters = [original_poster]
        for poster in tmdb_posters:
            if poster["url"] != item.image:
                # Ensure language is None instead of undefined for consistency
                poster_copy = poster.copy()
                if poster_copy.get("language") is None:
                    poster_copy["language"] = None
                posters.append(poster_copy)
        
        # Get current custom poster if exists
        from app.models import CustomPosterPreference
        try:
            current_preference = CustomPosterPreference.objects.get(
                user=request.user,
                item=item
            )
            current_poster = current_preference.custom_image_url
        except CustomPosterPreference.DoesNotExist:
            current_poster = item.image
            
        context = {
            "item": item,
            "posters": posters,
            "current_poster": current_poster,
        }
        
        return render(request, "app/components/poster_selection_modal.html", context)
        
    except Exception as e:
        logger.error("Error in poster selection modal: %s", e)
        return HttpResponseBadRequest("Error loading posters")


@require_GET
def season_poster_selection_modal(request, source, media_id, season_number):
    """Return the poster selection modal with available posters for a season."""
    if source != Sources.TMDB.value:
        return HttpResponseBadRequest("Poster selection only available for TMDB seasons")
        
    try:
        # Get or create the season item
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
            )
        except Item.DoesNotExist:
            # Item doesn't exist, so we need to create it
            # First get the metadata from TMDB
            from app.providers import services
            metadata = services.get_media_metadata(
                "tv_with_seasons", 
                media_id, 
                source, 
                season_numbers=[season_number]
            )
            season_metadata = metadata[f"season/{season_number}"]
            
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                title=season_metadata["title"],
                image=season_metadata["image"],
                season_number=season_number,
            )
        
        # Get available posters from TMDB for the season
        tmdb_posters = tmdb.get_poster_images(media_id, "season", season_number)
        
        # Create the original poster entry
        original_poster = {
            "url": item.image,
            "thumbnail_url": item.image,
            "width": 0,
            "height": 0,
            "aspect_ratio": 0.667,
            "vote_average": 0,
            "vote_count": 0,
            "language": None,
            "is_current": True
        }
        
        # Combine original with TMDB posters, ensuring original is first
        # and not duplicated if it's already in the TMDB results
        posters = [original_poster]
        for poster in tmdb_posters:
            if poster["url"] != item.image:
                # Ensure language is None instead of undefined for consistency
                poster_copy = poster.copy()
                if poster_copy.get("language") is None:
                    poster_copy["language"] = None
                posters.append(poster_copy)
        
        # Get current custom poster if exists
        from app.models import CustomPosterPreference
        try:
            current_preference = CustomPosterPreference.objects.get(
                user=request.user,
                item=item
            )
            current_poster = current_preference.custom_image_url
        except CustomPosterPreference.DoesNotExist:
            current_poster = item.image
            
        context = {
            "item": item,
            "posters": posters,
            "current_poster": current_poster,
        }
        
        return render(request, "app/components/poster_selection_modal.html", context)
        
    except Exception as e:
        logger.error("Error in season poster selection modal: %s", e)
        return HttpResponseBadRequest("Error loading season posters")


@require_POST
def save_poster_preference(request):
    """Save the user's poster preference for an item."""
    try:
        media_type = request.POST["media_type"]
        media_id = request.POST["media_id"]
        source = request.POST["source"]
        custom_image_url = request.POST["poster_url"]
        
        # Handle season items differently
        if media_type == MediaTypes.SEASON.value:
            season_number = request.POST.get("season_number")
            if not season_number:
                return JsonResponse({"error": "Season number required for season items"}, status=400)
            
            # Get or create the season item
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
                season_number=season_number,
            )
        else:
            # Get or create the item
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
        
        # Update or create the preference
        from app.models import CustomPosterPreference

        accent = compute_and_store_poster_accent(
            item,
            poster_url=custom_image_url,
            force=True,
        )
        palette = build_accent_palette(accent)

        CustomPosterPreference.objects.update_or_create(
            user=request.user,
            item=item,
            defaults={"custom_image_url": custom_image_url}
        )

        item.image = custom_image_url
        item.poster_accent_color = palette["accent"]
        item.save(update_fields=["image", "poster_accent_color"])

        return JsonResponse(
            {
                "success": True,
                "poster_url": custom_image_url,
                "accent": palette["accent"],
                "contrast": palette["contrast"],
        }
        )
        
    except Item.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except Exception as e:
        logger.error("Error saving poster preference: %s", e)
        return JsonResponse({"error": str(e)}, status=400)


@require_POST
def mark_consumed(request, media_type, instance_id):
    """Mark a media item as consumed."""
    if media_type != MediaTypes.MOVIE.value:
        raise Http404("Mark as consumed is only available for movies")
        
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    
    from app.services import mark_consumed as mark_consumed_service
    mark_consumed_service(request.user, media)
    
    # Return fragment for HTMX to swap
    context = {
        "media": media,
        "media_type": media_type,
    }
    return render(request, "app/components/media_actions.html", context)


@require_POST
def add_diary_entry(request, media_type, instance_id):
    """Create a new diary entry."""
    if media_type != MediaTypes.MOVIE.value:
        raise Http404("Diary entries are only available for movies")
        
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    
    form = DiaryEntryForm(
        request.POST,
        user=request.user,
        item=media.item,
    )
    
    if form.is_valid():
        from app.services import create_diary_entry
        entry = create_diary_entry(
            user=request.user,
            item=media.item,
            consumed_at=form.cleaned_data["consumed_at"],
            rating=form.cleaned_data["rating"],
            review=form.cleaned_data["review"],
            auto_mark_consumed=request.POST.get("auto_mark_consumed") == "true",
            tags=form.cleaned_data.get("tags", []),
        )
        
        if request.headers.get("HX-Request"):
            # Return fragment for modal to close and actions to refresh
            context = {
                "media": media,
                "media_type": media_type,
                "entry": entry,
        }
            return render(request, "app/components/media_actions.html", context)
        
        return redirect("diary_item", media_type=media_type, instance_id=instance_id)
    
    # If form invalid, return the form with errors
    context = {
        "form": form,
        "media": media,
        "media_type": media_type,
    }
    return render(request, "app/components/diary_form.html", context)


@require_GET
def diary_list(request):
    """Show user's diary entries."""
    # Get filters from query params
    media_type = request.GET.get("media_type", "")
    year = request.GET.get("year")
    item_id = request.GET.get("item_id")
    page = request.GET.get("page", 1)
    
    # Base queryset - order by created_at descending (newest first) to reflect actual logging order
    entries = DiaryEntry.objects.filter(user=request.user).select_related('item').prefetch_related('tags').order_by('-created_at')
    
    # Apply filters
    if media_type:
        entries = entries.filter(item__media_type=media_type)
    
    if item_id:
        try:
            item_id = int(item_id)
            entries = entries.filter(item__id=item_id)
        except ValueError:
            pass
    
    if year:
        try:
            year = int(year)
            entries = entries.filter(consumed_at__year=year)
        except ValueError:
            pass
    
    # Get unique years for filter dropdown
    years = entries.dates("consumed_at", "year", order="DESC")
    
    # Paginate
    paginator = Paginator(entries, 25)  # 25 entries per page
    page_obj = paginator.get_page(page)
    
    context = {
        "entries": page_obj,
        "years": years,
        "current_year": year,
        "current_media_type": media_type,
        "media_type_choices": MediaTypes.choices,
    }
    
    if request.headers.get("HX-Request"):
        return render(request, "app/components/diary_entries.html", context)
    
    return render(request, "app/diary.html", context)


@require_GET
def diary_item(request, media_type, instance_id):
    """Show diary entries for a specific item."""
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    
    entries = DiaryEntry.objects.filter(
        user=request.user,
        item=media.item,
    ).prefetch_related('tags').order_by("-consumed_at")
    
    context = {
        "media": media,
        "media_type": media_type,
        "entries": entries,
    }
    
    if request.headers.get("HX-Request"):
        return render(request, "app/components/diary_item_entries.html", context)
    
    return render(request, "app/diary_item.html", context)


from datetime import date

def log_modal(request, source, media_type, media_id, season_number=None):
    """Show the log entry modal."""
    if media_type not in [
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.BOOK.value,
    ]:
        raise Http404("Logging is only available for movies, TV shows, seasons, and books")
        
    # Get or create the item - fetch metadata if it doesn't exist
    try:
        if media_type == MediaTypes.SEASON.value and season_number is not None:
            item = Item.objects.get(
                source=source, 
                media_type=media_type, 
                media_id=media_id, 
                season_number=season_number
            )
        else:
            item = Item.objects.get(source=source, media_type=media_type, media_id=media_id)
    except Item.DoesNotExist:
        # Fetch metadata and create the item
        if media_type == MediaTypes.SEASON.value and season_number is not None:
            # For seasons, we need to get the TV show metadata first
            tv_metadata = services.get_media_metadata("tv_with_seasons", media_id, source, [season_number])
            season_metadata = tv_metadata[f"season/{season_number}"]
            item, _ = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                season_number=season_number,
                defaults={
                    "title": season_metadata["title"],
                    "image": season_metadata["image"],
        },
            )
        else:
            metadata = services.get_media_metadata(media_type, media_id, source)
            defaults = {
                "title": metadata["title"],
                "image": metadata["image"],
            }
            if media_type == MediaTypes.BOOK.value:
                defaults["total_pages"] = metadata.get("total_pages") or metadata.get("max_progress")
            item, created = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults=defaults,
            )
            if (
                media_type == MediaTypes.BOOK.value
                and not created
                and defaults.get("total_pages")
                and item.total_pages != defaults["total_pages"]
            ):
                item.total_pages = defaults["total_pages"]
                item.save(update_fields=["total_pages"])
        
    # Use the same template for both movies and TV shows
    return render(request, 'app/components/log_modal.html', {
        'item': item,
        'user': request.user,
        'today': date.today(),
        'book_completion': request.GET.get('book_complete') == '1',
    })


@require_POST
def mark_movie_watched(request, source, media_id):
    """Mark a movie as watched by creating a tracking instance and marking it as consumed."""
    media_type = MediaTypes.MOVIE.value
        
    # Get or create the item
    metadata = services.get_media_metadata(media_type, media_id, source)
    item, _ = Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )
    
    # Get or create the media instance
    from app.models import Movie
    media_instance, created = Movie.objects.get_or_create(
        item=item,
        user=request.user,
        defaults={
            "status": Status.COMPLETED.value,
        }
    )
    
    if not created:
        # If it already exists, mark it as consumed
        media_instance.mark_consumed()
    
    # Get diary entries for this media
    diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
    
    # Return updated action buttons for HTMX to swap  
    # Add required fields to metadata for template compatibility
    metadata["media_type"] = media_type
    metadata["source"] = source
    metadata["media_id"] = media_id
    context = {
        "media": metadata,
        "media_type": media_type,
        "current_instance": media_instance,
        "diary_entries": diary_entries,
    }
    return render(request, "app/components/media_actions.html", context)


@require_POST
def unmark_movie_watched(request, source, media_id):
    """Unmark a movie as watched by removing the tracking instance."""
    media_type = MediaTypes.MOVIE.value
        
    try:
        # Get the item
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type
        )
        
        # Get the media instance
        from app.models import Movie
        media_instance = Movie.objects.get(
            item=item,
            user=request.user
        )
        
        # Delete the media instance to "unwatch" it
        media_instance.delete()
        
        # Get metadata for template
        metadata = services.get_media_metadata(media_type, media_id, source)
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": None,  # No instance after unwatching
            "diary_entries": diary_entries,
        }
        return render(request, "app/components/media_actions.html", context)
        
    except (Item.DoesNotExist, Movie.DoesNotExist):
        # If the item or media instance doesn't exist, return the unwatched state
        metadata = services.get_media_metadata(media_type, media_id, source)
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": None,
        }
        return render(request, "app/components/media_actions.html", context)


@require_POST
def mark_tv_watched(request, source, media_type, media_id):
    """Mark a TV show as watched by creating a tracking instance and marking all episodes as consumed."""
    try:
        if media_type != MediaTypes.TV.value:
            raise Http404("Mark as watched is only available for TV shows")
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                
        },
        )
        
        # Get or create the TV instance
        from app.models import TV
        # Temporarily disable calendar triggers to avoid events.tasks error
        item._disable_calendar_triggers = True
        tv_instance, created = TV.objects.get_or_create(
            item=item,
            user=request.user,
            defaults={
                "status": Status.COMPLETED.value,
                
        }
        )
        
        if not created:
            # If it already exists, just update the status (TV shows don't have direct end_date)
            tv_instance.status = Status.COMPLETED.value
            tv_instance.save()
        
        # Mark all episodes and seasons as watched
        try:
            tv_instance._completed()  # This creates all seasons and episodes and marks them as watched
        except Exception as e:
            # Log the error but don't fail the request
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in _completed() for TV {media_id}: {e}")
            # Continue without marking episodes as watched
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Check if there are any seasons in progress
        has_seasons_in_progress = False
        if tv_instance:
            # Use prefetched seasons to avoid N+1 query
            has_seasons_in_progress = any(season.status == Status.IN_PROGRESS.value for season in tv_instance.seasons.all())
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": tv_instance,
            "diary_entries": diary_entries,
            "has_seasons_in_progress": has_seasons_in_progress,
            
        }
        
        # Add notification headers
        response = render(request, "app/components/media_actions.html", context)
        season_count = len(metadata.get("related", {}).get("seasons", []))
        response["X-Notification-Message"] = f"Entire show marked as watched (all {season_count} seasons and episodes)"
        response["X-Notification-Type"] = "success"
        return response
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in mark_tv_watched for {media_id}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Return a simple error response
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def unmark_tv_watched(request, source, media_type, media_id):
    """Unmark a TV show as watched by removing the tracking instance and all episodes."""
    if media_type != MediaTypes.TV.value:
        raise Http404("Unwatch is only available for TV shows")
        
    try:
        # Get the item
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type
        )
        
        # Get the TV instance
        from app.models import TV
        tv_instance = TV.objects.get(
            item=item,
            user=request.user
        )
        
        # Delete all related episodes and seasons first
        for season in tv_instance.seasons.all():
            season.episodes.all().delete()
            season.delete()
        
        # Delete the TV instance to "unwatch" it
        tv_instance.delete()
        
        # Get metadata for template
        metadata = services.get_media_metadata(media_type, media_id, source)
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": None,  # No instance after unwatching
            "diary_entries": diary_entries,
            "has_seasons_in_progress": False,  # No seasons after unwatching
        }
        
        # Add notification headers
        response = render(request, "app/components/media_actions.html", context)
        season_count = len(metadata.get("related", {}).get("seasons", []))
        response["X-Notification-Message"] = f"Show unmarked - all {season_count} seasons and episodes removed"
        response["X-Notification-Type"] = "info"
        return response
        
    except (Item.DoesNotExist, TV.DoesNotExist):
        # If the item or TV instance doesn't exist, return the unwatched state
        metadata = services.get_media_metadata(media_type, media_id, source)
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": None,
            "has_seasons_in_progress": False,  # No seasons if instance doesn't exist
        }
        return render(request, "app/components/media_actions.html", context)


@require_POST
def start_tracking_tv(request, source, media_type, media_id):
    """Start tracking a TV show by creating Season 1 with 'In Progress' status."""
    try:
        if media_type != MediaTypes.TV.value:
            raise Http404("Start tracking is only available for TV shows")
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                
        },
        )
        
        # Get or create the TV instance (but don't set it to In Progress)
        from app.models import TV
        tv_instance, created = TV.objects.get_or_create(
            item=item,
            user=request.user,
            defaults={
                "status": Status.PLANNING.value,  # Set to Planning initially
            }
        )
        
        # Create Season 1 with 'In Progress' status
        from app.models import Season
        season_item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        
        season_instance, season_created = Season.objects.get_or_create(
            item=season_item,
            user=request.user,
            related_tv=tv_instance,
            defaults={
                "status": Status.IN_PROGRESS.value,
            }
        )
        
        if not season_created:
            # If season already exists, update it to In Progress
            season_instance.status = Status.IN_PROGRESS.value
            season_instance.save()
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Check if there are any seasons in progress
        has_seasons_in_progress = False
        if tv_instance:
            # Use prefetched seasons to avoid N+1 query
            has_seasons_in_progress = any(season.status == Status.IN_PROGRESS.value for season in tv_instance.seasons.all())
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": tv_instance,
            "diary_entries": diary_entries,
            "has_seasons_in_progress": has_seasons_in_progress,
            
        }
        
        # Add notification headers
        response = render(request, "app/components/media_actions.html", context)
        response["X-Notification-Message"] = "Started watching - Season 1 is now in progress"
        response["X-Notification-Type"] = "success"
        return response
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in start_tracking_tv for {media_id}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Return a simple error response
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def watch_episode(request, source, media_type, media_id, season_number, episode_number):
    """Mark an episode as watched by creating an Episode instance."""
    if media_type != MediaTypes.EPISODE.value:
        raise Http404("Watch episode is only available for episodes")
        
    try:
        # Get or create the episode item
        episode_item, created = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": f"Episode {episode_number}",  # Will be updated with real title
                "image": settings.IMG_NONE,
            }
        )
        
        # Update episode item with real metadata (whether created or existing)
        try:
            # Get the season metadata to find the episode details
            season_metadata = services.get_media_metadata(
                "tv_with_seasons",
                media_id,
                source,
                [season_number]
            )[f"season/{season_number}"]
            
            # Find the episode in the metadata
            for episode_data in season_metadata["episodes"]:
                if episode_data["episode_number"] == episode_number:
                    # Update the episode item with real data
                    episode_item.title = episode_data["name"]
                    if episode_data.get("still_path"):
                        episode_item.image = f"https://image.tmdb.org/t/p/original{episode_data['still_path']}"
                    episode_item.overview = episode_data.get("overview")
                    episode_item.save()
                    break
        except Exception as e:
            # If we can't get metadata, keep the defaults
            logging.getLogger(__name__).warning(f"Could not update episode metadata: {e}")
        
        # Get the season instance for this user
        season_instance = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number=season_number,
            user=request.user
        )
        
        # Store season and TV status before marking episode as watched
        season_status_before = season_instance.status
        tv_status_before = season_instance.related_tv.status
        
        # Get or create the episode instance
        episode_instance, created = Episode.objects.get_or_create(
            item=episode_item,
            related_season=season_instance,
            defaults={
                "end_date": timezone.now(),
        }
        )
        
        if not created and not episode_instance.end_date:
            # If episode exists but wasn't watched, mark it as watched
            episode_instance.end_date = timezone.now()
            episode_instance.save()
        
        # Refresh to get updated status
        season_instance.refresh_from_db()
        season_instance.related_tv.refresh_from_db()
        
        # Check if season or show was just completed
        response = render_episode_card(request, episode_instance)
        
        if season_status_before != Status.COMPLETED.value and season_instance.status == Status.COMPLETED.value:
            # Season was just completed
            if tv_status_before != Status.COMPLETED.value and season_instance.related_tv.status == Status.COMPLETED.value:
                # Show was just completed!
                response['X-Notification-Message'] = f'Congratulations! You completed {season_instance.related_tv.item.title}!'
                response['X-Notification-Type'] = 'success'
            else:
                # Just season completed, next season started
                response['X-Notification-Message'] = f'Season {season_number} completed! Starting Season {season_number + 1}...'
                response['X-Notification-Type'] = 'success'
        
        return response
        
    except (Item.DoesNotExist, Season.DoesNotExist):
        raise Http404("Episode or season not found")


@require_POST
def unwatch_episode(request, source, media_type, media_id, season_number, episode_number):
    """Mark an episode as unwatched by removing the Episode instance."""
    if media_type != MediaTypes.EPISODE.value:
        raise Http404("Unwatch episode is only available for episodes")
        
    try:
        # Get or create the episode item
        episode_item, created = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": f"Episode {episode_number}",  # Will be updated with real title
                "image": settings.IMG_NONE,
            }
        )
        
        # Update episode item with real metadata (whether created or existing)
        try:
            # Get the season metadata to find the episode details
            season_metadata = services.get_media_metadata(
                "tv_with_seasons",
                media_id,
                source,
                [season_number]
            )[f"season/{season_number}"]
            
            # Find the episode in the metadata
            for episode_data in season_metadata["episodes"]:
                if episode_data["episode_number"] == episode_number:
                    # Update the episode item with real data
                    episode_item.title = episode_data["name"]
                    if episode_data.get("still_path"):
                        episode_item.image = f"https://image.tmdb.org/t/p/original{episode_data['still_path']}"
                    episode_item.overview = episode_data.get("overview")
                    episode_item.save()
                    break
        except Exception as e:
            # If we can't get metadata, keep the defaults
            logging.getLogger(__name__).warning(f"Could not update episode metadata: {e}")
        
        # Get the season instance for this user
        season_instance = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number=season_number,
            user=request.user
        )
        
        # Get and delete the episode instance
        episode_instance = Episode.objects.get(
            item=episode_item,
            related_season=season_instance
        )
        
        # Delete the episode instance (unwatch it)
        episode_instance.delete()
        
        # Create a mock episode object with all metadata from the API
        class MockEpisode:
            def __init__(self, item, metadata):
                self.item = item
                self.history = []
                self.image = item.image
                self.title = item.title
                self.episode_number = item.episode_number
                # Get episode data from metadata
                self.air_date = metadata.get("air_date")
                # Format runtime using the same function as tmdb.process_episodes
                from app.providers.tmdb import get_readable_duration
                self.runtime = get_readable_duration(metadata.get("runtime"))
                self.vote_average_out_of_5 = metadata.get("vote_average", 0) / 2 if metadata.get("vote_average") else None
                self.overview = metadata.get("overview", "No synopsis available.")
                # Add fields needed for URL generation
                self.source = item.source
                self.media_type = item.media_type
                self.media_id = item.media_id
                self.season_number = item.season_number
        
        # Get episode metadata from API
        try:
            season_metadata = services.get_media_metadata(
                "tv_with_seasons",
                media_id,
                source,
                [season_number]
            )[f"season/{season_number}"]
            
            episode_metadata = {}
            for ep_data in season_metadata["episodes"]:
                if ep_data["episode_number"] == episode_number:
                    episode_metadata = ep_data
                    break
        except Exception:
            episode_metadata = {}
        
        mock_episode = MockEpisode(episode_item, episode_metadata)
        
        # Return the updated episode card
        return render_episode_card(request, mock_episode)
        
    except (Item.DoesNotExist, Season.DoesNotExist, Episode.DoesNotExist):
        raise Http404("Episode or season not found")


def render_episode_card(request, episode):
    """Helper function to render an episode card."""
    # Create a mock episode object that matches the template's expectations
    class MockEpisode:
        def __init__(self, episode_instance):
            # Handle both Episode model instances and existing MockEpisode objects
            if hasattr(episode_instance, 'end_date'):
                # It's an Episode model instance
                self.item = episode_instance.item
                self.history = [episode_instance] if episode_instance.end_date else []
                self.image = episode_instance.item.image
                self.title = episode_instance.item.title
                self.episode_number = episode_instance.item.episode_number
                
                # Get episode metadata for additional fields
                try:
                    season_metadata = services.get_media_metadata(
                        "tv_with_seasons",
                        episode_instance.item.media_id,
                        episode_instance.item.source,
                        [episode_instance.item.season_number]
                    )[f"season/{episode_instance.item.season_number}"]
                    
                    # Find the episode in the metadata
                    for episode_data in season_metadata["episodes"]:
                        if episode_data["episode_number"] == episode_instance.item.episode_number:
                            self.air_date = episode_data.get("air_date")
                            # Format runtime using the same function as tmdb.process_episodes
                            from app.providers.tmdb import get_readable_duration
                            self.runtime = get_readable_duration(episode_data.get("runtime"))
                            self.vote_average_out_of_5 = episode_data.get("vote_average", 0) / 2 if episode_data.get("vote_average") else None
                            self.overview = episode_data.get("overview", "No synopsis available.")
                            break
                    else:
                        # Episode not found in metadata
                        self.air_date = None
                        self.runtime = None
                        self.vote_average_out_of_5 = None
                        self.overview = "No synopsis available."
                except Exception:
                    # If we can't get metadata, use defaults
                    self.air_date = None
                    self.runtime = None
                    self.vote_average_out_of_5 = None
                    self.overview = "No synopsis available."
                
                # Add fields needed for URL generation
                self.source = episode_instance.item.source
                self.media_type = episode_instance.item.media_type
                self.media_id = episode_instance.item.media_id
                self.season_number = episode_instance.item.season_number
            else:
                # It's already a MockEpisode object, copy its attributes
                # Make sure we have all the attributes
                self.item = episode_instance.item if hasattr(episode_instance, 'item') else None
                self.history = episode_instance.history if hasattr(episode_instance, 'history') else []
                self.image = episode_instance.image if hasattr(episode_instance, 'image') else None
                self.title = episode_instance.title if hasattr(episode_instance, 'title') else None
                self.episode_number = episode_instance.episode_number if hasattr(episode_instance, 'episode_number') else None
                self.air_date = episode_instance.air_date if hasattr(episode_instance, 'air_date') else None
                self.runtime = episode_instance.runtime if hasattr(episode_instance, 'runtime') else None
                self.vote_average_out_of_5 = episode_instance.vote_average_out_of_5 if hasattr(episode_instance, 'vote_average_out_of_5') else None
                self.overview = episode_instance.overview if hasattr(episode_instance, 'overview') else "No synopsis available."
                self.source = episode_instance.source if hasattr(episode_instance, 'source') else None
                self.media_type = episode_instance.media_type if hasattr(episode_instance, 'media_type') else None
                self.media_id = episode_instance.media_id if hasattr(episode_instance, 'media_id') else None
                self.season_number = episode_instance.season_number if hasattr(episode_instance, 'season_number') else None
    
    mock_episode = MockEpisode(episode)
    
    context = {
        "episode": mock_episode,
        "media": {
            "media_type": MediaTypes.SEASON.value,
            "season_number": episode.item.season_number if hasattr(episode, 'item') else episode.season_number,
        },
        "csrf_token": request.META.get('CSRF_COOKIE', ''),
    }
    return render(request, "app/components/media/episode_card.html", context)


@require_POST
def mark_season_watched(request, source, media_type, media_id, season_number):
    """Mark a season as watched by creating a tracking instance and marking all episodes as consumed."""
    # The URL passes media_type='tv' but we need to work with seasons
    season_media_type = MediaTypes.SEASON.value
        
    try:
        # Get the season item
        season_item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=season_media_type,
            season_number=season_number
        )
        
        # Get or create the season instance
        from app.models import Season
        # Temporarily disable calendar triggers to avoid events.tasks error
        season_item._disable_calendar_triggers = True
        season_instance, created = Season.objects.get_or_create(
            item=season_item,
            user=request.user,
            defaults={
                "status": Status.COMPLETED.value,
        }
        )
        
        if not created:
            # If it already exists, mark it as consumed
            season_instance.mark_consumed()
        
        # Mark all episodes in this season as watched
        try:
            season_instance._completed()  # This creates all episodes and marks them as watched
        except Exception as e:
            # Log the error but don't fail the request
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in _completed() for Season {media_id} S{season_number}: {e}")
            # Continue without marking episodes as watched
        
        # Get metadata for template
        metadata = services.get_media_metadata(season_media_type, media_id, source, season_numbers=[season_number])
        # For seasons, the metadata is already the season data, not a dict with season/{number} key
        season_metadata = metadata
        season_metadata["media_type"] = season_media_type
        season_metadata["source"] = source
        season_metadata["media_id"] = media_id
        
        # Get current instance for template
        user_medias = BasicMedia.objects.filter_media_prefetch(
            request.user,
            media_id,
            MediaTypes.SEASON.value,
            source,
            season_number=season_number,
        )
        current_instance = user_medias[0] if user_medias else None
        
        # Get diary entries for this season
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=season_item).order_by('-consumed_at')
        
        context = {
            "media": season_metadata,
            "media_type": season_media_type,
            "current_instance": current_instance,
            "diary_entries": diary_entries,
        }
        
        # Add notification headers
        response = render(request, "app/components/media_actions.html", context)
        episode_count = len(season_metadata.get("episodes", []))
        response["X-Notification-Message"] = f"Season {season_number} marked as watched ({episode_count} episodes)"
        response["X-Notification-Type"] = "success"
        return response
        
    except Item.DoesNotExist:
        raise Http404("Season not found")


@require_POST
def unmark_season_watched(request, source, media_type, media_id, season_number):
    """Unmark a season as watched by removing the tracking instance and all episodes."""
    # The URL passes media_type='tv' but we need to work with seasons
    season_media_type = MediaTypes.SEASON.value
        
    try:
        # Get the season item
        season_item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=season_media_type,
            season_number=season_number
        )
        
        # Get the season instance
        from app.models import Season
        season_instance = Season.objects.get(
            item=season_item,
            user=request.user
        )
        
        # Delete all related episodes first
        season_instance.episodes.all().delete()
        
        # Delete the season instance to "unwatch" it
        season_instance.delete()
        
        # Get metadata for template
        metadata = services.get_media_metadata(season_media_type, media_id, source, season_numbers=[season_number])
        # For seasons, the metadata is already the season data, not a dict with season/{number} key
        season_metadata = metadata
        season_metadata["media_type"] = season_media_type
        season_metadata["source"] = source
        season_metadata["media_id"] = media_id
        
        # Get diary entries for this season
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=season_item).order_by('-consumed_at')
        
        context = {
            "media": season_metadata,
            "media_type": season_media_type,
            "current_instance": None,  # No instance after unwatching
            "diary_entries": diary_entries,
        }
        
        # Add notification headers
        response = render(request, "app/components/media_actions.html", context)
        response["X-Notification-Message"] = f"Season {season_number} unmarked - all episodes removed"
        response["X-Notification-Type"] = "info"
        return response
        
    except (Item.DoesNotExist, Season.DoesNotExist):
        raise Http404("Season not found")


@require_POST
def add_movie_diary_entry(request, source, media_type, media_id, season_number=None):
    """Create a diary entry for a movie, TV show, or season using media metadata."""
    try:
        book_instance = None
        if media_type not in [
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.BOOK.value,
        ]:
            raise Http404("Diary entries are only available for movies, TV shows, seasons, and books")
            
        logger.info(f"Creating diary entry for {media_type} {media_id} from {source}")
        logger.info(f"POST data: {dict(request.POST)}")
        logger.info(f"Tags in POST: {request.POST.get('tags', 'NOT_FOUND')}")
        logger.info(f"All POST keys: {list(request.POST.keys())}")
        logger.info(f"Season number parameter: {season_number}")
        
        # Get or create the item
        if media_type == MediaTypes.SEASON.value and season_number is not None:
            logger.info(f"Processing season item creation for season {season_number}")
            # For seasons, we need to get the TV show metadata first
            tv_metadata = services.get_media_metadata("tv_with_seasons", media_id, source, [season_number])
            logger.info(f"TV metadata keys: {list(tv_metadata.keys())}")
            season_key = f"season/{season_number}"
            logger.info(f"Looking for season key: {season_key}")
            
            if season_key not in tv_metadata:
                logger.error(f"Season key {season_key} not found in tv_metadata")
                raise ValueError(f"Season {season_number} not found in metadata")
                
            season_metadata = tv_metadata[season_key]
            logger.info(f"Season metadata keys: {list(season_metadata.keys())}")
            logger.info(f"Season title: {season_metadata.get('title', 'NO_TITLE')}")
            
            item, created = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                season_number=season_number,
                defaults={
                    "title": season_metadata["title"],
                    "image": season_metadata["image"],
        },
            )
            logger.info(f"Season item {'created' if created else 'found'}: {item}")
        else:
            metadata = services.get_media_metadata(media_type, media_id, source)
            defaults = {
                "title": metadata["title"],
                "image": metadata["image"],
            }
            if media_type == MediaTypes.BOOK.value:
                defaults["total_pages"] = metadata.get("total_pages") or metadata.get("max_progress")
            item, created = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults=defaults,
            )
            if (
                media_type == MediaTypes.BOOK.value
                and not created
                and defaults.get("total_pages")
                and item.total_pages != defaults["total_pages"]
            ):
                item.total_pages = defaults["total_pages"]
                item.save(update_fields=["total_pages"])
        logger.info(f"Item {'created' if created else 'found'}: {item}")
        
        # Create diary entry using the services function
        from app.services import create_diary_entry
        from django.utils.dateparse import parse_date
        
        # Parse form data
        consumed_at = parse_date(request.POST.get('watch_date'))
        if not consumed_at:
            # Use current datetime to allow multiple entries per day (rewatches)
            consumed_at = timezone.now()
        else:
            # Convert date to datetime at end of day to allow multiple entries per day
            consumed_at = timezone.datetime.combine(consumed_at, timezone.datetime.max.time())
            
        rating = request.POST.get('rating')
        if rating and rating.strip():
            rating = float(rating)
        else:
            rating = None
            
        review = request.POST.get('review', '').strip()
        liked = request.POST.get('liked', '').lower() == 'true'
        is_rewatch = request.POST.get('is_rewatch') == 'on'
        auto_mark_consumed = request.POST.get('auto_mark_consumed') == 'true'
        
        # Parse tags
        tags_data = request.POST.get('tags', '').strip()
        tag_names = []
        if tags_data:
            tag_names = [tag.strip().lower() for tag in tags_data.split(',') if tag.strip()]
        
        logger.info(f"Parsed data - Date: {consumed_at}, Rating: {rating}, Review: {len(review)} chars, Liked: {liked}, Rewatch: {is_rewatch}, Auto-consume: {auto_mark_consumed}, Tags: {tag_names}")
        
        # Create the diary entry
        logger.info(f"About to create diary entry with auto_mark_consumed={auto_mark_consumed}")
        entry = create_diary_entry(
            user=request.user,
            item=item,
            consumed_at=consumed_at,
            rating=rating,
            review=review,
            liked=liked,
            is_rewatch=is_rewatch,
            auto_mark_consumed=auto_mark_consumed,
            tags=tag_names,
        )
        
        logger.info(f"Diary entry created successfully: {entry}")
        
        # For TV shows and seasons, mark all episodes as watched (but don't create diary entries for episodes)
        if media_type in [MediaTypes.TV.value, MediaTypes.SEASON.value] and auto_mark_consumed:
            try:
                if media_type == MediaTypes.TV.value:
                    # For TV shows, mark the entire show as completed
                    from app.models import TV
                    tv_instance, created = TV.objects.get_or_create(
                        item=item,
                        user=request.user,
                        defaults={
                            "status": Status.COMPLETED.value,
        }
                    )
                    
                    if not created:
                        # If it already exists, just update the status (TV shows don't have direct end_date)
                        tv_instance.status = Status.COMPLETED.value
                        tv_instance.save()
                    
                    # Mark all episodes and seasons as watched (no diary entries for episodes)
                    try:
                        tv_instance._completed()  # This creates all seasons and episodes and marks them as watched
                        logger.info(f"TV show {item.title} marked as completed with all episodes")
                    except Exception as e:
                        logger.error(f"Error in _completed() for TV {media_id}: {e}")
                        # Continue without marking episodes as watched
                    
                elif media_type == MediaTypes.SEASON.value:
                    logger.info(f"Processing season completion for season {item.season_number}")
                    # For seasons, mark the specific season and its episodes as completed
                    from app.models import Season
                    season_instance, created = Season.objects.get_or_create(
                        item=item,
                        user=request.user,
                        defaults={
                            "status": Status.COMPLETED.value,
        }
                    )
                    logger.info(f"Season instance {'created' if created else 'found'}: {season_instance}")
                    
                    if not created:
                        # If it already exists, mark it as consumed
                        logger.info("Marking existing season as consumed")
                        season_instance.mark_consumed()
                    
                    # Create and mark all episodes in this season as watched (no diary entries for episodes)
                    try:
                        # Get season metadata to create episodes
                        season_metadata = services.get_media_metadata(
                            "tv_with_seasons", 
                            media_id, 
                            source, 
                            [item.season_number]
                        )[f"season/{item.season_number}"]
                        
                        # Debug logging for season metadata structure
                        logger.info(f"Season {item.season_number} metadata keys: {list(season_metadata.keys())}")
                        logger.info(f"Season {item.season_number} details keys: {list(season_metadata.get('details', {}).keys())}")
                        
                        # Check if season has episodes (skip seasons with 0 episodes)
                        episode_count = season_metadata.get("details", {}).get("episodes", 0)
                        logger.info(f"Season {item.season_number} episode_count: {episode_count}")
                        
                        if episode_count == 0:
                            logger.info(f"Skipping season {item.season_number} with 0 episodes")
                            season_released = False
                        else:
                            logger.info(f"Season {item.season_number} has {episode_count} episodes, processing")
                            season_released = True
                        
                        # Only process episodes if season is released
                        if season_released:
                            # Process episodes like in season_details view
                            episodes_in_db = season_instance.episodes.all()
                            
                            if source == Sources.MANUAL.value:
                                from app.providers import manual
                                season_metadata["episodes"] = manual.process_episodes(
                                    season_metadata,
                                    episodes_in_db,
                                )
                            else:
                                from app.providers import tmdb
                                season_metadata["episodes"] = tmdb.process_episodes(
                                    season_metadata,
                                    episodes_in_db,
                                )
                            
                            # Check if episodes exist in metadata
                            episodes_list = season_metadata.get("episodes")
                            if episodes_list:
                                # Get remaining episodes and create them
                                episodes_to_create = season_instance.get_remaining_eps(season_metadata)
                                if episodes_to_create:
                                    from app.models import bulk_create_with_history, Episode
                                    bulk_create_with_history(episodes_to_create, Episode)
                    except Exception as e:
                        logger.error(f"Failed to create episodes for season {item.title} S{item.season_number}: {e}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        # Continue without marking episodes as watched
                    
            except Exception as e:
                    logger.warning(f"Failed to mark {media_type} as completed: {e}")

        if media_type == MediaTypes.BOOK.value:
            completion_datetime = consumed_at or timezone.now()
            from decimal import Decimal

            book_defaults = {
                "status": Status.COMPLETED.value,
                "end_date": completion_datetime,
            }
            book_instance, created_book = Book.objects.get_or_create(
                item=item,
                user=request.user,
                defaults=book_defaults,
            )
            logger.info(f"Book instance {'created' if created_book else 'found'}: {book_instance}")

            book_instance.status = Status.COMPLETED.value
            if not book_instance.start_date:
                book_instance.start_date = completion_datetime
            book_instance.end_date = completion_datetime

            if rating is not None:
                book_instance.score = Decimal(str(rating))
            if review:
                book_instance.notes = review

            book_instance.completed_manually = False
            book_instance.save(update_fields=["status", "end_date", "completed_manually"])
            logger.info(f"Book {book_instance} marked as completed for diary entry")
            if request.POST.get('book_complete', '').lower() in ('true', '1', 'yes', 'on'):
                book_instance.completion_diary_entry = entry
                book_instance.completed_manually = False
                book_instance.save(update_fields=['completion_diary_entry', 'completed_manually'])

        # Return success response
        return JsonResponse({"success": True, "entry_id": entry.id})
        
    except Exception as e:
        logger.error(f"Error creating diary entry: {str(e)}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=400)


@require_GET
def edit_diary_entry(request, entry_id):
    """Show edit modal for a diary entry."""
    entry = get_object_or_404(DiaryEntry.objects.prefetch_related('tags'), id=entry_id, user=request.user)
    
    # Pre-populate form with existing data
    form = DiaryEntryForm(initial={
        'consumed_at': entry.consumed_at,
        'rating': entry.rating,
        'review': entry.review,
        'tags': ', '.join([tag.name for tag in entry.tags.all()]),
    })
    
    context = {
        'entry': entry,
        'form': form,
        'user': request.user,
    }
    
    return render(request, 'app/components/edit_diary_modal.html', context)


@require_POST
def update_diary_entry(request, entry_id):
    """Update a diary entry."""
    try:
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        
        # Debug: Log the POST data
        logger.info(f"Update diary entry {entry_id} - POST data: {dict(request.POST)}")
        
        # Parse form data
        consumed_at = parse_date(request.POST.get('watch_date'))
        if not consumed_at:
            # Keep the original datetime to preserve ordering
            consumed_at = entry.consumed_at
        else:
            # Convert date to datetime at end of day to allow multiple entries per day
            consumed_at = timezone.datetime.combine(consumed_at, timezone.datetime.max.time())
            
        rating = request.POST.get('rating')
        if rating and rating.strip():
            rating = float(rating)
        else:
            rating = None
            
        review = request.POST.get('review', '').strip()
        liked = request.POST.get('liked', '').lower() == 'true'
        is_rewatch = request.POST.get('is_rewatch') == 'on'  # Checkbox value
        
        # Parse tags
        tags_data = request.POST.get('tags', '').strip()
        tag_names = []
        if tags_data:
            tag_names = [tag.strip().lower() for tag in tags_data.split(',') if tag.strip()]
        
        logger.info(f"Parsed data - Date: {consumed_at}, Rating: {rating}, Review: '{review}', Liked: {liked}, Rewatch: {is_rewatch}, Tags: {tag_names}")
        
        # Update the entry
        entry.consumed_at = consumed_at
        entry.rating = rating
        entry.review = review
        entry.liked = liked
        entry.is_rewatch = is_rewatch
        entry.save()
        
        # Update tags
        from app.services import update_diary_entry_tags
        update_diary_entry_tags(entry, tag_names)
        
        logger.info(f"Diary entry updated successfully: {entry}")
        logger.info(f"Updated values - Date: {entry.consumed_at}, Rating: {entry.rating}, Review: '{entry.review}', Liked: {entry.liked}")
        
        # Return updated diary entries HTML
        from django.core.paginator import Paginator
        entries = DiaryEntry.objects.filter(user=request.user).order_by('-consumed_at')
        paginator = Paginator(entries, 25)
        page_obj = paginator.get_page(1)
        
        context = {
            "entries": page_obj,
            "years": entries.dates("consumed_at", "year", order="DESC"),
            "current_year": None,
            "current_media_type": None,
            "media_type_choices": MediaTypes.choices,
        }
        
        return render(request, "app/components/diary_entries.html", context)
        
    except Exception as e:
        logger.error(f"Error updating diary entry: {str(e)}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=400)


@require_POST
def delete_diary_entry(request, entry_id):
    """Delete a diary entry."""
    try:
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        item = entry.item
        user = entry.user
        book_instance = None
        book_completion_entry = False

        if item.media_type == MediaTypes.BOOK.value:
            try:
                book_instance = Book.objects.get(user=user, item=item)
                book_completion_entry = (
                    book_instance.completion_diary_entry_id == entry.id
                )
            except Book.DoesNotExist:
                book_instance = None
        
        logger.info(f"Deleting diary entry {entry_id} for {item} by {user}")
        
        # Delete the entry
        entry.delete()
        
        # Check if this was the last diary entry for this item
        remaining_entries = DiaryEntry.objects.filter(
            user=user, 
            item=item
        ).exists()
        
        logger.info(f"Remaining diary entries for {item}: {remaining_entries}")
        
        # If no diary entries remain, also delete the media instance (unwatch)
        if not remaining_entries:
            if item.media_type == MediaTypes.MOVIE.value:
                try:
                    movie_instance = Movie.objects.get(user=user, item=item)
                    logger.info(f"Deleting Movie instance to unwatch: {movie_instance}")
                    movie_instance.delete()
                    logger.info(f"Successfully unwatched {item} for {user}")
                except Movie.DoesNotExist:
                    logger.info(f"No Movie instance found for {item} - already unwatched")
                    
            elif item.media_type == MediaTypes.TV.value:
                try:
                    tv_instance = TV.objects.get(user=user, item=item)
                    logger.info(f"Deleting TV instance to unwatch: {tv_instance}")
                    
                    # Delete all related episodes and seasons first
                    for season in tv_instance.seasons.all():
                        season.episodes.all().delete()
                        season.delete()
                    
                    # Delete the TV instance
                    tv_instance.delete()
                    logger.info(f"Successfully unwatched TV show {item} and all seasons/episodes for {user}")
                except TV.DoesNotExist:
                    logger.info(f"No TV instance found for {item} - already unwatched")
                    
            elif item.media_type == MediaTypes.SEASON.value:
                try:
                    season_instance = Season.objects.get(user=user, item=item)
                    logger.info(f"Deleting Season instance to unwatch: {season_instance}")
                    
                    # Delete all related episodes first
                    season_instance.episodes.all().delete()
                    
                    # Delete the season instance
                    season_instance.delete()
                    logger.info(f"Successfully unwatched season {item} and all episodes for {user}")
                except Season.DoesNotExist:
                    logger.info(f"No Season instance found for {item} - already unwatched")
            elif item.media_type == MediaTypes.BOOK.value and book_instance:
                if not book_instance.completed_manually:
                    logger.info("Deleting Book instance created via diary completion")
                    book_instance.delete()
                else:
                    logger.info("Book instance retained (manual tracking)")
            elif item.media_type == MediaTypes.BOOK.value and not book_instance:
                logger.info(f"No Book instance found for {item} - already untracked")
        elif item.media_type == MediaTypes.BOOK.value and book_instance and book_completion_entry:
            logger.info("Clearing diary completion link from Book instance")
            book_instance.completion_diary_entry = None
            book_instance.save(update_fields=['completion_diary_entry'])
        
        # Return success response
        return JsonResponse({"success": True})
        
    except Exception as e:
        logger.error(f"Error deleting diary entry: {str(e)}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=400)


@require_GET
def tag_autocomplete(request):
    """Return tag suggestions for autocomplete."""
    query = request.GET.get('q', '').strip().lower()
    if len(query) < 1:
        return JsonResponse({'tags': []})
    
    try:
        from app.models import Tag
        tags = Tag.objects.filter(name__icontains=query).order_by('-usage_count', 'name')[:10]
        
        return JsonResponse({
            'tags': [{'name': tag.name, 'usage_count': tag.usage_count} for tag in tags]
        })
    except Exception as e:
        # If Tag model doesn't exist yet, return empty results
        return JsonResponse({'tags': []})


@require_POST
def mark_book_read(request, source, media_id):
    """Mark a book as read by creating a Book instance with completed status."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        # Get or create the Book instance
        book_instance, created = Book.objects.get_or_create(
            item=item,
            user=request.user,
            defaults={
                "status": Status.COMPLETED.value,
                "end_date": timezone.now(),
                "completed_manually": True,
            }
        )

        if not created:
            # If it already exists, update the status only if it's not already completed
            book_instance.end_date = timezone.now()
            book_instance.completed_manually = True
            book_instance.completion_diary_entry = None
            
            # Only update status if it's not already completed
            if book_instance.status != Status.COMPLETED.value:
                book_instance.status = Status.COMPLETED.value
                book_instance.save(update_fields=["status", "end_date", "completed_manually", "completion_diary_entry"])
            else:
                book_instance.save(update_fields=["end_date", "completed_manually", "completion_diary_entry"])
        
        # Create a completed reading session
        session, _ = BookSession.objects.get_or_create(
            related_book=book_instance,
            status=Status.COMPLETED.value,
            defaults={
                "end_date": timezone.now(),
            }
        )
        # Ensure end_date is set if it wasn't already
        if not session.end_date:
            session.end_date = timezone.now()
            session.save(update_fields=['end_date'])
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error marking book as read: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def start_reading_book(request, source, media_id):
    """Start reading a book by creating a Book instance with in-progress status."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        # Get or create the Book instance
        book_instance, created = Book.objects.get_or_create(
            item=item,
            user=request.user,
            defaults={
                "status": Status.IN_PROGRESS.value,
                "start_date": timezone.now(),
            }
        )
        
        if not created:
            # If it already exists, update the status
            book_instance.status = Status.IN_PROGRESS.value
            if not book_instance.start_date:
                book_instance.start_date = timezone.now()
            book_instance.save()
        
        # Create an in-progress reading session
        BookSession.objects.get_or_create(
            related_book=book_instance,
            status=Status.IN_PROGRESS.value,
            defaults={
                "start_date": timezone.now(),
            }
        )
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error starting to read book: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def log_book_progress(request, source, media_id):
    """Log reading progress for a book."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        form = BookProgressForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": "Invalid form data", "details": form.errors}, status=400)
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        # Get or create the Book instance
        book_instance, created = Book.objects.get_or_create(
            item=item,
            user=request.user,
            defaults={
                "status": Status.IN_PROGRESS.value,
                "start_date": timezone.now(),
                "completed_manually": False,
            }
        )

        if not created and book_instance.status != Status.IN_PROGRESS.value:
            book_instance.status = Status.IN_PROGRESS.value
            if not book_instance.start_date:
                book_instance.start_date = timezone.now()
            book_instance.completed_manually = False
            book_instance.save(update_fields=["status", "start_date", "completed_manually"])
        
        # Log the reading session
        session = book_instance.log_reading_session(
            progress_type=form.cleaned_data['progress_type'],
            progress_value=form.cleaned_data['progress_value']
        )
        
        # Check if book is completed
        if form.cleaned_data['progress_type'] == 'percentage' and form.cleaned_data['progress_value'] >= 100:
            book_instance.status = Status.COMPLETED.value
            book_instance.end_date = timezone.now()
            book_instance.completed_manually = True
            book_instance.completion_diary_entry = None
            book_instance.save(update_fields=["status", "end_date", "completed_manually", "completion_diary_entry"])
            session.status = Status.COMPLETED.value
            session.end_date = timezone.now()
            session.save()
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error logging book progress: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def log_book_completed(request, source, media_id):
    """Log a completed book with rating and review."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        form = BookLogForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": "Invalid form data", "details": form.errors}, status=400)
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        # Get or create the Book instance
        book_instance, created = Book.objects.get_or_create(
            item=item,
            user=request.user,
            defaults={
                "status": Status.COMPLETED.value,
                "end_date": form.cleaned_data.get('end_date') or timezone.now(),
                "completed_manually": True,
            }
        )

        if not created:
            book_instance.status = Status.COMPLETED.value
            book_instance.end_date = form.cleaned_data.get('end_date') or timezone.now()
            book_instance.completed_manually = True
            book_instance.completion_diary_entry = None
            book_instance.save(update_fields=["status", "end_date", "completed_manually", "completion_diary_entry"])
        
        # Update score and notes if provided
        if form.cleaned_data.get('score'):
            book_instance.score = form.cleaned_data['score']
        if form.cleaned_data.get('notes'):
            book_instance.notes = form.cleaned_data['notes']
        book_instance.save()
        
        # Create a completed reading session
        BookSession.objects.get_or_create(
            related_book=book_instance,
            status=Status.COMPLETED.value,
            defaults={
                "end_date": form.cleaned_data.get('end_date') or timezone.now(),
                "notes": form.cleaned_data.get('notes', ''),
            }
        )
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error logging completed book: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def book_progress_modal(request, source, media_id):
    """Return the book progress modal template."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
        # Get the item for context
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type,
        )
        context = {
            'media': item,
        }
        return render(request, "app/components/book_progress_modal.html", context)
    except Item.DoesNotExist:
        raise Http404("Book not found")


@require_GET
def book_completed_modal(request, source, media_id):
    """Return the book completed modal template."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
        # Get the item for context
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type,
        )
        context = {
            'media': item,
        }
        return render(request, "app/components/book_completed_modal.html", context)
    except Item.DoesNotExist:
        raise Http404("Book not found")
