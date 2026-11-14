# FORK: search endpoints for the native app, reusing the web search views'
# context builders. URL wiring lives in fork_urls.py.
import logging
import re
from collections.abc import Mapping
from http import HTTPStatus as HTTP  # noqa: N814

from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils.translation import gettext
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import views as drf_views
from rest_framework.response import Response

from app import helpers, music_views, search_views
from app.forms import ManualItemForm, PodcastShowTrackerForm, get_form_class
from app.icon_specs import type_icon
from app.models import TV, MediaTypes, PodcastShow, Season, Sources, Status
from app.services import metadata_resolution
from app.templatetags.app_tags import global_search_react
from users.models import ALL_SEARCH_TYPE, VALID_SEARCH_TYPES

from .contract_serializers import DetailErrorSerializer

logger = logging.getLogger(__name__)

PARENT_KINDS = {"tv", "season"}
PARENT_LIMIT = 5
MUSIC_REDIRECT = re.compile(r"/details/music/artist/(\d+)(?:/[^/]+/album/(\d+))?")


class ManualEntryView(drf_views.APIView):
    """Create a custom entry using the same forms as the web page."""

    @extend_schema(operation_id="manualEntryOptions", responses={200: dict})
    def get(self, request):
        """Return the web form's type and status choices."""
        return Response({
            "media_types": [{"value": value, "label": gettext(label), "icon": type_icon(value)} for value, label in MediaTypes.choices],
            "statuses": [{"value": value, "label": gettext(value)} for value in Status.values],
            "default_status": helpers.default_status_for_new_entry(),
            "ratings_enabled": request.user.ratings_enabled,
            "rating_scale_max": request.user.rating_scale_max,
        })

    @extend_schema(operation_id="createManualEntry", request=dict, responses={201: dict, 400: dict})
    def post(self, request):
        """Validate both forms before saving the item and its tracking row."""
        if request.user.is_demo:
            return Response({"detail": "This section is view-only for demo accounts."}, status=HTTP.FORBIDDEN)
        if not isinstance(request.data, Mapping):
            return Response({"detail": "Expected an object."}, status=HTTP.BAD_REQUEST)
        data = request.data.copy()
        form = ManualItemForm(data, user=request.user)
        if not form.is_valid():
            return Response({"errors": form.errors}, status=HTTP.BAD_REQUEST)
        item = form.save(commit=False)
        data.update({"source": item.source, "media_id": item.media_id})
        if item.media_type == MediaTypes.PODCAST.value:
            data["show_id"] = "0"
            tracking = PodcastShowTrackerForm(data, user=request.user)
        else:
            tracking = get_form_class(item.media_type)(data, user=request.user)
        if not tracking.is_valid():
            return Response({"errors": tracking.errors}, status=HTTP.BAD_REQUEST)
        try:
            with transaction.atomic():
                item.save()
                tracked = tracking.save(commit=False)
                tracked.user = request.user
                if item.media_type == MediaTypes.PODCAST.value:
                    show = PodcastShow.objects.create(
                        podcast_uuid=item.media_id, source=Sources.MANUAL.value,
                        title=item.title, image=item.image or "", description=item.synopsis or "",
                        rss_feed_url=form.cleaned_data.get("rss_feed_url") or "",
                    )
                    tracked.show = show
                else:
                    tracked.item = item
                    if item.media_type == MediaTypes.SEASON.value:
                        tracked.related_tv = form.cleaned_data["parent_tv"]
                    elif item.media_type == MediaTypes.EPISODE.value:
                        tracked.related_season = form.cleaned_data["parent_season"]
                tracked.save()
                if item.media_type == MediaTypes.PODCAST.value and show.rss_feed_url:
                    from app.tasks_podcast import refresh_podcast_show_from_rss_task

                    transaction.on_commit(
                        lambda: refresh_podcast_show_from_rss_task.delay(show.id),
                        robust=True,
                    )
        except IntegrityError:
            return Response({"errors": {"title": ["This entry already exists."]}}, status=HTTP.BAD_REQUEST)
        return Response({"id": item.id, "title": item.title}, status=HTTP.CREATED)


def _django_request(request):
    """Return the underlying Django request the web builders expect."""
    return getattr(request, "_request", request)


class _Notices:
    """Message storage that keeps the web view's notices in a list."""

    def __init__(self, notices):
        self.notices = notices

    def add(self, _level, message, _extra_tags=""):
        self.notices.append(str(message))


def _search_type(request):
    """Return the requested search type, falling back to the all-library type."""
    requested = request.GET.get("media_type") or ALL_SEARCH_TYPE
    if requested == ALL_SEARCH_TYPE or requested in VALID_SEARCH_TYPES:
        return requested
    return None


