# FORK: History endpoints the native app needs beyond GET /api/v1/history/:
# genre options, an item's sessions, one day's overflow, release-date days,
# editing/deleting a feed entry, and saving a History view. Each reuses the
# query and service code behind the web History page; URL wiring lives in
# fork_urls.py.
import json
import logging
from functools import partial
from http import HTTPStatus as HTTP  # noqa: N814

from django.core.exceptions import ObjectDoesNotExist
from django.http import QueryDict
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import views as drf_views
from rest_framework.response import Response

from app import history_cache
from app import history_views as web_history
from app.fork_services_history import (
    HistoryDeletionError,
    HistoryRecordNotFoundError,
    delete_history_record_core,
    delete_instance_without_history,
)
from app.history_entry_builders import _serialize_item, _serialize_show
from app.models import BasicMedia, MediaTypes
from integrations.state.ratings import rating_edit
from users.models import HISTORY_VIEW_TYPE, SavedView
from users.views import _saved_history_query

from .helpers import paginate_data, parse_limit_offset, validate_body

logger = logging.getLogger(__name__)

# Types whose feed entry is the tracked row itself, so deleting the entry
# deletes the play (same set the web delete core uses).
INSTANCE_ENTRY_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.EPISODE.value,
    MediaTypes.GAME.value,
    MediaTypes.BOARDGAME.value,
}
_EDITABLE_ENTRY_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.EPISODE.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
}
_FEED_PARAMS = [
    OpenApiParameter("media_type", str),
    OpenApiParameter("media_id", str),
    OpenApiParameter("source", str),
    OpenApiParameter("season_number", int),
    OpenApiParameter("album", int),
    OpenApiParameter("podcast_show", int),
    OpenApiParameter("genre", str),
    OpenApiParameter("implied_genre", str),
    OpenApiParameter("logging_style", str),
]


def django_request(request, **updates):
    """Hand the shared web helpers a plain request carrying the API user."""
    plain = request._request
    plain.user = request.user
    if updates:
        query = QueryDict(mutable=True)
        query.update(request.GET)
        for key, value in updates.items():
            query[key] = value
        plain.GET = query
    return plain


