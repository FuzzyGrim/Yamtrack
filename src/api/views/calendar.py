from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.authentication import APIKeyAuthentication, BearerAuthentication
from api.helpers import (
    error_response,
    paginate_data,
    parse_limit_offset,
    resolve_calendar_date_range,
)
from api.schemas import (
    BadRequestResponse,
    InternalServerErrorResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    UnauthorizedResponse,
)
from api.serializers import (
    ApiMessageResponseSerializer,
    EventSerializer,
    PaginatedEventsSerializer,
)
from events import tasks
from events.models import Event


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
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            500: InternalServerErrorResponse,
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
            return error_response(
                "Error occurred while fetching events.", HTTP.INTERNAL_SERVER_ERROR, e
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
            401: UnauthorizedResponse,
        },
    )
    def post(self, request):
        """Trigger calendar events update."""
        tasks.reload_calendar.delay(request.user)
        return Response(
            {"detail": "Task queued"},
            status=HTTP.ACCEPTED,
        )
