import asyncio
from http import HTTPStatus as HTTP  # noqa: N814

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.utils.timezone import datetime, localdate, make_aware
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from health_check.views import HealthCheckView
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from app.forms import ManualItemForm, get_form_class
from app.models import BasicMedia, Item, MediaTypes, Sources
from app.providers import services, tmdb
from app.statistics import (
    get_activity_data,
    get_media_type_distribution,
    get_score_distribution,
    get_status_distribution,
    get_status_pie_chart_data,
    get_timeline,
    get_user_media,
)
from events import tasks
from events.models import Event
from lists.models import CustomList, CustomListItem
from users.models import MediaStatusChoices

from .authentication import APIKeyAuthentication, BearerAuthentication
from .changes_history_processor import (
    delete_changes_history_entry,
    get_changes_history_entries,
    get_changes_history_entry,
)
from .helpers import (
    MEDIA_TYPE_COMPLETE_MODEL_MAP,
    SOURCES_VALID_LIST,
    apply_aggregated_sort,
    apply_list_sort,
    build_lists_by_item_id,
    check_source_type,
    check_valid_type,
    fetch_results_all_types,
    fetch_results_for_type,
    get_item_lists,
    get_media_status,
    get_sorts,
    paginate_data,
    parse_excluded_items,
    parse_limit_offset,
    parse_sort_filter,
    parse_status_param,
    resolve_calendar_date_range,
    try_parse_date,
    validate_body,
)
from .schemas import (
    EpisodeNumberParam,
    ListSearchParam,
    ListSortParam,
    MediaIdParam,
    MediaTypeCompleteParam,
    MediaTypeParam,
    PaginationLimitParam,
    PaginationOffsetParam,
    SeasonNumberParam,
    SourceParam,
    forbidden_response,
)
from .serializers import (
    ApiErrorResponseSerializer,
    ApiMessageResponseSerializer,
    ChangesHistoryEntrySerializer,
    CompleteEpisodeSerializer,
    CompleteMediaSerializer,
    EpisodeSerializer,
    EventSerializer,
    GenericObjectSerializer,
    HealthResponseSerializer,
    HistorySerializer,
    InfoSerializer,
    ListCreateRequestSerializer,
    ListMinimizedSerializer,
    ListSerializer,
    ListUpdateRequestSerializer,
    MediaSerializer,
    MixedMediaSerializer,
    PaginatedChangesHistoryResponseSerializer,
    PaginatedEventsSerializer,
    PaginatedGenericResponseSerializer,
    PaginatedHistoryResponseSerializer,
    PaginatedListsMinimizedResponseSerializer,
    PaginatedListsResponseSerializer,
    PaginatedMediaSerializer,
    PaginatedPolymorphicMediaResponseSerializer,
    RelatedResponseSerializer,
    SearchResponseSerializer,
    StatisticsResponseSerializer,
    TimelineItemSerializer,
    UpdateAnimeSerializer,
    UpdateBoardGameSerializer,
    UpdateBookSerializer,
    UpdateComicSerializer,
    UpdateEpisodeSerializer,
    UpdateGameSerializer,
    UpdateMangaSerializer,
    UpdateMovieSerializer,
    UpdateSeasonSerializer,
    UpdateTVSerializer,
    serialize_data,
)

# TODO!: check sorters and filters in paginate_data since data is not serialized yet. Maybe data should be serialized first and then sorted/paginated later?? Sorting/filtering should occur at db search level, pagination should be done right after, always at the db search level, then the data should be serialized.  # noqa: E501, W505

# TODO!: for children items, it should return an error if user is trying to access a non existing season/episode (for example if it's requested the season 4 of a 2 season show)  # noqa: E501, W505

# TODO: Implement search for already tracked media (item_id and tracked fields)  # noqa: E501, FIX002, W505

# TODO: Implement global search endpoint for every media_type  # noqa: E501, FIX002, W505

# TODO: Implement admin commands to manage users (add admins, remove/add users, etc)  # noqa: E501, FIX002, W505

# TODO: Move operations on db to `models` file of the relative django app  # noqa: E501, FIX002, W505

# TODO!!: since it's possible to add to lists untracked items, the id field can be null, so it's impossible to get these elements from the list, while it should be possible. The untracked added element is in the Items table, but not in the media tables. Add the list of lists an item is in to the model of the medias, so they can be retrieved and computed easily.  # noqa: E501, FIX002, W505

# TODO: look into django.core.paginator Paginator  # noqa: FIX002

# TODO: Review children endpoints performance and avoid repeated list lookups per item.  # noqa: E501, FIX002, W505


# /api/v1/calendar/
class CalendarView(drf_views.APIView):
    """Calendar view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedEventsSerializer

    @extend_schema(
        operation_id="calendar_get",
        summary="Get events",
        parameters=[
            OpenApiParameter(
                name="start_date",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description="Filter range start date.",
            ),
            OpenApiParameter(
                name="end_date",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description=(
                    "Filter range end date. If omitted with start_date, "
                    "defaults to the end of that month; otherwise defaults "
                    "to the end of the selected/current month."
                ),
            ),
            OpenApiParameter(
                name="month",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description=(
                    "Calendar month (1-12) used with year. Used only "
                    "if start_date is not set. Default is current month."
                ),
            ),
            OpenApiParameter(
                name="year",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description=(
                    "Calendar year used with month. Used only "
                    "if start_date is not set. Default is current year."
                ),
            ),
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedEventsSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Events response example",
                        description="Events response example",
                        summary="Events response example",
                        value={
                            "pagination": {
                                "total": 2,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": 5086,
                                    "item": {
                                        "media_id": "208569",
                                        "source": "tmdb",
                                        "media_type": "episode",
                                        "title": "Will Trent",
                                        "image": "https://image.tmdb.org/t/p/w500/qG5O46gUxxYGImld03tl2zLhvrg.jpg",
                                        "season_number": 4,
                                        "episode_number": 13,
                                    },
                                    "item_id": "tv/tmdb/208569/4/13",
                                    "parent_id": "tv/tmdb/208569/4",
                                    "content_number": 13,
                                    "datetime": "2026-04-01T00:00:00Z",
                                    "notification_sent": False,
                                },
                                {
                                    "id": 14438,
                                    "item": {
                                        "media_id": "75219",
                                        "source": "tmdb",
                                        "media_type": "episode",
                                        "title": "9-1-1",
                                        "image": "https://image.tmdb.org/t/p/w500/2hFiCrn4XtvvTGlZQdLzGhnaOsg.jpg",
                                        "season_number": 9,
                                        "episode_number": 16,
                                    },
                                    "item_id": "tv/tmdb/75219/9/16",
                                    "parent_id": "tv/tmdb/75219/9",
                                    "content_number": 16,
                                    "datetime": "2026-04-03T00:00:00Z",
                                    "notification_sent": False,
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid date format example",
                        description="Invalid date format example",
                        summary="Invalid date format example",
                        value={"detail": "Invalid date format."},
                    )
                ],
            ),
            403: forbidden_response,
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching events",
                        description="Error while fetching events example",
                        summary="Error while fetching events example",
                        value={
                            "detail": "Error occurred while fetching events.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request):
        """Retrieve calendar events."""
        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")
        month_q = request.GET.get("month")
        year_q = request.GET.get("year")

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        try:
            first_day, last_day = resolve_calendar_date_range(
                start_date,
                end_date,
                month_q,
                year_q,
            )
        except (TypeError, ValueError):
            return Response(
                {"detail": "Invalid date format."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            releases = Event.objects.get_user_events(request.user, first_day, last_day)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Error occurred while fetching events.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        paginated_data = paginate_data(request, releases, limit, offset)
        paginated_data["results"] = EventSerializer(
            paginated_data["results"],
            many=True,
        ).data

        return Response(paginated_data)


# /api/v1/calendar/update/
class CalendarUpdateView(drf_views.APIView):
    """Update calendar view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ApiMessageResponseSerializer

    @extend_schema(
        operation_id="calendar_update_post",
        summary="Trigger calendar update",
        request=None,
        responses={
            202: OpenApiResponse(
                ApiMessageResponseSerializer,
                description="Task queued successfully",
                examples=[
                    OpenApiExample(
                        "Task queued example",
                        description="Task queued example",
                        summary="Task queued example",
                        value={"detail": "Task queued"},
                    )
                ],
            ),
            403: forbidden_response,
        },
    )
    def post(self, request):
        """Trigger calendar events update."""
        tasks.reload_calendar.delay(request.user)
        return Response(
            {"detail": "Task queued"},
            status=HTTP.ACCEPTED,
        )


