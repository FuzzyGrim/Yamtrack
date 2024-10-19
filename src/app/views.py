import logging

from django.apps import apps
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import database, helpers
from app.forms import FilterForm, ItemForm, get_form_class
from app.models import STATUS_IN_PROGRESS, Episode, Item, Season
from app.providers import igdb, mal, mangaupdates, services, tmdb

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress and repeating."""
    list_by_type = database.get_in_progress(request.user)
    context = {"list_by_type": list_by_type}
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request):
    """Increase or decrease the progress of a media item from home page."""
    item = Item.objects.get(id=request.POST["item"])
    media_type = item.media_type
    operation = request.POST["operation"]

    media = database.get_media(media_type, item, request.user)

    if media:
        if operation == "increase":
            media.increase_progress()
        elif operation == "decrease":
            media.decrease_progress()

        response = media.progress_response()
        return render(
            request,
            "app/components/progress_changer.html",
            {"media": response, "media_type": media_type},
        )

    messages.error(
        request,
        "Media item was deleted before trying to change progress",
    )

    response = HttpResponse()
    response["HX-Redirect"] = reverse("home")
    return response


@require_GET
def media_list(request, media_type):
    """Return the media list page."""
    layout_user = request.user.get_layout(media_type)

    if request.GET:
        layout_request = request.GET.get("layout", layout_user)
        filter_form = FilterForm(request.GET, layout=layout_request)
        if filter_form.is_valid() and layout_request != layout_user:
            request.user.set_layout(media_type, layout_request)
    else:
        filter_form = FilterForm(layout=layout_user)

    status_filter = request.GET.get("status", "all")
    sort_filter = request.GET.get("sort", "score")

    media_list = database.get_media_list(
        user=request.user,
        media_type=media_type,
        status_filter=[status_filter.capitalize()],
        sort_filter=sort_filter,
    )

    return render(
        request,
        request.user.get_layout_template(media_type),
        {
            "media_type": media_type,
            "media_list": media_list,
            "filter_form": filter_form,
        },
    )


@require_GET
def media_search(request):
    """Return the media search page."""
    media_type = request.GET["media_type"]
    query = request.GET["q"]
    request.user.set_last_search_type(media_type)

    source = request.GET.get("source")

    if media_type == "manga":
        if source == "mangaupdates":
            query_list = mangaupdates.search(query)
        else:
            query_list = mal.search(media_type, query)
    elif media_type in "anime":
        query_list = mal.search(media_type, query)
    elif media_type in ("tv", "movie"):
        query_list = tmdb.search(media_type, query)
    elif media_type == "game":
        query_list = igdb.search(query)

    context = {"query_list": query_list, "source": source}

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, media_type, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a media item."""
    source = request.GET.get("source")
    media_metadata = services.get_media_metadata(media_type, media_id, source)

    context = {"media": media_metadata}
    return render(request, "app/media_details.html", context)


@require_GET
def season_details(request, media_id, title, season_number):  # noqa: ARG001 title for URL
    """Return the details page for a season."""
    tv_metadata = tmdb.tv_with_seasons(media_id, [season_number])
    season_metadata = tv_metadata[f"season/{season_number}"]

    episodes_in_db = Episode.objects.filter(
        item__media_id=media_id,
        item__season_number=season_number,
        related_season__user=request.user,
    ).values("item__episode_number", "watch_date", "repeats")

    season_metadata["episodes"] = tmdb.process_episodes(
        season_metadata,
        episodes_in_db,
    )

    context = {"season": season_metadata, "tv": tv_metadata}
    return render(request, "app/season_details.html", context)


@require_GET
def track(request):
    """Return the tracking form for a media item."""
    media_id = request.GET["media_id"]
    source = request.GET["source"]

    media_type = request.GET["media_type"]
    season_number = request.GET.get("season_number")

    item, _ = Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=season_number,
        episode_number=None,
        defaults={
            "title": request.GET["title"],
            "image": request.GET["image"],
        },
    )

    media = database.get_media(media_type, item, request.user)

    initial_data = {
        "item": item,
    }

    if media_type == "game" and media:
        initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)

    form = get_form_class(media_type)(instance=media, initial=initial_data)

    title = request.GET["title"]
    if season_number:
        title = f"{title} S{season_number}"

    form_id = f"form-{item.id}"
    form.helper.form_id = form_id

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form_id": form_id,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
    """Save or update media data to the database."""
    item = Item.objects.get(id=request.POST["item"])
    media_type = item.media_type

    instance = database.get_media(media_type, item, request.user)

    if not instance:
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
        messages.error(
            request,
            "Could not save the media item, there were errors in the form.",
        )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    item = Item.objects.get(id=request.POST["item"])
    media_type = item.media_type

    media = database.get_media(media_type, item, request.user)
    if media:
        media.delete()
        logger.info("%s deleted successfully.", media)
    else:
        logger.warning("The %s was already deleted before.", media_type)

    return helpers.redirect_back(request)


