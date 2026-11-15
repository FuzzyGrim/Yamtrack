"""JSON API views for dashboards and external integrations."""

import logging

from django.contrib.auth.decorators import login_not_required
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET

from app import statistics as stats
from app.models import Status
from users.models import User

logger = logging.getLogger(__name__)


def _serialize_datetime(value):
    """Return an ISO-8601 string for a datetime, or None."""
    if value is None:
        return None
    return value.isoformat()


@login_not_required
@require_GET
def api_statistics(request, token):
    """Return consumption statistics for the user identified by token.

    Optional query params match the HTML statistics page:
    ``start-date`` and ``end-date`` as ``YYYY-MM-DD``, or ``all`` for both.
    """
    try:
        user = User.objects.get(token=token)
    except User.DoesNotExist as error:
        logger.warning("Statistics API: invalid token")
        raise Http404 from error

    start_date, end_date = stats.parse_activity_date_range(request)
    user_media, media_count = stats.get_user_media(user, start_date, end_date)
    status_distribution = stats.get_status_distribution(user_media)
    consumption_stats = stats.get_consumption_stats(user_media, media_count)

    consumption = [
        {
            "media_type": entry["media_type"],
            "value": entry["value"],
            "descriptor": str(entry["descriptor"]),
        }
        for entry in consumption_stats
    ]

    return JsonResponse(
        {
            "start_date": _serialize_datetime(start_date),
            "end_date": _serialize_datetime(end_date),
            "media_count": media_count,
            "in_progress": stats.get_status_total(
                status_distribution,
                Status.IN_PROGRESS.value,
            ),
            "consumption": consumption,
        },
    )