# /api/v1/search/sources/
class SearchSourcesView(drf_views.APIView):
    """Search media types and metadata sources the user can pick from."""

    @extend_schema(
        operation_id="listSearchSources",
        parameters=[
            OpenApiParameter(
                name="media_type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Media type to list sources for; `all` has none.",
            ),
        ],
        responses={200: dict, 400: DetailErrorSerializer},
    )
    def get(self, request):
        """Return the type menu, search bar copy and sources for one type."""
        media_type = _search_type(request)
        if media_type is None:
            return Response(
                {"detail": "media_type is not a searchable type."},
                status=HTTP.BAD_REQUEST,
            )
        bar = global_search_react(
            {"request": _django_request(request), "user": request.user},
            media_type,
        )["props"]
        sources = []
        default = ""
        if media_type != ALL_SEARCH_TYPE:
            sources = [
                {"value": option.value, "label": option.label}
                for option in metadata_resolution.available_metadata_sources(
                    media_type,
                    request.user,
                )
            ]
            default = metadata_resolution.metadata_default_source(
                request.user,
                media_type,
            )
            if sources and default not in {source["value"] for source in sources}:
                default = sources[0]["value"]
        return Response(
            {
                "media_type": media_type,
                "media_types": bar["mediaTypes"],
                "barcode_types": bar["barcodeTypes"],
                "labels": bar["labels"],
                "sources": sources,
                "default_source": default,
            },
            status=HTTP.OK,
        )


# /api/v1/search/type/
class SearchTypeView(drf_views.APIView):
    """Remember the media type picked in the search bar."""

    @extend_schema(
        operation_id="updateSearchType",
        request={"application/json": {"type": "object"}},
        responses={200: dict, 400: DetailErrorSerializer},
    )
    def put(self, request):
        """Save `{media_type}` as the user's last search type."""
        media_type = request.data.get("media_type")
        if media_type not in VALID_SEARCH_TYPES:
            return Response(
                {"detail": "media_type is not a searchable type."},
                status=HTTP.BAD_REQUEST,
            )
        saved = request.user.update_preference("last_search_type", media_type)
        return Response({"media_type": saved}, status=HTTP.OK)


