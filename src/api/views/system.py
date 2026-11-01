import asyncio
from http import HTTPStatus as HTTP  # noqa: N814

from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils.timezone import now
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from health_check.views import HealthCheckView
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.examples import (
    HEALTH_EXAMPLE,
    INFO_EXAMPLE,
)
from api.serializers import (
    HealthResponseSerializer,
    InfoSerializer,
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
                examples=[HEALTH_EXAMPLE],
            ),
            500: OpenApiResponse(
                HealthResponseSerializer,
                description="One or more health checks failed",
            ),
        },
    )
    def get(self, request):  # noqa: ARG002
        """Check API health status."""
        results = async_to_sync(self._collect_health_results)()
        health_data = {
            "timestamp": now(),
            "checks": {repr(result.check): result for result in results},
        }
        response_data = HealthResponseSerializer(health_data).data
        has_errors = any(result.error for result in results)
        status_code = HTTP.INTERNAL_SERVER_ERROR if has_errors else HTTP.OK
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
                examples=[INFO_EXAMPLE],
            )
        },
    )
    def get(self, request):  # noqa: ARG002
        """Get application information."""
        response_data = InfoSerializer(settings).data
        return Response(response_data, status=HTTP.OK)