# /api/v1/history/genres/
class HistoryGenresView(drf_views.APIView):
    """Genre and implied-genre options for the History filter panel."""

    @extend_schema(operation_id="listHistoryGenres", responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        """Return the user's sorted genres and implied genres."""
        response = web_history.history_genres(django_request(request))
        return Response(_json(response), status=HTTP.OK)


def _json(response):
    return json.loads(response.content)


# /api/v1/history/sessions/
class HistorySessionsView(drf_views.APIView):
    """Every logged session of one item, with the activity stats."""

    @extend_schema(operation_id="listHistorySessions", parameters=_FEED_PARAMS, responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        """Return sessions, stats and per-day activity intensity for the filtered history."""
        response = web_history.activity_sessions_modal(
            django_request(request, format="json"),
        )
        return Response(_json(response), status=HTTP.OK)


# /api/v1/history/days/<day_key>/
class HistoryDayView(drf_views.APIView):
    """One bounded page of a day's entries, for days that overflow the feed."""

    @extend_schema(
        operation_id="getHistoryDay",
        parameters=[OpenApiParameter("entry_offset", int), *_FEED_PARAMS],
    responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, day_key):
        """Return one page of the day starting at entry_offset."""
        if history_cache.history_day_key(day_key) != day_key:
            return Response({"detail": "Invalid history day key."}, status=HTTP.BAD_REQUEST)
        try:
            history_cache._date_from_day_key(day_key)
            offset = int(request.GET.get("entry_offset", "0"))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid history day key or offset."}, status=HTTP.BAD_REQUEST)
        if offset < 0:
            return Response({"detail": "Invalid history entry offset."}, status=HTTP.BAD_REQUEST)
        plain = django_request(request)
        filters, logging_style = web_history._parse_history_filters(plain)
        day = history_cache.get_cached_history_day(
            request.user,
            day_key,
            logging_style_override=logging_style,
            entry_offset=offset,
            max_entries=history_cache.HISTORY_ENTRIES_PER_DAY_PAGE,
            entry_filter=partial(
                web_history._cached_history_entry_is_visible,
                filters=filters,
                allowed_types=web_history._enabled_history_media_types(request.user),
            ),
        )
        if day is None:
            return Response({"detail": "History day not found."}, status=HTTP.NOT_FOUND)
        return Response({"day": day}, status=HTTP.OK)


# /api/v1/history/release/
class HistoryReleaseDaysView(drf_views.APIView):
    """Days grouped by release date instead of play date."""

    @extend_schema(
        operation_id="listHistoryReleaseDays",
        parameters=[
            OpenApiParameter("start_date", str),
            OpenApiParameter("end_date", str),
            OpenApiParameter("media_type", str),
            OpenApiParameter("genre", str),
            OpenApiParameter("implied_genre", str),
            OpenApiParameter("limit", int, description="Days per page."),
            OpenApiParameter("offset", int, description="Days to skip."),
        ],
    responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        """Return one page of release-date days, newest first.

        Paged by day like the web page so the response stays bounded
        (docs/architecture/history-memory.md).
        """
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err
        plain = django_request(request)
        filters, _style = web_history._parse_history_filters(plain)
        date_filters = {
            key: request.GET[key]
            for key in ("start_date", "end_date")
            if request.GET.get(key)
        }
        days = web_history._build_release_history_days(
            request.user,
            date_filters=date_filters,
            filters=filters,
        )
        page = paginate_data(request, days, limit, offset)
        for day in page["results"]:
            for entry in day["entries"]:
                entry["item"] = _serialize_item(entry.get("item"))
                if entry.get("show") is not None:
                    entry["show"] = _serialize_show(entry["show"])
        return Response(page, status=HTTP.OK)


# /api/v1/history/entries/<media_type>/<instance_id>/
class HistoryEntryView(drf_views.APIView):
    """Edit or delete the tracked row behind one History feed entry.

    ``instance_id`` is the feed entry's own ``instance_id``; no history-record
    id is needed. Only the fields the web edit form changes are accepted.
    """

    def _instance(self, request, media_type, instance_id):
        if media_type not in _EDITABLE_ENTRY_TYPES:
            return None, Response(
                {"detail": "This history entry cannot be changed."},
                status=HTTP.BAD_REQUEST,
            )
        try:
            return BasicMedia.objects.get_media(request.user, media_type, instance_id), None
        except (ObjectDoesNotExist, ValueError):
            return None, Response(
                {"detail": "History entry not found."},
                status=HTTP.NOT_FOUND,
            )

    @extend_schema(operation_id="updateHistoryEntry", request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
    @rating_edit
    def patch(self, request, media_type, instance_id):
        """Update date, score, status or notes of the entry."""
        instance, error = self._instance(request, media_type, instance_id)
        if error:
            return error
        body = request.data.dict() if hasattr(request.data, "dict") else dict(request.data or {})
        if "date" in body and "end_date" not in body:
            body["end_date"] = body.pop("date")
        validated, problem = validate_body(body, media_type)
        if problem:
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(problem)},
                status=HTTP.BAD_REQUEST,
            )
        for field, value in validated.items():
            if hasattr(instance, field):
                setattr(instance, field, value)
        instance.save()
        return Response(
            {
                "instance_id": instance.id,
                "media_type": media_type,
                "end_date": getattr(instance, "end_date", None),
                "score": getattr(instance, "score", None),
                "status": getattr(instance, "status", None),
            },
            status=HTTP.OK,
        )

    @extend_schema(operation_id="deleteHistoryEntry", responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, media_type, instance_id):
        """Delete the play behind the entry (play-per-instance types only)."""
        if media_type not in INSTANCE_ENTRY_TYPES:
            return Response(
                {"detail": "This history entry cannot be deleted."},
                status=HTTP.BAD_REQUEST,
            )
        instance, error = self._instance(request, media_type, instance_id)
        if error:
            return error
        historical = instance.history.order_by("-history_date").first()
        if historical is None:
            delete_instance_without_history(request.user, media_type, instance)
            return Response(status=HTTP.NO_CONTENT)
        try:
            delete_history_record_core(request.user, media_type, historical.history_id)
        except HistoryRecordNotFoundError:
            return Response({"detail": "History record not found."}, status=HTTP.NOT_FOUND)
        except HistoryDeletionError as exc:
            return Response({"detail": exc.message}, status=HTTP.INTERNAL_SERVER_ERROR)
        return Response(status=HTTP.NO_CONTENT)


# /api/v1/history/saved-views/
class HistorySavedViewsView(drf_views.APIView):
    """Save the current History filters as a named sidebar view."""

    @extend_schema(operation_id="createHistorySavedView", request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        """Create a saved History view from {name, query}."""
        if request.user.is_demo:
            return Response(
                {"detail": "This section is view-only for demo accounts."},
                status=HTTP.FORBIDDEN,
            )
        name = str(request.data.get("name") or "").strip()[:100]
        if not name:
            return Response({"detail": "Give the view a name."}, status=HTTP.BAD_REQUEST)
        last = (
            SavedView.objects.filter(user=request.user, media_type=HISTORY_VIEW_TYPE)
            .order_by("-position")
            .first()
        )
        saved = SavedView.objects.create(
            user=request.user,
            media_type=HISTORY_VIEW_TYPE,
            name=name,
            query=_saved_history_query({"query": str(request.data.get("query") or "")}),
            position=last.position + 1 if last else 0,
        )
        return Response(
            {"id": saved.id, "name": saved.name, "query": saved.query},
            status=HTTP.CREATED,
        )