@require_POST
def episode_handler(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    media_id = request.POST["media_id"]
    season_number = request.POST["season_number"]
    episode_number = request.POST["episode_number"]

    try:
        related_season = Season.objects.get(
            item__media_id=media_id,
            item__season_number=season_number,
            item__episode_number=None,
            user=request.user,
        )
    except Season.DoesNotExist:
        tv_metadata = tmdb.tv_with_seasons(media_id, [season_number])
        season_metadata = tv_metadata[f"season/{season_number}"]

        item = Item.objects.create(
            media_id=media_id,
            source="tmdb",
            media_type="season",
            season_number=season_number,
            title=tv_metadata["title"],
            image=season_metadata["image"],
        )
        related_season = Season(
            item=item,
            user=request.user,
            score=None,
            status=STATUS_IN_PROGRESS,
            notes="",
        )

        related_season.save()
        logger.info("%s did not exist, it was created successfully.", related_season)

    if "unwatch" in request.POST:
        related_season.unwatch(episode_number)

    else:
        if "release" in request.POST:
            watch_date = request.POST["release"]
        else:
            # set watch date from form
            watch_date = request.POST["date"]
        related_season.watch(episode_number, watch_date)

    return helpers.redirect_back(request)


@require_http_methods(["GET", "POST"])
def add_manual_item(request):
    """Return the form for manually adding media items."""
    if request.method == "POST":
        form = ItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.source = "manual"
            manual_items_count = Item.objects.filter(source="manual").count()
            item.media_id = manual_items_count + 1
            item.save()

            media_form = get_form_class(item.media_type)(request.POST)
            media_form.fields["item"].required = False
            if media_form.is_valid():
                media_form.instance.item = item
                media_form.save()
            else:
                item.delete()

            return redirect("add_manual_item")

    default_media_type = "movie"
    form = ItemForm(initial={"media_type": default_media_type})

    # Remove TV-related media types from the choices
    choices = form.fields["media_type"].choices
    filtered_choices = [
        item for item in choices if item[0] not in ("tv", "season", "episode")
    ]
    form.fields["media_type"].choices = filtered_choices

    context = {"form": form, "media_form": get_form_class(default_media_type)}

    return render(request, "app/add_manual.html", context)


@require_GET
def add_manual_media(request):
    """Return the form for manually adding media items."""
    media_type = request.GET.get("media_type")
    context = {"form": get_form_class(media_type)}
    return render(request, "app/components/add_manual_form.html", context)


@require_GET
def history(request):
    """Return the history page for a media item."""
    media_type = request.GET["media_type"]

    item, _ = Item.objects.get_or_create(
        media_id=request.GET["media_id"],
        source=request.GET["source"],
        media_type=media_type,
        season_number=request.GET.get("season_number"),
        episode_number=request.GET.get("episode_number"),
        defaults={
            "title": request.GET["title"],
            "image": request.GET["image"],
        },
    )

    media = database.get_media(media_type, item, request.user)
    changes = []
    if media:
        history = media.history.all()
        if history is not None:
            last = history.first()
            for _ in range(history.count()):
                new_record, old_record = last, last.prev_record
                if old_record is not None:
                    delta = new_record.diff_against(old_record)
                    changes.append(delta)
                    last = old_record
                else:
                    # If there is no previous record, it's a creation entry
                    history_model = apps.get_model(
                        app_label="app",
                        model_name=f"historical{media_type}",
                    )
                    creation_changes = [
                        {
                            "field": field.verbose_name,
                            "new": getattr(new_record, field.attname),
                        }
                        for field in history_model._meta.get_fields()  # noqa: SLF001
                        if getattr(new_record, field.attname)  # not None/0/empty
                        and not field.name.startswith("history")
                        and field.name != "id"
                    ]
                    changes.append(
                        {
                            "new_record": new_record,
                            "changes": creation_changes,
                        },
                    )

    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "changes": changes,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def history_delete(request):
    """Delete a history record for a media item."""
    history_id = request.POST["history_id"]
    media_type = request.POST["media_type"]

    model_name = f"historical{media_type}"

    history = apps.get_model(app_label="app", model_name=model_name).objects.get(
        history_id=history_id,
    )

    if history.history_user_id == request.user.id:
        history.delete()
        logger.info("History record deleted successfully.")
    else:
        logger.warning("User does not have permission to delete this history record.")

    return helpers.redirect_back(request)
