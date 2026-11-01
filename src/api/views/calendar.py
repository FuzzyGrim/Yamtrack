from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.examples import (
    EVENTS_EXAMPLE,
    TASK_QUEUED_EXAMPLE,
)
from api.helpers import (
    error_response,
    paginate_data,
    parse_limit_offset,
    resolve_calendar_date_range,
)
from api.schemas import (
    BadRequestResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
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
                examples=[EVENTS_EXAMPLE],
            ),
            400: BadRequestResponse,
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

    serializer_class = ApiMessageResponseSerializer

    @extend_schema(
        operation_id="calendar_update_post",
        summary="Trigger calendar update",
        request=None,
        responses={
            202: OpenApiResponse(
                ApiMessageResponseSerializer,
                description="Task queued successfully",
                examples=[TASK_QUEUED_EXAMPLE],
            ),
        },
    )
    def post(self, request):
        """Trigger calendar events update."""
        tasks.reload_calendar.delay(request.user)
        return Response(
            {"detail": "Task queued"},
            status=HTTP.ACCEPTED,
        )
