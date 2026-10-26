from http import HTTPStatus as HTTP  # noqa: N814

from django.utils.timezone import datetime, localdate, make_aware
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.helpers import (
    get_timeline,
    try_parse_date,
)
from api.schemas import (
    BadRequestResponse,
)
from api.serializers import (
    StatisticsResponseSerializer,
)
from app.statistics import (
    get_activity_data,
    get_media_type_distribution,
    get_score_distribution,
    get_status_distribution,
    get_status_pie_chart_data,
    get_user_media,
)


# /api/v1/statistics/
class StatisticsView(drf_views.APIView):
    """Statistics view."""

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
            400: BadRequestResponse,
        },
    )
    def get(self, request):
        """Retrieve statistics for the authenticated user."""
        # TODO: Possibly don't use WebUI needed statistics but compute them for API
        timeformat = "%Y-%m-%d"
        today = localdate()
        try:
            one_year_ago = today.replace(year=today.year - 1)
        except ValueError:
            one_year_ago = today.replace(year=today.year - 1, day=28)
        one_year_ago = one_year_ago.strftime(timeformat)
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

        start_datetime = None
        end_datetime = None
        if start_date and end_date:
            start_datetime = make_aware(
                datetime.combine(start_date, datetime.min.time()),
            )
            end_datetime = make_aware(
                datetime.combine(end_date, datetime.max.time()),
            )
        user_media, media_count = get_user_media(
            user,
            start_datetime,
            end_datetime,
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
        activity_data = get_activity_data(request.user, start_datetime, end_datetime)

        statistics = StatisticsResponseSerializer(
            {
                "start_date": start_date,
                "end_date": end_date,
                "media_count": media_count,
                "activity_data": activity_data,
                "media_type_distribution": media_type_distribution,
                "score_distribution": score_distribution,
                "top_rated": top_rated,
                "status_distribution": status_distribution,
                "status_pie_chart_data": status_pie_chart_data,
                "timeline": timeline or {},
            },
        ).data

        return Response(statistics, status=HTTP.OK)
