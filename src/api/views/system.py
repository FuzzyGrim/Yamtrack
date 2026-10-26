import asyncio
from http import HTTPStatus as HTTP  # noqa: N814

from asgiref.sync import async_to_sync
from django.conf import settings
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
)
from health_check.views import HealthCheckView
from rest_framework import views as drf_views
from rest_framework.response import Response

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
                description="One or more health checks failed",
            ),
        },
    )
    def get(self, request):  # noqa: ARG002
        """Check API health status."""
        results = async_to_sync(self._collect_health_results)()
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
        response_data = InfoSerializer(settings).data
        return Response(response_data, status=HTTP.OK)