# /api/v1/changes_history/[media_type]/[history_id]
class MediaTypeChangesHistoryDetailView(drf_views.APIView):
    """Changes history record view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ChangesHistoryEntrySerializer

    @extend_schema(
        operation_id="changes_history_entry_get",
        summary="Get changes history record",
        parameters=[
            MediaTypeCompleteParam,
            OpenApiParameter(
                name="history_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="The ID of the changes history record to retrieve.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                ChangesHistoryEntrySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Changes history record example",
                        description="Changes history record example",
                        summary="Changes history record example",
                        value={
                            "id": 312,
                            "item_id": "tv/tmdb/245703",
                            "timestamp": "2026-01-18T15:21:02.920479Z",
                            "changes": [
                                {"field": "status", "old_value": 3, "new_value": 1}
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "History record not found example",
                        description="History record not found example",
                        summary="History record not found example",
                        value={
                            "detail": "History record not found",
                            "errors": "HistoricalTV matching query does not exist.",
                        },
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching history record example",
                        description="Error while fetching history record example",
                        summary="Error while fetching history record example",
                        value={
                            "detail": "An error occurred while fetching the history record.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, history_id):
        """Retrieve the changes history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            record = get_changes_history_entry(media_type, history_id, request.user)

            serialized_data = ChangesHistoryEntrySerializer(
                record, context={"media_type": media_type}
            ).data
            return Response(serialized_data, status=HTTP.OK)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "History record not found",
                    "errors": str(e),
                },
                status=HTTP.NOT_FOUND,
            )

    @extend_schema(
        operation_id="changes_history_entry_delete",
        summary="Delete changes history record",
        parameters=[
            MediaTypeCompleteParam,
            OpenApiParameter(
                name="history_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="The ID of the changes history record to delete.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="History record deleted successfully",
                examples=[
                    OpenApiExample(
                        "History record deleted example",
                        description="History record deleted example",
                        summary="History record deleted example",
                        value=None,
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "History record not found example",
                        description="History record not found example",
                        summary="History record not found example",
                        value={
                            "detail": "History record not found",
                            "errors": "HistoricalTV matching query does not exist.",
                        },
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while deleting history record example",
                        description="Error while deleting history record example",
                        summary="Error while deleting history record example",
                        value={
                            "detail": "An error occurred while deleting the history record.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def delete(self, request, media_type, history_id):
        """Delete the changes history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            delete_changes_history_entry(media_type, history_id, request.user)
            return Response(
                {"detail": "Record removed correctly"},
                status=HTTP.NO_CONTENT,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "History record not found",
                    "errors": str(e),
                },
                status=HTTP.NOT_FOUND,
            )


# /api/v1/health/
class HealthView(drf_views.APIView):
    """Health check view."""

    authentication_classes = []
    permission_classes = []
    serializer_class = HealthResponseSerializer

    checks = HealthCheckView.checks

    def get_checks(self):
        """Return instantiated health checks using the installed library."""
        helper_view = HealthCheckView()
        helper_view.checks = self.checks
        return list(helper_view.get_checks())

    async def _collect_health_results(self):
        """Run all health checks and return their results."""
        return await asyncio.gather(
            *(check.get_result() for check in self.get_checks())
        )

    @extend_schema(
        operation_id="health_get",
        summary="Check API health status",
        responses={
            200: OpenApiResponse(
                HealthResponseSerializer,
                description="API is healthy",
                examples=[
                    OpenApiExample(
                        "Healthy API example",
                        description="Healthy API example",
                        summary="Healthy API example",
                        value={
                            "status": "ok",
                            "timestamp": "2026-04-28T08:49:33.826808+00:00",
                            "checks": {
                                "Cache(alias='default')": {
                                    "status": "ok",
                                    "error": None,
                                },
                                "Database(alias='default')": {
                                    "status": "ok",
                                    "error": None,
                                },
                                "Storage(alias='default')": {
                                    "status": "ok",
                                    "error": None,
                                },
                            },
                        },
                    )
                ],
            ),
            500: OpenApiResponse(
                HealthResponseSerializer,
                description="API is unhealthy",
                examples=[
                    OpenApiExample(
                        "Unhealthy API example",
                        description="Unhealthy API example",
                        summary="Unhealthy API example",
                        value={
                            "status": "unavailable",
                            "timestamp": "2026-04-28T08:49:33.826808+00:00",
                            "checks": {
                                "Cache(alias='default')": {
                                    "status": "ok",
                                    "error": None,
                                },
                                "Database(alias='default')": {
                                    "status": "ok",
                                    "error": None,
                                },
                                "DNS(hostname='laptop')": {
                                    "status": "error",
                                    "error": "OK",
                                },
                                "Mail(backend='django.core.mail.backends.smtp.EmailBackend')": {
                                    "status": "error",
                                    "error": "OK",
                                },
                                "Storage(alias='default')": {
                                    "status": "ok",
                                    "error": None,
                                },
                            },
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request):  # noqa: ARG002
        """Check API health status."""
        # TODO: speed up data collection, right now request takes ~2s
        results = asyncio.run(self._collect_health_results())
        errors = [result.error for result in results if result.error]
        plugins = {}
        for result in results:
            plugin = result.check
            plugin.errors = [result.error] if result.error else []
            plugins[repr(plugin)] = plugin
        health_data = {
            "plugins": plugins,
            "errors": errors,
        }
        response_data = HealthResponseSerializer(health_data).data
        status_code = HTTP.INTERNAL_SERVER_ERROR if errors else HTTP.OK
        return Response(response_data, status=status_code)


# /api/v1/info/
class InfoView(drf_views.APIView):
    """Info endpoint."""

    authentication_classes = []
    permission_classes = []
    serializer_class = InfoSerializer

    @extend_schema(
        operation_id="info_get",
        summary="Get application information",
        responses={
            200: OpenApiResponse(
                InfoSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Info response example",
                        description="Info response example",
                        summary="Info response example",
                        value={
                            "version": "dev",
                            "debug": True,
                            "frontend_url": "http://localhost:8000",
                            "language": "en-us",
                            "timezone": "UTC",
                            "admin_enabled": True,
                            "track_time": True,
                        },
                    )
                ],
            )
        },
    )
    def get(self, request):  # noqa: ARG002
        """Get application information."""
        response_data = InfoSerializer({}).data
        return Response(response_data, status=HTTP.OK)


# /api/v1/lists/
class ListsView(drf_views.APIView):
    """Lists view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedListsResponseSerializer

    @extend_schema(
        operation_id="lists_get",
        summary="Get user's lists",
        parameters=[
            ListSearchParam,
            ListSortParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedListsResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieve lists example",
                        description="Retrieve lists example",
                        summary="Retrieve lists example",
                        value={
                            "pagination": {
                                "total": 2,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": 1,
                                    "name": "Test1",
                                    "description": "This is a test list",
                                    "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                                    "owner": {"id": 1, "username": "admin"},
                                    "collaborators": [],
                                    "items_count": 17,
                                    "latest_update": "2026-03-23T21:17:32.705709Z",
                                },
                                {
                                    "id": 2,
                                    "name": "Test2",
                                    "description": "Ciaoo",
                                    "image": "https://image.tmdb.org/t/p/w500/xunXvzFlkf1GGgMkCySA9CCFumB.jpg",
                                    "owner": {"id": 1, "username": "admin"},
                                    "collaborators": [],
                                    "items_count": 27,
                                    "latest_update": "2026-04-08T15:58:11.652660Z",
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid pagination example",
                        description="Invalid pagination example",
                        summary="Invalid pagination example",
                        value={"detail": "Invalid pagination parameters."},
                    )
                ],
            ),
            403: forbidden_response,
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching history record example",
                        description="Error while fetching history record example",
                        summary="Error while fetching history record example",
                        value={
                            "detail": "An error occurred while fetching the history record.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request):
        """Retrieve the lists for the authenticated user."""
        user = request.user
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        custom_lists = CustomList.objects.get_user_lists_with_stats(
            user,
            search=search,
        )

        sort, sort_order = parse_sort_filter(sort_filter)
        sorted_lists = apply_list_sort(custom_lists, sort, sort_order)
        if sorted_lists is None:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.NOT_FOUND,
            )

        paginated_data = paginate_data(request, sorted_lists, limit, offset)
        paginated_data["results"] = ListSerializer(
            paginated_data["results"],
            many=True,
            context={"include_items": False},
        ).data
        return Response(paginated_data, status=HTTP.OK)

    @extend_schema(
        operation_id="lists_post",
        summary="Create lists",
        request=ListCreateRequestSerializer,
        responses={
            201: OpenApiResponse(
                ListSerializer,
                description="Successfully created",
                examples=[
                    OpenApiExample(
                        "Created list example",
                        description="Created list example",
                        summary="Created list example",
                        value={
                            "id": 1,
                            "name": "Test1",
                            "description": "This is a test list",
                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                            "owner": {"id": 1, "username": "admin"},
                            "collaborators": [],
                            "items_count": 1,
                            "latest_update": "2026-03-23T21:17:32.705709Z",
                            "items": {
                                "pagination": {
                                    "total": 1,
                                    "limit": 20,
                                    "offset": 0,
                                    "next": None,
                                    "previous": None,
                                },
                                "results": [
                                    {
                                        "id": 2902,
                                        "consumption_id": 1,
                                        "item": {
                                            "media_id": "1",
                                            "source": "manual",
                                            "media_type": "comic",
                                            "title": "Comic 1",
                                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                                            "season_number": None,
                                            "episode_number": None,
                                        },
                                        "item_id": "comic/manual/1",
                                        "parent_id": None,
                                        "tracked": True,
                                        "created_at": "2026-03-23T21:16:16.978287Z",
                                        "score": None,
                                        "status": 3,
                                        "progress": 0,
                                        "progressed_at": "2026-03-23T21:16:16.965721Z",
                                        "start_date": None,
                                        "end_date": None,
                                        "notes": "",
                                        "lists": [{"list_id": 1, "list_item_id": 16}],
                                    },
                                ],
                            },
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Missing body example",
                        description="Missing body example",
                        summary="Missing body example",
                        value={"detail": "Missing body."},
                    )
                ],
            ),
            403: forbidden_response,
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching history record example",
                        description="Error while fetching history record example",
                        summary="Error while fetching history record example",
                        value={
                            "detail": "An error occurred while fetching the history record.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def post(self, request):
        """Create a new custom list."""
        user = request.user
        body = request.data

        if not body:
            return Response(
                {"detail": "Missing body."},
                status=HTTP.BAD_REQUEST,
            )

        name = body.get("name", "").strip()
        if not name:
            return Response(
                {"detail": "Field 'name' is required."},
                status=HTTP.BAD_REQUEST,
            )
        description = body.get("description", "")
        collaborator_ids = body.get("collaborators", [])

        if collaborator_ids and not isinstance(collaborator_ids, list):
            return Response(
                {
                    "detail": "Field 'collaborators' must be an array of user IDs.",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            # TODO: move to lists/models.py
            custom_list = CustomList.objects.create(
                name=name,
                description=description,
                owner=user,
            )

            if collaborator_ids:
                collaborators = get_user_model().objects.filter(id__in=collaborator_ids)

                if collaborators.count() != len(collaborator_ids):
                    custom_list.delete()
                    return Response(
                        {
                            "detail": "One or more collaborator IDs are invalid.",
                        },
                        status=HTTP.BAD_REQUEST,
                    )

                custom_list.collaborators.set(collaborators)

            serialized_data = ListSerializer(
                custom_list,
            ).data
            return Response(serialized_data, status=HTTP.CREATED)

        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "An error occurred while creating the list.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/lists/[list_id]/
class ListDetailView(drf_views.APIView):
    """List detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ListSerializer

    @extend_schema(
        operation_id="list_detail_delete",
        summary="Delete a specific list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to delete.",
            ),
        ],
        responses={
            204: OpenApiResponse(description="List deleted successfully"),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while deleting list example",
                        description="Error while deleting list example",
                        summary="Error while deleting list example",
                        value={
                            "detail": "An error occurred while deleting the list.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def delete(self, request, list_id):
        """Delete a specific custom list."""
        user = request.user

        try:
            custom_list = CustomList.objects.get(id=list_id)
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not custom_list.user_can_delete(user):
            return Response(
                {
                    "detail": "You do not have permission to delete this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        custom_list.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="list_detail_get",
        summary="Get details of a specific list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search query to filter list items by title.",
            ),
            OpenApiParameter(
                name="sort",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Sort field and order.",
                enum=[
                    suffix_sort
                    for sort in get_sorts(None, sort_type="all")
                    for suffix_sort in (f"{sort}_asc", f"{sort}_desc")
                ],
            ),
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                ListSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list example",
                        description="Retrieved list example",
                        summary="Retrieved list example",
                        value={
                            "id": 1,
                            "name": "Test1",
                            "description": "This is a test list",
                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                            "owner": {"id": 1, "username": "admin"},
                            "collaborators": [],
                            "items_count": 1,
                            "latest_update": "2026-03-23T21:17:32.705709Z",
                            "items": {
                                "pagination": {
                                    "total": 0,
                                    "limit": 20,
                                    "offset": 0,
                                    "next": None,
                                    "previous": None,
                                },
                                "results": [],
                            },
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid pagination example",
                        description="Invalid pagination example",
                        summary="Invalid pagination example",
                        value={"detail": "Invalid pagination parameters."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching list example",
                        description="Error while fetching list example",
                        summary="Error while fetching list example",
                        value={
                            "detail": "An error occurred while fetching the list.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request, list_id):
        """Retrieve details and paginated items of a specific list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("collaborators", "items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_view(user):
            return Response(
                {
                    "detail": "You do not have permission to view this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        items = user_list.items.all()

        search_query = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        # TODO: move to lists/models.py
        if search_query:
            items = items.filter(title__icontains=search_query)

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        media_objects = []
        for item in items:
            # Shows info about the last consumption of the media if it's tracked
            media = BasicMedia.objects.filter_media_prefetch(
                user,
                item.media_id,
                item.media_type,
                item.source,
                season_number=item.season_number,
                episode_number=item.episode_number,
            ).first()

            media_objects.append(media if media is not None else item)

        if sort_filter:
            sort, sort_order = parse_sort_filter(sort_filter)
            if sort not in get_sorts(None, sort_type="all"):
                return Response(
                    {"detail": "Invalid sorting"},
                    status=HTTP.BAD_REQUEST,
                )
            media_objects = apply_aggregated_sort(media_objects, sort)
            if isinstance(media_objects, Response):
                return media_objects
            if sort_order == "desc":
                media_objects.reverse()

        paginated_data = paginate_data(request, media_objects, limit, offset)
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_list = ListSerializer(
            user_list,
            context={
                "paginated_items": paginated_data,
                "lists_by_item_id": lists_by_item_id,
            },
        ).data

        return Response(serialized_list, status=HTTP.OK)

    @extend_schema(
        operation_id="list_detail_patch",
        summary="Update a specific list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve.",
            ),
        ],
        request=ListUpdateRequestSerializer,
        responses={
            200: OpenApiResponse(
                ListSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list example",
                        description="Retrieved list example",
                        summary="Retrieved list example",
                        value={
                            "id": 1,
                            "name": "Test1",
                            "description": "This is a test list",
                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                            "owner": {"id": 1, "username": "admin"},
                            "collaborators": [],
                            "items_count": 1,
                            "latest_update": "2026-03-23T21:17:32.705709Z",
                            "items": {
                                "pagination": {
                                    "total": 0,
                                    "limit": 20,
                                    "offset": 0,
                                    "next": None,
                                    "previous": None,
                                },
                                "results": [],
                            },
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid collaborators example",
                        description="Invalid collaborators example",
                        summary="Invalid collaborators example",
                        value={
                            "detail": "Field 'collaborators' must be an array of user IDs."
                        },
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching list example",
                        description="Error while fetching list example",
                        summary="Error while fetching list example",
                        value={
                            "detail": "An error occurred while fetching the list.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def patch(self, request, list_id):
        """Update a specific custom list."""
        user = request.user
        body = request.data

        try:
            # TODO: move to lists/models.py
            custom_list = CustomList.objects.get(id=list_id)
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not custom_list.user_can_edit(user):
            return Response(
                {
                    "detail": "You do not have permission to edit this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        name = body.get("name")
        description = body.get("description")
        collaborator_ids = body.get("collaborators")

        if name is not None:
            custom_list.name = name.strip()
        if description is not None:
            custom_list.description = description
        if collaborator_ids is not None:
            if not isinstance(collaborator_ids, list):
                return Response(
                    {
                        "detail": "Field 'collaborators' must be an array of user IDs.",
                    },
                    status=HTTP.BAD_REQUEST,
                )
            collaborators = get_user_model().objects.filter(id__in=collaborator_ids)
            if collaborators.count() != len(collaborator_ids):
                return Response(
                    {
                        "detail": "One or more collaborator IDs are invalid.",
                    },
                    status=HTTP.BAD_REQUEST,
                )
            custom_list.collaborators.set(collaborators)

        custom_list.save()
        serialized_data = ListSerializer(custom_list).data
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/lists/[list_id]/items/
class ListItemsView(drf_views.APIView):
    """List items view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="list_items_get",
        summary="Get items of a list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve items from.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search query to filter list items by title.",
            ),
            OpenApiParameter(
                name="sort",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Sort field and order.",
                enum=[
                    suffix_sort
                    for sort in get_sorts(None, sort_type="all")
                    for suffix_sort in (f"{sort}_asc", f"{sort}_desc")
                ],
            ),
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedMediaSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list items example",
                        description="Retrieved list items example",
                        summary="Retrieved list items example",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": 2902,
                                    "consumption_id": 1,
                                    "item": {
                                        "media_id": "1",
                                        "source": "manual",
                                        "media_type": "comic",
                                        "title": "Comic 1",
                                        "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                                        "season_number": None,
                                        "episode_number": None,
                                    },
                                    "item_id": "comic/manual/1",
                                    "parent_id": None,
                                    "tracked": True,
                                    "created_at": "2026-03-23T21:16:16.978287Z",
                                    "score": None,
                                    "status": 3,
                                    "progress": 0,
                                    "progressed_at": "2026-03-23T21:16:16.965721Z",
                                    "start_date": None,
                                    "end_date": None,
                                    "notes": "",
                                    "lists": [{"list_id": 1, "list_item_id": 16}],
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid pagination example",
                        description="Invalid pagination example",
                        summary="Invalid pagination example",
                        value={"detail": "Invalid pagination parameters."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="List not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching list items example",
                        description="Error while fetching list items example",
                        summary="Error while fetching list items example",
                        value={
                            "detail": "An error occurred while fetching the list items.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request, list_id):
        """Get items of a list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_view(user):
            return Response(
                {
                    "detail": "You do not have permission to view this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        items = user_list.items.all()

        search_query = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        # TODO: move to lists/models.py
        if search_query:
            items = items.filter(title__icontains=search_query)

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        media_objects = []
        for item in items:
            # Shows info about the last consumption of the media if it's tracked
            media = BasicMedia.objects.filter_media_prefetch(
                user,
                item.media_id,
                item.media_type,
                item.source,
                season_number=item.season_number,
                episode_number=item.episode_number,
            ).first()

            media_objects.append(media if media is not None else item)

        if sort_filter:
            sort, sort_order = parse_sort_filter(sort_filter)
            if sort not in get_sorts(None, sort_type="all"):
                return Response(
                    {"detail": "Invalid sorting"},
                    status=HTTP.NOT_FOUND,
                )
            media_objects = apply_aggregated_sort(media_objects, sort)
            if isinstance(media_objects, Response):
                return media_objects
            if sort_order == "desc":
                media_objects.reverse()

        paginated_data = paginate_data(request, media_objects, limit, offset)
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = MixedMediaSerializer(
            paginated_data["results"],
            many=True,
            context={
                "serialize_items_as_media": True,
                "lists_by_item_id": lists_by_item_id,
            },
        ).data
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/lists/[list_id]/items/[item_id]/
class ListItemView(drf_views.APIView):
    """List item detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="list_item_delete",
        summary="Delete item from list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve items from.",
                required=True,
            ),
            OpenApiParameter(
                name="item_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of the item to delete",
                required=True,
            ),
        ],
        responses={
            204: OpenApiResponse(description="Item deleted successfully"),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid item ID example",
                        description="Invalid item ID example",
                        summary="Invalid item ID example",
                        value={"detail": "Invalid item ID."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Item not found in the list example",
                        description="Item not found in the list example",
                        summary="Item not found in the list example",
                        value={"detail": "Item not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while deleting item from list example",
                        description="Error while deleting item from list example",
                        summary="Error while deleting item from list example",
                        value={
                            "detail": "An error occurred while deleting the item from the list.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def delete(self, request, list_id, item_id):
        """Delete an item from a list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {
                    "detail": "You do not have permission to edit this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item(item_id, include_item=True)
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Item not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="list_item_get",
        summary="Get list item details",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve items from.",
                required=True,
            ),
            OpenApiParameter(
                name="item_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of the item to retrieve details of.",
                required=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                MediaSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list item example",
                        description="Retrieved list item example",
                        summary="Retrieved list item example",
                        value={
                            "id": 313,
                            "media_id": "105248",
                            "source": "tmdb",
                            "source_url": "https://www.themoviedb.org/tv/105248/season/1",
                            "media_type": "season",
                            "title": "Cyberpunk: Edgerunners",
                            "max_progress": 10,
                            "image": "https://image.tmdb.org/t/p/w500/6MMjX9T0L7eoRfZFTnzC6WXYZLK.jpg",
                            "synopsis": "In a dystopia riddled with corruption and cybernetic implants, a talented but reckless street kid strives to become a mercenary outlaw — an edgerunner.",
                            "genres": [
                                "Animation",
                                "Action & Adventure",
                                "Drama",
                                "Sci-Fi & Fantasy",
                            ],
                            "score": 8.3,
                            "score_count": 688,
                            "details": {
                                "first_air_date": "2022-09-12",
                                "last_air_date": "2022-09-13",
                                "episodes": 10,
                                "runtime": "26m",
                                "total_runtime": "4h 25m",
                                "tvdb_id": 384541,
                            },
                            "related": {
                                "episodes": [
                                    {
                                        "id": None,
                                        "consumption_id": None,
                                        "item": {
                                            "media_id": "105248",
                                            "source": "tmdb",
                                            "media_type": "episode",
                                            "title": "Let You Down",
                                            "image": "https://image.tmdb.org/t/p/original/egBHU73t79tMg2qrqj3aJof1ibS.jpg",
                                            "season_number": 1,
                                            "episode_number": 1,
                                        },
                                        "item_id": "tv/tmdb/105248/1/1",
                                        "parent_id": "tv/tmdb/105248/1",
                                        "tracked": False,
                                        "created_at": None,
                                        "score": None,
                                        "status": None,
                                        "progress": None,
                                        "progressed_at": None,
                                        "start_date": None,
                                        "end_date": None,
                                        "notes": None,
                                        "lists": [],
                                    }
                                ]
                            },
                            "item_id": "tv/tmdb/105248/1",
                            "parent_id": "tv/tmdb/105248",
                            "tracked": True,
                            "consumptions_number": 1,
                            "consumptions": [
                                {
                                    "consumption_id": 3,
                                    "created": "2026-01-15T15:33:03.302349Z",
                                    "score": None,
                                    "progress": 1,
                                    "progressed_at": "2026-01-15T20:13:00Z",
                                    "status": 1,
                                    "start_date": "2025-09-17T09:40:00Z",
                                    "end_date": "2026-01-15T20:13:00Z",
                                    "notes": "",
                                }
                            ],
                            "lists": [{"list_id": 1, "list_item_id": 1}],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid item ID example",
                        description="Invalid item ID example",
                        summary="Invalid item ID example",
                        value={"detail": "Invalid item ID."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Item not found in the list example",
                        description="Item not found in the list example",
                        summary="Item not found in the list example",
                        value={"detail": "Item not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching item details example",
                        description="Error while fetching item details example",
                        summary="Error while fetching item details example",
                        value={
                            "detail": "An error occurred while fetching the item details.",
                            "errors": "",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request, list_id, item_id):
        """Get details of a list item."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_view(user):
            return Response(
                {
                    "detail": "You don't have permission to view this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item(item_id, include_item=True)
            item = list_item.item
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Item not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        view_class = MediaDetailView
        extra_kwargs = {"media_type": item.media_type}

        if item.media_type == MediaTypes.SEASON.value:
            view_class = MediaSeasonDetailView
            extra_kwargs = {
                "media_type": MediaTypes.TV.value,
                "season_number": item.season_number,
            }
        elif item.media_type == MediaTypes.EPISODE.value:
            view_class = MediaEpisodeDetailView
            extra_kwargs = {
                "media_type": MediaTypes.TV.value,
                "season_number": item.season_number,
                "episode_number": item.episode_number,
            }

        # Call the appropriate media detail class to avoid code duplication
        return view_class().get(
            request,
            source=item.source,
            media_id=item.media_id,
            **extra_kwargs,
        )


# /api/v1/media/
class MediaListView(drf_views.APIView):
    """List media view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Retrieve the list of media for the authenticated user."""
        # TODO: check progress sort might not be working
        user = request.user
        media_type = request.GET.get("media_type")
        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        exclude = parse_excluded_items(request)

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        status = parse_status_param(status)
        if status is None:
            return Response(
                {"detail": "Invalid status"},
                status=HTTP.NOT_FOUND,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        if media_type:
            if not check_valid_type(media_type, complete=True):
                return Response(
                    {"detail": "Unsupported media type."},
                    status=HTTP.BAD_REQUEST,
                )
            results, has_error = fetch_results_for_type(
                user,
                media_type,
                status,
                sort,
                search,
            )
        else:
            # Exclude EPISODES and SEASONS from results by default
            # to declutter the results
            # TODO: Add an option to return those too? (seasons=true&episodes=false)
            results, has_error = fetch_results_all_types(
                user,
                status,
                sort,
                search,
                exclude,
            )

        if has_error:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.NOT_FOUND,
            )

        if isinstance(results, Response):
            return results

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset)
        # TODO: see if this can be optimized with a single query for all medias instead of one per episode  # noqa: E501, W505
        # TODO: see if lists infos can be saved in the `results` object to avoid using `context` to pass additional parameters  # noqa: E501, W505
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = serialize_data(
            paginated_data["results"],
            context={
                "request": request,
                "lists_by_item_id": lists_by_item_id,
            },
            many=True,
            homogeneous=False,
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/
class MediaTypeListView(drf_views.APIView):
    """List media by type view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type):
        """Retrieve the list of media of a specific media type."""
        user = request.user
        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        status = parse_status_param(status)
        if status is None:
            return Response(
                {"detail": "Invalid status"},
                status=HTTP.NOT_FOUND,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )
        results, has_error = fetch_results_for_type(
            user,
            media_type,
            status,
            sort,
            search,
        )

        if has_error:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.NOT_FOUND,
            )

        if isinstance(results, Response):
            return results

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset)
        # TODO: see if this can be optimized with a single query for all medias instead of one per episode  # noqa: E501, W505
        # TODO: see if lists infos can be saved in the `results` object to avoid using `context` to pass additional parameters  # noqa: E501, W505
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = serialize_data(
            paginated_data["results"],
            context={
                "request": request,
                "lists_by_item_id": lists_by_item_id,
            },
            many=True,
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)

    def post(self, request, media_type):
        """Track a new media item of a specific media type."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not request.data:
            return Response(
                {"detail": "Missing body."},
                status=HTTP.BAD_REQUEST,
            )

        body = request.data
        body["media_type"] = media_type
        body["status"] = (
            get_media_status(body["status"], reverse=True)
            if "status" in body
            # default status when tracking a new media will be "planning"
            else MediaStatusChoices.PLANNING
        )

        source = body.get("source", Sources.MANUAL.value)

        if source == Sources.MANUAL.value:
            form = ManualItemForm(body, user=request.user)
            if not form.is_valid():
                return Response(
                    {
                        "detail": "Invalid form data.",
                        "errors": form.errors,
                    },
                    status=HTTP.BAD_REQUEST,
                )

            try:
                item = form.save()
            except IntegrityError:
                media_name = form.cleaned_data.get("title", "item")
                if form.cleaned_data.get("season_number"):
                    media_name += f" - Season {form.cleaned_data['season_number']}"
                if form.cleaned_data.get("episode_number"):
                    media_name += f" - Episode {form.cleaned_data['episode_number']}"
                return Response(
                    {"detail": f"Conflict. {media_name} already exists."},
                    status=HTTP.CONFLICT,
                )

            media_data = dict(body)
            media_data.update({"source": item.source, "media_id": item.media_id})
            media_form = get_form_class(item.media_type)(media_data)
            if not media_form.is_valid():
                item.delete()
                return Response(
                    {
                        "detail": "Invalid media data.",
                        "errors": media_form.errors,
                    },
                    status=HTTP.BAD_REQUEST,
                )

            media_form.instance.user = request.user
            media_form.instance.item = item
            if item.media_type == MediaTypes.SEASON.value:
                media_form.instance.related_tv = form.cleaned_data.get("parent_tv")
            elif item.media_type == MediaTypes.EPISODE.value:
                media_form.instance.related_season = form.cleaned_data.get(
                    "parent_season",
                )

            media_form.save()
            serialized_data = serialize_data(media_form.instance)
            return Response(serialized_data, status=HTTP.CREATED)

        media_id = body.get("media_id")
        if not media_id:
            return Response(
                {
                    "detail": "'media_id' is required for provider sources.",
                },
                status=HTTP.BAD_REQUEST,
            )

        season_number = body.get("season_number")

        try:
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        defaults = {"title": metadata.get("title"), "image": metadata.get("image")}
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults=defaults,
        )

        try:
            item.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        model = MEDIA_TYPE_COMPLETE_MODEL_MAP.get(media_type)
        if model is None:
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        instance = model(item=item, user=request.user)

        media_data = dict(body)
        media_data.update({"source": item.source, "media_id": item.media_id})
        media_form = get_form_class(media_type)(media_data, instance=instance)
        if not media_form.is_valid():
            return Response(
                {
                    "detail": "Invalid media data.",
                    "errors": media_form.errors,
                },
                status=HTTP.BAD_REQUEST,
            )

        media_form.save()
        serialized_data = serialize_data(media_form.instance)
        return Response(serialized_data, status=HTTP.CREATED)


# /api/v1/media/[media_type]/[source]/[media_id]/
class MediaDetailView(drf_views.APIView):
    """Media view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, media_type, source, media_id):
        """Delete a tracked media item and all its consumptions."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    def get(self, request, media_type, source, media_id):
        """Retrieve details of a specific media for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            media_metadata["related"].pop("recommendations")

        seasons_by_number = None
        if media_type == MediaTypes.TV.value:
            serie_seasons = list(
                BasicMedia.objects.get_serie_seasons(
                    user,
                    media_id,
                    source,
                ),
            )
            season_lists_by_number = (
                BasicMedia.objects.get_serie_season_lists_by_number(
                    user,
                    serie_seasons,
                )
            )
            for tracked in serie_seasons:
                season_number = getattr(tracked.item, "season_number", None)
                if season_number is not None:
                    tracked.lists = season_lists_by_number.get(season_number, [])

            seasons_by_number = {
                tracked.item.season_number: tracked
                for tracked in serie_seasons
                if getattr(tracked, "item", None) is not None
                and tracked.item.season_number is not None
            }

        lists = get_item_lists(user, media_id, source, media_type)

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "seasons": seasons_by_number,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)

    def patch(self, request, media_type, source, media_id):
        """Update a tracked media item."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, media_type)

        if error:
            return Response(
                {"detail": f"{error}"},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Failed to update media.",
                    "errors": str(e),
                },
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            media_metadata["related"].pop("recommendations")

        lists = get_item_lists(user, media_id, source, media_type)

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/changes_history/
class MediaChangesHistoryView(drf_views.APIView):
    """Media changes history view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedChangesHistoryResponseSerializer

    @extend_schema(
        operation_id="media_changes_history_get",
        summary="Get media changes history",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedChangesHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        value={
                            "pagination": {
                                "total": 2,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": 312,
                                    "item_id": "tv/tmdb/245703",
                                    "timestamp": "2026-01-18T15:21:02.920479Z",
                                    "changes": [
                                        {
                                            "field": "status",
                                            "old_value": 3,
                                            "new_value": 1,
                                        }
                                    ],
                                },
                                {
                                    "id": 150,
                                    "item_id": "tv/tmdb/245703",
                                    "timestamp": "2025-09-17T09:41:00Z",
                                    "changes": [
                                        {
                                            "field": "score",
                                            "old_value": None,
                                            "new_value": 9.0,
                                        },
                                        {
                                            "field": "status",
                                            "old_value": None,
                                            "new_value": 3,
                                        },
                                        {
                                            "field": "notes",
                                            "old_value": None,
                                            "new_value": "",
                                        },
                                    ],
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve changes history timeline entries for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
        )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, media_type)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = ChangesHistoryEntrySerializer(
            paginated_data["results"],
            many=True,
            context={"media_type": media_type},
        ).data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/history/
class MediaConsumptionHistoryView(drf_views.APIView):
    """Media consumption history view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedHistoryResponseSerializer

    @extend_schema(
        operation_id="media_consumption_history_get",
        summary="Get media consumption history",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "consumption_id": 312,
                                    "created": "2026-03-19T10:31:55.747255Z",
                                    "score": 10.0,
                                    "progress": 26,
                                    "progressed_at": "2026-03-19T10:41:00Z",
                                    "status": 1,
                                    "start_date": "2026-03-19T10:31:00Z",
                                    "end_date": "2026-03-19T10:41:00Z",
                                    "notes": "aSDASDF",
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Season not found example",
                        description="Season not found or not tracked example",
                        summary="Season not found example",
                        value={"detail": "Season not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        # TODO: missing sorting
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = HistorySerializer(
            paginated_data["results"],
            many=True,
        )
        paginated_data["results"] = consumptions.data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/history/[consumption_id]/
class MediaConsumptionEntryDetailView(drf_views.APIView):
    """Media consumption history entry detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = HistorySerializer

    @extend_schema(
        operation_id="media_consumption_entry_delete",
        summary="Delete a media consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to delete",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Consumption entry deleted successfully",
                examples=[
                    OpenApiExample(
                        "Consumption entry deleted example",
                        description="Consumption entry deleted example",
                        summary="Consumption entry deleted example",
                        value=None,
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                    OpenApiExample(
                        "Consumption entry not found example",
                        description="Consumption entry not found example",
                        summary="Consumption entry not found example",
                        value={"detail": "Consumption entry not found."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def delete(self, request, media_type, source, media_id, consumption_id):
        """Delete a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        consumption.delete()

        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="media_consumption_entry_get",
        summary="Get a media consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to retrieve",
            ),
        ],
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                    OpenApiExample(
                        "Consumption entry not found example",
                        description="Consumption entry not found example",
                        summary="Consumption entry not found example",
                        value={"detail": "Consumption entry not found"},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, consumption_id):
        """Retrieve a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": " Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = HistorySerializer(
            consumption,
        ).data
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        operation_id="media_consumption_entry_patch",
        summary="Update a media consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to update",
            ),
        ],
        request=PolymorphicProxySerializer(
            component_name="HistoryUpdateRequest",
            serializers=[
                UpdateAnimeSerializer,
                UpdateBoardGameSerializer,
                UpdateBookSerializer,
                UpdateComicSerializer,
                UpdateGameSerializer,
                UpdateMangaSerializer,
                UpdateMovieSerializer,
                UpdateTVSerializer,
            ],
            resource_type_field_name=None,
        ),
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def patch(self, request, media_type, source, media_id, consumption_id):
        """Update a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, media_type)

        if error:
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(error)},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
                status=HTTP.BAD_REQUEST,
            )

        consumption.refresh_from_db()

        serialized_data = HistorySerializer(
            consumption,
        ).data
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/
class MediaListsView(drf_views.APIView):
    """Media lists view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedListsMinimizedResponseSerializer

    @extend_schema(
        operation_id="media_lists_get",
        summary="Get media lists",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedListsMinimizedResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieve lists example",
                        description="Retrieve lists example",
                        summary="Retrieve lists example",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [{"list_id": 2, "list_item_id": 28}],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the lists that a specific media is in."""
        user = request.user

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )
        # TODO: if media doesn't exist in the provider it should return 404
        lists = get_item_lists(user, media_id, source, media_type)
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/[list_id]/
class MediaListDetailView(drf_views.APIView):
    """Media list detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ListMinimizedSerializer

    @extend_schema(
        operation_id="media_list_detail_delete",
        summary="Remove media from a specific list",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Media removed from list successfully",
                examples=[
                    OpenApiExample(
                        "Media removed example",
                        description="Media removed example",
                        summary="Media removed example",
                        value=None,
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Media not found in list example",
                        description="Media not found in list example",
                        summary="Media not found in list example",
                        value={"detail": "Media not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def delete(self, request, media_type, source, media_id, list_id):
        """Remove a specific media from a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item_by_media(
                media_id,
                source,
                media_type,
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="media_list_detail_put",
        summary="Put media in a specific list",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(
                ListMinimizedSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Media added to list example",
                        description="Media added to list example",
                        summary="Media addedd to list example",
                        value=[{"list_id": 2, "list_item_id": 28}],
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Media not found in list example",
                        description="Media not found in list example",
                        summary="Media not found in list example",
                        value={"detail": "Media not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def put(self, request, media_type, source, media_id, list_id):
        """Add a specific media to a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
        except Item.DoesNotExist:
            return Response(
                {"detail": "Media not found."},
                status=HTTP.NOT_FOUND,
            )

        if user_list.items.filter(id=item.id).exists():
            return Response(
                {"detail": "Media already in the list."},
                status=HTTP.CONFLICT,
            )

        user_list.items.add(item)

        lists = get_item_lists(user, media_id, source, media_type)

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/recommendations/
class MediaRecommendationsView(drf_views.APIView):
    """Media recommendations view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RelatedResponseSerializer

    @extend_schema( 
        operation_id="media_recommendations_get",
        summary="Get media recommendations",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
        ],
        responses={
            200: OpenApiResponse(
                RelatedResponseSerializer(many=True),
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieve recommendations example",
                        description="Retrieve recommendations example",
                        summary="Retrieve recommendations example",
                        value=[
                            {
                                "source": "tmdb",
                                "media_type": "tv",
                                "image": "https://image.tmdb.org/t/p/w500/pFao5i4giBsGl7QQBXi7oMUPzCn.jpg",
                                "media_id": 167,
                                "title": "La Femme Nikita",
                            },
                            {
                                "source": "tmdb",
                                "media_type": "tv",
                                "image": "https://image.tmdb.org/t/p/w500/f7xmk6fNp0hXcGI9k0vXeX69Afq.jpg",
                                "media_id": 45576,
                                "title": "Hunted",
                            },
                        ],
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Media not found in list example",
                        description="Media not found in list example",
                        summary="Media not found in list example",
                        value={"detail": "Media not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, _, media_type, source, media_id):
        """Retrieve recommendations for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        recommendations = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            recommendations = media_metadata["related"]["recommendations"]

        return Response(recommendations, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/seasons/
class MediaSeasonsView(drf_views.APIView):
    """Media seasons view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
        user = request.user
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type) or media_type != MediaTypes.TV.value:
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        seasons = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "seasons" in media_metadata["related"]
        ):
            seasons = media_metadata["related"]["seasons"]

        paginated_data = paginate_data(request, seasons, limit, offset)
        lists_by_number = {}
        for season in paginated_data["results"]:
            season_number = season.get("season_number")
            if season_number is None:
                continue

            lists_by_number[season_number] = get_item_lists(
                user,
                media_id,
                source,
                MediaTypes.SEASON.value,
                season_number=season_number,
            )

        season_numbers = [
            season.get("season_number")
            for season in paginated_data["results"]
            if season.get("season_number") is not None
        ]

        items_by_number = {
            item.season_number: item
            for item in Item.objects.filter(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                season_number__in=season_numbers,
            )
        }

        tracked_by_number = {}
        if season_numbers:
            tracked_seasons = BasicMedia.objects.get_serie_seasons(
                user,
                media_id,
                source,
                season_numbers=season_numbers,
            )
            for tracked in tracked_seasons:
                item = getattr(tracked, "item", None)
                tracked_number = getattr(item, "season_number", None)
                if (
                    tracked_number is not None
                    and tracked_number in season_numbers
                    and tracked_number not in tracked_by_number
                ):
                    tracked_by_number[tracked_number] = tracked

        season_media_entries = []
        for season in paginated_data["results"]:
            season_number = season.get("season_number")
            tracked = tracked_by_number.get(season_number)
            lists = lists_by_number.get(season_number, [])

            if tracked is not None:
                tracked.lists = lists
                if getattr(tracked, "item", None) is None:
                    tracked.item = items_by_number.get(season_number)
                season_media_entries.append(tracked)
                continue

            item = items_by_number.get(season_number)
            if item is None:
                item = Item(
                    media_id=media_id,
                    source=source,
                    media_type=MediaTypes.SEASON.value,
                    title=season.get("season_title") or season.get("title") or "",
                    image=season.get("image") or settings.IMG_NONE,
                    season_number=season_number,
                )

            season_media_entries.append(
                type(
                    "TempMedia",
                    (),
                    {
                        "id": None,
                        "item": item,
                        "lists": lists,
                        "created_at": None,
                        "score": None,
                        "status": None,
                        "progress": None,
                        "progressed_at": None,
                        "start_date": None,
                        "end_date": None,
                        "notes": None,
                    },
                )(),
            )

        paginated_data["results"] = serialize_data(
            season_media_entries,
            many=True,
            context={
                "request": request,
            },
            serializer_class=MediaSerializer,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/sync/
class MediaSyncView(drf_views.APIView):
    """Sync media view."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, _, media_type, source, media_id):
        """Trigger sync of metadata from provider (non-manual sources only)."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if source == Sources.MANUAL.value:
            return Response(
                {"detail": "Manual items cannot be synced."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot sync `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        cache_key = f"{source}_{media_type}_{media_id}"

        ttl = cache.ttl(cache_key)
        if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
            response = Response(
                {
                    "detail": (
                        "The data was recently synced, please wait a few seconds."
                    ),
                },
                status=HTTP.TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = str(ttl)
            return response

        cache.delete(cache_key)

        try:
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
            )

            item, _ = Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults={
                    "title": metadata["title"],
                    "image": metadata["image"],
                },
            )

            item.fetch_releases(delay=False)

            return Response(
                {"detail": "Metadata synced successfully."},
                status=HTTP.ACCEPTED,
            )

        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/
class MediaSeasonDetailView(drf_views.APIView):
    """Season view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, media_type, source, media_id, season_number):
        """Delete a tracked season item for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve details of a specific season for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not media_metadata:
            return Response(
                {"detail": "Season not found."},
                status=HTTP.NOT_FOUND,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        season_episodes = list(
            BasicMedia.objects.get_season_episodes(
                user,
                media_id,
                source,
                season_number=season_number,
            ),
        )
        episode_lists_by_number = BasicMedia.objects.get_season_episode_lists_by_number(
            user,
            season_episodes,
        )
        for tracked in season_episodes:
            episode_number = getattr(tracked.item, "episode_number", None)
            if episode_number is not None:
                tracked.lists = episode_lists_by_number.get(episode_number, [])

        episodes_by_number = {
            tracked.item.episode_number: tracked
            for tracked in season_episodes
            if getattr(tracked, "item", None) is not None
            and tracked.item.episode_number is not None
        }

        lists = get_item_lists(
            user,
            media_id,
            source,
            "season",
            season_number=season_number,
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "episodes": episodes_by_number,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)

    def patch(self, request, media_type, source, media_id, season_number):
        """Update a tracked season item."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {"detail": f"{error}"},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception:  # noqa: BLE001
            return Response(
                {"detail": "Failed to update season."},
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "season",
            season_number=season_number,
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/changes_history/
class MediaSeasonChangesHistoryView(drf_views.APIView):
    """Changes history season view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedChangesHistoryResponseSerializer

    @extend_schema(
        operation_id="season_changes_history_get",
        summary="Get season changes history",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedChangesHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        value={
                            "pagination": {
                                "total": 4,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": 144,
                                    "item_id": "tv/tmdb/245703/1",
                                    "timestamp": "2026-01-18T15:21:02.888039Z",
                                    "changes": [
                                        {
                                            "field": "status",
                                            "old_value": 3,
                                            "new_value": 1,
                                        }
                                    ],
                                },
                                {
                                    "id": 143,
                                    "item_id": "tv/tmdb/245703/1",
                                    "timestamp": "2026-01-18T15:15:10.443874Z",
                                    "changes": [
                                        {
                                            "field": "notes",
                                            "old_value": "",
                                            "new_value": "Ciccio bomba",
                                        },
                                        {
                                            "field": "score",
                                            "old_value": 9.0,
                                            "new_value": 2.0,
                                        },
                                    ],
                                },
                                {
                                    "id": 142,
                                    "item_id": "tv/tmdb/245703/1",
                                    "timestamp": "2026-01-18T15:10:47.709781Z",
                                    "changes": [
                                        {
                                            "field": "score",
                                            "old_value": None,
                                            "new_value": 9.0,
                                        }
                                    ],
                                },
                                {
                                    "id": 9,
                                    "item_id": "tv/tmdb/245703/1",
                                    "timestamp": "2025-09-17T09:41:00Z",
                                    "changes": [
                                        {
                                            "field": "status",
                                            "old_value": None,
                                            "new_value": 3,
                                        },
                                        {
                                            "field": "notes",
                                            "old_value": None,
                                            "new_value": "",
                                        },
                                    ],
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Season not found example",
                        description="Season not found or not tracked example",
                        summary="Season not found example",
                        value={"detail": "Season not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve changes history timeline entries for a season."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            "season",
            source,
            season_number=season_number,
        )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, MediaTypes.SEASON.value)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = ChangesHistoryEntrySerializer(
            paginated_data["results"],
            many=True,
            context={"media_type": MediaTypes.SEASON.value},
        ).data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/episodes/
class MediaSeasonEpisodesView(drf_views.APIView):
    """Season episodes view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the episodes for a specific season of a tv serie."""
        user = request.user
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Failed to retrieve season episodes.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        episodes = []
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episodes = media_metadata["episodes"]

        paginated = paginate_data(request, episodes, limit, offset)

        # TODO: see if this can be optimized with a single query for all episodes instead of one per episode  # noqa: E501, W505
        # TODO: see if lists infos can be saved in the `episodes` object to avoid using `context` to pass additional parameters  # noqa: E501, W505
        lists_by_number = {}
        for episode in paginated["results"]:
            episode_number = episode.get("episode_number")
            if episode_number is None:
                continue
            lists_by_number[episode_number] = get_item_lists(
                user,
                media_id,
                source,
                "episode",
                season_number=season_number,
                episode_number=episode_number,
            )

        episode_numbers = [
            episode.get("episode_number")
            for episode in paginated["results"]
            if episode.get("episode_number") is not None
        ]

        tracked_by_number = {}
        if episode_numbers:
            tracked_episodes = BasicMedia.objects.get_season_episodes(
                user,
                media_id,
                source,
                season_number=season_number,
                episode_numbers=episode_numbers,
            )
            for tracked in tracked_episodes:
                item = getattr(tracked, "item", None)
                tracked_number = getattr(item, "episode_number", None)
                if (
                    tracked_number is not None
                    and tracked_number in episode_numbers
                    and tracked_number not in tracked_by_number
                ):
                    tracked_by_number[tracked_number] = tracked

        paginated["results"] = serialize_data(
            paginated["results"],
            many=True,
            context={
                "source": source,
                "tracked_episodes": tracked_by_number,
                "lists_by_number": lists_by_number,
            },
            serializer_class=EpisodeSerializer,
        )
        return Response(paginated, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/
class MediaSeasonConsumptionHistoryView(drf_views.APIView):
    """Season consumption history view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedHistoryResponseSerializer

    @extend_schema(
        operation_id="season_consumption_history_get",
        summary="Get season consumption history",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "consumption_id": 138,
                                    "created": "2026-03-19T10:31:55.759835Z",
                                    "score": None,
                                    "progress": 23,
                                    "progressed_at": "2026-03-19T10:31:00Z",
                                    "status": 3,
                                    "start_date": "2026-03-19T10:31:00Z",
                                    "end_date": "2026-03-19T10:31:00Z",
                                    "notes": "",
                                }
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Season not found example",
                        description="Season not found or not tracked example",
                        summary="Season not found example",
                        value={"detail": "Season not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the history timeline for a specific season of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # TODO: missing sorting
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = HistorySerializer(
            paginated_data["results"],
            many=True,
        ).data
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/[consumption_id]/  # noqa: E501, W505
class MediaSeasonConsumptionEntryDetailView(drf_views.APIView):
    """Season consumption history entry detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = HistorySerializer

    @extend_schema(
        operation_id="season_consumption_entry_delete",
        summary="Delete a season consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to delete",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Consumption entry deleted successfully",
                examples=[
                    OpenApiExample(
                        "Consumption entry deleted example",
                        description="Consumption entry deleted example",
                        summary="Consumption entry deleted example",
                        value=None,
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                    OpenApiExample(
                        "Consumption entry not found example",
                        description="Consumption entry not found example",
                        summary="Consumption entry not found example",
                        value={"detail": "Consumption entry not found."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        consumption_id,
    ):
        """Delete a specific consumption history entry for a specific season."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Failed to retrieve consumption entry.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        consumption.delete()

        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="season_consumption_entry_get",
        summary="Get a season consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to retrieve",
            ),
        ],
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                    OpenApiExample(
                        "Consumption entry not found example",
                        description="Consumption entry not found example",
                        summary="Consumption entry not found example",
                        value={"detail": "Consumption entry not found"},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, season_number, consumption_id):
        """Retrieve a specific consumption history entry for a specific season."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        operation_id="season_consumption_entry_patch",
        summary="Update a season consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to update",
            ),
        ],
        request=UpdateSeasonSerializer,
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        consumption_id,
    ):
        """Update a specific consumption history entry for a specific season."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(error)},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
                status=HTTP.BAD_REQUEST,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/lists/
class MediaSeasonListsView(drf_views.APIView):
    """Season lists view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedListsMinimizedResponseSerializer

    @extend_schema(
        operation_id="season_lists_get",
        summary="Get season lists",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedListsMinimizedResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieve lists example",
                        description="Retrieve lists example",
                        summary="Retrieve lists example",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [{"list_id": 2, "list_item_id": 28}],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the lists that a specific season is in."""
        user = request.user

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "season",
            season_number=season_number,
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/lists/[list_id]/
class MediaSeasonListDetailView(drf_views.APIView):
    """Season list detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ListMinimizedSerializer

    @extend_schema(
        operation_id="season_list_detail_delete",
        summary="Remove season from list",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Season removed from list successfully",
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Media not in list example",
                        description="Media not in list example",
                        summary="Media not in list example",
                        value={"detail": "Media not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def delete(self, request, media_type, source, media_id, season_number, list_id):
        """Remove a specific season from a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item_by_media(
                media_id,
                source,
                MediaTypes.SEASON.value,
                season_number=season_number,
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="season_list_detail_put",
        summary="Add season to list",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(
                ListMinimizedSerializer,
                description="Season added to list successfully",
                examples=[
                    OpenApiExample(
                        "Season added to list example",
                        description="Season added to list example",
                        summary="Season added to list example",
                        value={"list_id": 2, "list_item_id": 28},
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Media not in list example",
                        description="Media not in list example",
                        summary="Media not in list example",
                        value={"detail": "Media not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def put(self, request, media_type, source, media_id, season_number, list_id):
        """Add a specific season to a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type="season",
                season_number=season_number,
            )
        except Item.DoesNotExist:
            return Response(
                {"detail": "Media not found."},
                status=HTTP.NOT_FOUND,
            )

        if user_list.items.filter(id=item.id).exists():
            return Response(
                {"detail": "Media already in the list."},
                status=HTTP.CONFLICT,
            )

        user_list.items.add(item)

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            season_number=season_number,
        )

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/sync/
class MediaSeasonSyncView(drf_views.APIView):
    """Sync season."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, _, media_type, source, media_id, season_number):
        """Trigger sync of metadata from provider (non-manual sources only)."""
        # TODO: see if it can be simplified reducing the number of return statements
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if source == Sources.MANUAL.value:
            return Response(
                {"detail": "Manual items cannot be synced."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot sync `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        cache_key = f"{source}_season_{media_id}_{season_number}"

        ttl = cache.ttl(cache_key)
        if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
            response = Response(
                {
                    "detail": (
                        "The data was recently synced, please wait a few seconds."
                    ),
                },
                status=HTTP.TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = str(ttl)
            return response

        cache.delete(cache_key)

        try:
            metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )

            item, _ = Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type="season",
                season_number=season_number,
                defaults={
                    "title": metadata["title"],
                    "image": metadata["image"],
                },
            )

            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )
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

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)

            if episodes_to_update:
                Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )

            item.fetch_releases(delay=False)

            return Response(
                {"detail": "Metadata synced successfully."},
                status=HTTP.ACCEPTED,
            )

        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "An error occurred while syncing metadata.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/
class MediaEpisodeDetailView(drf_views.APIView):
    """Episode view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
    ):
        """Delete a tracked episode item for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "An error occurred while fetching media.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {
                    "detail": "Episode not found or not tracked.",
                },
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve details of a specific episode for the authenticated user."""
        user = request.user
        episode = None

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "An error occurred while fetching media metadata.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not media_metadata:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episode = next(
                (
                    obj
                    for obj in media_metadata["episodes"]
                    if obj["episode_number"] == int(episode_number)
                ),
                None,
            )

        if not episode:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "An error occurred while fetching user media.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        media_metadata.pop("episodes")

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
        )

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=HTTP.OK)

    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
    ):
        """Update a tracked episode item."""
        user = request.user
        episode = None

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "An error occurred while fetching user media.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {
                    "detail": "Episode not found or not tracked.",
                },
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(error)},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not media_metadata:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )

        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episode = next(
                (
                    obj
                    for obj in media_metadata["episodes"]
                    if obj["episode_number"] == int(episode_number)
                ),
                None,
            )

            if not episode:
                return Response(
                    {"detail": "Episode not found."},
                    status=HTTP.NOT_FOUND,
                )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
        )

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/changes_history/  # noqa: E501, W505
class MediaEpisodeChangesHistoryView(drf_views.APIView):
    """Changes history episode view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedChangesHistoryResponseSerializer

    @extend_schema(
        operation_id="episode_changes_history_get",
        summary="Get episode changes history",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedChangesHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        value={
                            "pagination": {
                                "total": 2,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": 1226,
                                    "item_id": "tv/tmdb/245703/1/1",
                                    "timestamp": "2026-01-18T15:21:02.851911Z",
                                    "changes": [
                                        {
                                            "field": "end_date",
                                            "old_value": None,
                                            "new_value": "2026-01-18T15:15:00Z",
                                        }
                                    ],
                                },
                                {
                                    "id": 112,
                                    "item_id": "tv/tmdb/245703/1/1",
                                    "timestamp": "2026-01-15T15:33:03.502096Z",
                                    "changes": [
                                        {
                                            "field": "end_date",
                                            "old_value": None,
                                            "new_value": "2025-09-17T09:41:00Z",
                                        }
                                    ],
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Episode not found example",
                        description="Episode not found or not tracked example",
                        summary="Episode not found example",
                        value={"detail": "Episode not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve changes history timeline entries for a specific episode."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            "episode",
            source,
            season_number=season_number,
            episode_number=episode_number,
        )

        if not user_medias:
            return Response(
                {"detail": "Episode not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, MediaTypes.EPISODE.value)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = ChangesHistoryEntrySerializer(
            paginated_data["results"],
            many=True,
            context={"media_type": MediaTypes.EPISODE.value},
        ).data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/  # noqa: E501, W505
class MediaEpisodeConsumptionHistoryView(drf_views.APIView):
    """Episode consumption history view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedHistoryResponseSerializer

    @extend_schema(
        operation_id="episode_consumption_history_get",
        summary="Get episode consumption history",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "consumption_id": 138,
                                    "created": "2026-03-19T10:31:55.759835Z",
                                    "score": None,
                                    "progress": 23,
                                    "progressed_at": "2026-03-19T10:31:00Z",
                                    "status": 3,
                                    "start_date": "2026-03-19T10:31:00Z",
                                    "end_date": "2026-03-19T10:31:00Z",
                                    "notes": "",
                                }
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Season not found example",
                        description="Season not found or not tracked example",
                        summary="Season not found example",
                        value={"detail": "Season not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the history timeline for a specific episode of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # TODO: missing sorting
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = serialize_data(
            paginated_data["results"],
            serializer_class=HistorySerializer,
            many=True,
        )
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/[consumption_id]/  # noqa: E501, W505
class MediaEpisodeConsumptionEntryDetailView(drf_views.APIView):
    """Episode consumption history entry detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = HistorySerializer

    @extend_schema(
        operation_id="episode_consumption_entry_delete",
        summary="Delete an episode consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to delete",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Consumption entry deleted successfully",
                examples=[
                    OpenApiExample(
                        "Consumption entry deleted example",
                        description="Consumption entry deleted example",
                        summary="Consumption entry deleted example",
                        value=None,
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                    OpenApiExample(
                        "Consumption entry not found example",
                        description="Consumption entry not found example",
                        summary="Consumption entry not found example",
                        value={"detail": "Consumption entry not found."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Delete a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{e!s}"},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        consumption.delete()

        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="episode_consumption_entry_get",
        summary="Get an episode consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to retrieve",
            ),
        ],
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                    OpenApiExample(
                        "Consumption entry not found example",
                        description="Consumption entry not found example",
                        summary="Consumption entry not found example",
                        value={"detail": "Consumption entry not found"},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Retrieve a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{e!s}"},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        operation_id="episode_consumption_entry_patch",
        summary="Update an episode consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to update",
            ),
        ],
        request=UpdateEpisodeSerializer,
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    ),
                    OpenApiExample(
                        "Invalid source example",
                        description="Invalid source example",
                        summary="Invalid source example",
                        value={
                            "detail": "Cannot query `invalid_source` for `tv` media type"
                        },
                    ),
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Update a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{e!s}"},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {"detail": f"{error}"},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
                status=HTTP.BAD_REQUEST,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/
class MediaEpisodeListsView(drf_views.APIView):
    """Episode lists view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedListsMinimizedResponseSerializer

    @extend_schema(
        operation_id="episode_lists_get",
        summary="Get episode lists",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedListsMinimizedResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieve lists example",
                        description="Retrieve lists example",
                        summary="Retrieve lists example",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [{"list_id": 2, "list_item_id": 28}],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "Media not found example",
                        description="Media not found or not tracked example",
                        summary="Media not found example",
                        value={"detail": "Media not found or not tracked."},
                    )
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the lists that a specific episode is in."""
        user = request.user

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": " Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/[list_id]/  # noqa: E501, W505
class MediaEpisodeListDetailView(drf_views.APIView):
    """Episode list detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ListMinimizedSerializer

    @extend_schema(
        operation_id="episode_list_detail_delete",
        summary="Remove episode from list",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Episode removed from the list successfully.",
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Media not in list example",
                        description="Media not in list example",
                        summary="Media not in list example",
                        value={"detail": "Media not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        list_id,
    ):
        """Remove a specific episode from a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": " Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item_by_media(
                media_id,
                source,
                MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="episode_list_detail_put",
        summary="Add episode to list",
        parameters=[
            MediaTypeParam,
            SourceParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(
                ListMinimizedSerializer,
                description="Season added to list successfully",
                examples=[
                    OpenApiExample(
                        "Season added to list example",
                        description="Season added to list example",
                        summary="Season added to list example",
                        value={"list_id": 2, "list_item_id": 28},
                    )
                ],
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid media type example",
                        description="Invalid media type example",
                        summary="Invalid media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            404: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Not found",
                examples=[
                    OpenApiExample(
                        "List not found example",
                        description="List not found example",
                        summary="List not found example",
                        value={"detail": "List not found."},
                    ),
                    OpenApiExample(
                        "Media not in list example",
                        description="Media not in list example",
                        summary="Media not in list example",
                        value={"detail": "Media not found in the list."},
                    ),
                ],
            ),
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal Server Error",
                examples=[
                    OpenApiExample(
                        "Internal server error example",
                        description="Internal server error example",
                        summary="Internal server error example",
                        value={"detail": "Internal Server Error."},
                    )
                ],
            ),
        },
    )
    def put(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        list_id,
    ):
        """Add a specific episode to a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": " Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type="episode",
                season_number=season_number,
                episode_number=episode_number,
            )
        except Item.DoesNotExist:
            return Response(
                {"detail": "Media not found."},
                status=HTTP.NOT_FOUND,
            )

        if user_list.items.filter(id=item.id).exists():
            return Response(
                {"detail": "Media already in the list."},
                status=HTTP.CONFLICT,
            )

        user_list.items.add(item)

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            season_number=season_number,
            episode_number=episode_number,
        )

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/sync/
class MediaEpisodeSyncView(drf_views.APIView):
    """Sync episode view."""

    permission_classes = [permissions.IsAuthenticated]

    def post(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,  # noqa: ARG002
    ):
        """Redirect episode sync to season sync."""
        season_sync = MediaSeasonSyncView()
        return season_sync.post(
            request,
            media_type=media_type,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )


# /api/v1/search/[media_type]/
class SearchProviderView(drf_views.APIView):
    """Search view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MediaSerializer

    @extend_schema(
        operation_id="search_get",
        summary="Search for media",
        parameters=[
            MediaTypeParam,
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            ),
            OpenApiParameter(
                name="source",
                type=OpenApiTypes.STR,
                enum=SOURCES_VALID_LIST,
                location=OpenApiParameter.QUERY,
                description="Source of the media",
            ),
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                SearchResponseSerializer,
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Unsupported media type example",
                        description="Unsupported media type example",
                        summary="Unsupported media type example",
                        value={"detail": "Unsupported media type."},
                    )
                ],
            ),
            403: forbidden_response,
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching results",
                        description="Error while fetching results example",
                        summary="Error while fetching results example",
                        value={
                            "detail": "Internal server error.",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request, media_type):
        """Search for media using the specified provider."""
        search = request.GET.get("search", "")
        source = request.GET.get("source", None)
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )
        if media_type in ("season", "episode"):
            # Since data of seasons and episodes (title, author, description,
            # etc.) is not saved in the db but retrieved every time, it's not
            # possible to search for them
            return Response(
                {
                    "detail": f"Search for {media_type} is not supported.",
                },
                status=HTTP.BAD_REQUEST,
            )

        results_accum = []
        page = 1
        last_response = None

        try:
            while True:
                last_response = services.search(
                    media_type,
                    search,
                    page,
                    source,
                    limit=limit,
                    offset=offset,
                    user=request.user,
                )
                if (
                    not isinstance(last_response, dict)
                    or "results" not in last_response
                ):
                    break
                page_results = last_response.get("results", []) or []
                results_accum.extend(page_results)
                if len(results_accum) >= offset + limit:
                    break
                total_pages = last_response.get("total_pages")
                if total_pages and page >= total_pages:
                    break
                if not page_results:
                    break

                page += 1

        except Exception:  # noqa: BLE001
            return Response(
                {"detail": HTTP.INTERNAL_SERVER_ERROR.phrase},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        total = (
            last_response.get("total_results")
            if isinstance(last_response, dict)
            else len(results_accum)
        )

        resolved_total = total or len(results_accum)
        paginated_data = paginate_data(
            request,
            results_accum,
            limit,
            offset,
            total=resolved_total,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/statistics/
class StatisticsView(drf_views.APIView):
    """Statistics view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StatisticsResponseSerializer

    @extend_schema(
        operation_id="statistics_get",
        summary="Get user statistics",
        parameters=[
            OpenApiParameter(
                name="start_date",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description="Filter media started after this date (YYYY-MM-DD)",
            ),
            OpenApiParameter(
                name="end_date",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description="Filter media started before this date (YYYY-MM-DD)",
            ),
        ],
        responses={
            200: OpenApiResponse(
                StatisticsResponseSerializer,
                description="Successful response",
            ),
            400: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Bad request",
                examples=[
                    OpenApiExample(
                        "Invalid date format example",
                        description="Invalid date format example",
                        summary="Invalid date format example",
                        value={"detail": "Invalid date format."},
                    )
                ],
            ),
            403: forbidden_response,
            500: OpenApiResponse(
                ApiErrorResponseSerializer,
                description="Internal server error",
                examples=[
                    OpenApiExample(
                        "Error while fetching statistics",
                        description="Error while fetching statistics example",
                        summary="Error while fetching statistics example",
                        value={
                            "detail": "Internal server error.",
                        },
                    )
                ],
            ),
        },
    )
    def get(self, request):
        """Retrieve statistics for the authenticated user."""
        # TODO: Possibly don't use WebUI needed statistics but compute them for API
        timeformat = "%Y-%m-%d"
        today = localdate()
        one_year_ago = today.replace(year=today.year - 1).strftime(timeformat)
        today = today.strftime(timeformat)

        user = request.user
        start_date = request.GET.get("start_date", one_year_ago)
        end_date = request.GET.get("end_date", today)
        if not start_date:
            start_date = one_year_ago
        if not end_date:
            end_date = today

        if start_date == "all" and end_date == "all":
            start_date = None
            end_date = None
        else:
            try:
                start_date = try_parse_date(start_date)
                end_date = try_parse_date(end_date)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Invalid date format."},
                    status=HTTP.BAD_REQUEST,
                )

            if start_date and end_date:
                start_date = make_aware(
                    datetime.combine(start_date, datetime.min.time()),
                )
                end_date = make_aware(
                    datetime.combine(end_date, datetime.max.time()),
                )
        user_media, media_count = get_user_media(
            user,
            start_date,
            end_date,
        )
        media_type_distribution = get_media_type_distribution(
            media_count,
        )
        score_distribution, top_rated = get_score_distribution(user_media)
        status_distribution = get_status_distribution(user_media)
        status_pie_chart_data = get_status_pie_chart_data(
            status_distribution,
        )
        timeline = get_timeline(user_media)
        activity_data = get_activity_data(request.user, start_date, end_date)

        statistics = {
            "start_date": start_date,
            "end_date": end_date,
            "media_count": media_count,
            "activity_data": activity_data,
            "media_type_distribution": media_type_distribution,
            "score_distribution": score_distribution,
            "top_rated": serialize_data(top_rated, many=True),
            "status_distribution": status_distribution,
            "status_pie_chart_data": status_pie_chart_data,
            "timeline": {
                month: serialize_data(
                    items,
                    many=True,
                    context={"request": request},
                    serializer_class=TimelineItemSerializer,
                )
                for month, items in (timeline or {}).items()
            },
        }

        return Response(statistics, status=HTTP.OK)
