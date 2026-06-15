from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme

from app.models import BasicMedia, Item, MediaTypes, Status

YEAR_ONLY_PARTS = 1
YEAR_MONTH_PARTS = 2


def get_owned_media_or_404(request, media_type, instance_id, *, prefetch=False):
    """Return media owned by the current user or raise 404."""
    try:
        if prefetch:
            return BasicMedia.objects.get_media_prefetch(
                request.user,
                media_type,
                instance_id,
            )
        return BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    except ObjectDoesNotExist as exc:
        msg = "Media not found"
        raise Http404(msg) from exc


def get_configured_app_url():
    """Return the configured public application origin, if one is available."""
    for url in getattr(settings, "URLS", []):
        if url:
            return url.rstrip("/")

    base_url = getattr(settings, "BASE_URL", None)
    parsed_base_url = urlparse(base_url or "")
    if parsed_base_url.scheme and parsed_base_url.netloc:
        return base_url.rstrip("/")

    return None


def build_absolute_app_url(request, path):
    """Build an absolute URL using the configured public origin when possible."""
    parsed_path = urlparse(path)
    if parsed_path.scheme and parsed_path.netloc:
        return path

    configured_app_url = get_configured_app_url()
    if configured_app_url:
        return urljoin(f"{configured_app_url}/", path.lstrip("/"))

    if request is None:
        return None

    return request.build_absolute_uri(path)


def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = int(total_minutes / 60)
    minutes = int(total_minutes % 60)
    if hours == 0:
        return f"{minutes}min"
    return f"{hours}h {minutes:02d}min"


def redirect_back(request):
    """Redirect to the previous page, removing the 'page' parameter if present."""
    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        next_url = request.GET["next"]

        # Parse the URL
        parsed_url = urlparse(next_url)

        # Get the query parameters and remove params we don't want
        query_params = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
        query_params.pop("page", None)
        query_params.pop("load_media_type", None)

        # Reconstruct the URL
        new_query = urlencode(query_params)
        new_parts = list(parsed_url)
        new_parts[4] = new_query  # index 4 is the query part

        # Convert back to a URL string
        clean_url = iri_to_uri(parsed_url._replace(query=new_query).geturl())

        return HttpResponseRedirect(clean_url)

    return redirect("home")


def form_error_messages(form, request):
    """Display form errors as messages."""
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(
                request,
                f"{field.replace('_', ' ').title()}: {error}",
            )


def format_search_response(page, per_page, total_results, results):
    """Format the search response for pagination."""
    return {
        "page": page,
        "total_results": total_results,
        "total_pages": total_results // per_page + 1,
        "results": results,
    }


def is_released_date(air_date, current_date=None):
    """Return whether the supplied air date has already passed."""
    current_date = current_date or timezone.localdate()
    normalized_air_date = None

    if isinstance(air_date, datetime):
        if timezone.is_naive(air_date):
            normalized_air_date = air_date.date()
        else:
            normalized_air_date = timezone.localtime(air_date).date()
    elif isinstance(air_date, date):
        normalized_air_date = air_date
    elif isinstance(air_date, str):
        parts = air_date.split("-")
        if len(parts) == YEAR_ONLY_PARTS:
            air_date = f"{air_date}-01-01"
        elif len(parts) == YEAR_MONTH_PARTS:
            air_date = f"{air_date}-01"

        try:
            normalized_air_date = date.fromisoformat(air_date)
        except ValueError:
            return False
    else:
        return False

    return normalized_air_date <= current_date


def _needs_image_refresh(item, new_image):
    """Return True when ``item.image`` should be replaced with ``new_image``."""
    if item is None or not new_image or new_image == settings.IMG_NONE:
        return False
    return not item.image or item.image == settings.IMG_NONE


def refresh_item_image_if_missing(item, new_image):
    """Update an Item's stored image when it's missing and a real one is available."""
    if not _needs_image_refresh(item, new_image):
        return
    item.image = new_image
    item.save(update_fields=["image"])


def enrich_items_with_user_data(request, items, section_name):
    """Enrich a list of items with user tracking data."""
    if not items:
        return []

    # All items are the same media type
    media_type = items[0]["media_type"]
    media_lookup = _build_user_media_lookup(request.user, items, media_type)

    # Enrich items with matched media
    enriched_items = []
    items_to_refresh = []
    for item in items:
        if media_type == MediaTypes.SEASON.value:
            key = (str(item["media_id"]), item["source"], item.get("season_number"))
        else:
            key = (str(item["media_id"]), item["source"])

        media_item = media_lookup.get(key)
        if _should_skip_completed_recommendation(
            request.user, section_name, media_item
        ):
            continue

        if media_item is not None and _needs_image_refresh(
            media_item.item, item.get("image")
        ):
            media_item.item.image = item["image"]
            items_to_refresh.append(media_item.item)

        enriched_items.append({"item": item, "media": media_item})

    if items_to_refresh:
        Item.objects.bulk_update(items_to_refresh, ["image"])

    return enriched_items


def _build_user_media_lookup(user, items, media_type):
    """Fetch the user's media for ``items`` and return a {key: media} lookup."""
    source = items[0]["source"]

    q_objects = Q()
    for item in items:
        filter_params = {
            "item__media_id": item["media_id"],
            "item__media_type": media_type,
            "item__source": source,
        }
        if media_type == MediaTypes.SEASON.value:
            filter_params["item__season_number"] = item.get("season_number")
        q_objects |= Q(**filter_params)

    q_objects &= Q(user=user)

    model = apps.get_model(app_label="app", model_name=media_type)
    media_queryset = model.objects.filter(q_objects).select_related("item")
    media_queryset = BasicMedia.objects._apply_prefetch_related(
        media_queryset,
        media_type,
    )
    BasicMedia.objects.annotate_max_progress(media_queryset, media_type)

    media_lookup = {}
    for media in media_queryset:
        if media_type == MediaTypes.SEASON.value:
            key = (media.item.media_id, media.item.source, media.item.season_number)
        else:
            key = (media.item.media_id, media.item.source)
        media_lookup[key] = media

    return media_lookup


def _should_skip_completed_recommendation(user, section_name, media_item):
    """Return True when a completed recommendation should be hidden."""
    return (
        user.hide_completed_recommendations
        and section_name == "recommendations"
        and media_item is not None
        and media_item.status == Status.COMPLETED.value
    )
