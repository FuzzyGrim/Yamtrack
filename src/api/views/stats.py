from datetime import datetime

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import can_view_user_profile
from api.serializers.common import media_summary_from_item
from app import statistics as stats


def _date_range(request):
    start = request.query_params.get("start_date")
    end = request.query_params.get("end_date")
    if start == "all" and end == "all":
        return None, None
    today = timezone.localdate()
    start_date = parse_date(start) if start else today.replace(year=today.year - 1)
    end_date = parse_date(end) if end else today
    return (
        timezone.make_aware(datetime.combine(start_date, datetime.min.time())),
        timezone.make_aware(datetime.combine(end_date, datetime.max.time())),
    )


def stats_payload(user, request):
    """Build a compact stats response."""
    start_date, end_date = _date_range(request)
    user_media, media_count = stats.get_user_media(user, start_date, end_date)
    score_distribution, top_rated = stats.get_score_distribution(user_media)
    status_distribution = stats.get_status_distribution(user_media)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "media_count": media_count,
        "media_type_distribution": stats.get_media_type_distribution(media_count),
        "score_distribution": score_distribution,
        "status_distribution": status_distribution,
        "top_rated": [
            {
                "media": media_summary_from_item(media.item, request=request, user=user),
                "rating": str(media.score) if media.score is not None else None,
            }
            for media in top_rated
        ],
    }


class MyStatsSummaryView(APIView):
    """Current user's stats summary."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(stats_payload(request.user, request))


class UserStatsSummaryView(APIView):
    """Public user's stats summary."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(stats_payload(user, request))