# /api/v1/search/suggestions/
class SearchSuggestionsView(drf_views.APIView):
    """Typeahead suggestions from the user's saved library."""

    @extend_schema(
        operation_id="listSearchSuggestions",
        parameters=[
            OpenApiParameter(name="q", type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(
                name="media_type",
                type=str,
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={200: dict},
    )
    def get(self, request):
        """Return `{suggestions: [{id, title, subtitle, image, url}]}`."""
        query = request.GET.get("q", "").strip()
        media_type = request.GET.get("media_type") or ALL_SEARCH_TYPE
        if (
            len(query) < search_views.MIN_SUGGESTION_QUERY_LENGTH
            or media_type not in {*MediaTypes.values, ALL_SEARCH_TYPE}
        ):
            return Response({"suggestions": []}, status=HTTP.OK)
        try:
            suggestions = search_views.get_saved_suggestions(
                request.user,
                media_type,
                query,
            )
        except Exception:  # - typeahead must never fail the page
            logger.debug("Suggestion search failed", exc_info=True)
            suggestions = []
        return Response(
            {"suggestions": search_views._suggestion_json(suggestions)},
            status=HTTP.OK,
        )


# /api/v1/search/results/
class SearchResultsView(drf_views.APIView):
    """The search page's shared results props, library and online."""

    @extend_schema(
        operation_id="searchResults",
        parameters=[
            OpenApiParameter(name="q", type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(
                name="media_type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="A searchable type, or `all` for library-only search.",
            ),
            OpenApiParameter(
                name="source",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Metadata source; defaults to the user's default.",
            ),
            OpenApiParameter(name="page", type=int, location=OpenApiParameter.QUERY),
            OpenApiParameter(
                name="layout",
                type=str,
                location=OpenApiParameter.QUERY,
                enum=["grid", "list"],
            ),
        ],
        responses={200: dict, 400: DetailErrorSerializer},
    )
    def get(self, request):
        """Return the rows, provider rows to stream and empty state for a query."""
        if _search_type(request) is None:
            return Response(
                {"detail": "media_type is not a searchable type."},
                status=HTTP.BAD_REQUEST,
            )
        http_request = _django_request(request)
        # The web view reads `request.user` on the Django request.
        http_request.user = request.user
        if http_request.GET.get("row"):
            # One row's next page, or one provider's row, as the web page streams them.
            try:
                row, unavailable = search_views._search_row_page(http_request)
            except ValueError:
                return Response(
                    {"detail": "offset and page must be numbers."},
                    status=HTTP.BAD_REQUEST,
                )
            return Response({"row": row, "unavailable": unavailable}, status=HTTP.OK)
        errors = []
        # The web view reports provider errors as flash messages; collect them
        # for the payload instead of leaving them for a later page.
        storage = getattr(http_request, "_messages", None)
        http_request._messages = _Notices(errors)
        try:
            context = search_views._search_context(http_request)
        finally:
            http_request._messages = storage
        context["request"] = http_request
        context["user"] = request.user
        props = search_views.build_search_results_props(context)
        props["errors"] = errors
        if errors and not props["rows"] and not props["onlineSources"]:
            props["state"] = "error"
        return Response(props, status=HTTP.OK)


# /api/v1/search/parents/
class SearchParentsView(drf_views.APIView):
    """Manual TV shows or seasons a new episode or season can attach to."""

    @extend_schema(
        operation_id="searchParents",
        parameters=[
            OpenApiParameter(
                name="kind",
                type=str,
                location=OpenApiParameter.QUERY,
                enum=sorted(PARENT_KINDS),
            ),
            OpenApiParameter(name="q", type=str, location=OpenApiParameter.QUERY),
        ],
        responses={200: dict, 400: DetailErrorSerializer},
    )
    def get(self, request):
        """Return up to five `{id, title, label}` parents matching the query."""
        kind = request.GET.get("kind")
        if kind not in PARENT_KINDS:
            return Response(
                {"detail": "kind must be 'tv' or 'season'."},
                status=HTTP.BAD_REQUEST,
            )
        query = request.GET.get("q", "").strip()
        if len(query) <= 1:
            return Response({"query": query, "results": []}, status=HTTP.OK)
        if kind == "tv":
            parents = TV.objects.filter(
                user=request.user,
                item__source=Sources.MANUAL.value,
                item__media_type=MediaTypes.TV.value,
                item__title__icontains=query,
            ).select_related("item")[:PARENT_LIMIT]
            rows = [
                {
                    "id": parent.id,
                    "title": parent.item.title,
                    "label": parent.item.title,
                }
                for parent in parents
            ]
        else:
            seasons = Season.objects.filter(
                user=request.user,
                item__source=Sources.MANUAL.value,
                item__media_type=MediaTypes.SEASON.value,
                item__title__icontains=query,
            ).select_related("item")[:PARENT_LIMIT]
            rows = [
                {
                    "id": season.id,
                    "title": gettext("%(title)s - Season %(number)s")
                    % {
                        "title": season.item.title,
                        "number": season.item.season_number,
                    },
                    "label": gettext("Season %(number)s of %(title)s")
                    % {
                        "title": season.item.title,
                        "number": season.item.season_number,
                    },
                }
                for season in seasons
            ]
        return Response({"query": query, "results": rows}, status=HTTP.OK)


# /api/v1/search/music/create/
class SearchMusicCreateView(drf_views.APIView):
    """Create an artist or album from an online music search result."""

    @extend_schema(
        operation_id="createMusicFromSearch",
        request=dict,
        responses={
            201: dict,
            400: DetailErrorSerializer,
            502: DetailErrorSerializer,
        },
    )
    def post(self, request):
        """Run the web create-from-search flow and return the new row ids."""
        kind = request.data.get("kind")
        provider_id = str(request.data.get("id") or "").strip()
        if kind not in {"artist", "album"} or not provider_id:
            return Response(
                {"detail": "'kind' (artist or album) and 'id' are required."},
                status=HTTP.BAD_REQUEST,
            )
        view = (
            music_views.create_artist_from_search
            if kind == "artist"
            else music_views.create_album_from_search
        )
        fake = RequestFactory().get("/")
        fake.user = request.user
        try:
            response = view(fake, provider_id)
        except Exception:
            logger.warning("Music create-from-search failed", exc_info=True)
            return Response(
                {"detail": "The music provider could not create this entry."},
                status=HTTP.BAD_GATEWAY,
            )
        match = MUSIC_REDIRECT.search(getattr(response, "url", "") or "")
        if not match:
            return Response(
                {"detail": "The music provider returned no entry."},
                status=HTTP.BAD_GATEWAY,
            )
        return Response(
            {
                "artist_id": int(match.group(1)),
                "album_id": int(match.group(2)) if match.group(2) else None,
            },
            status=HTTP.CREATED,
        )
