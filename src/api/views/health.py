from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from app import config
from app.models import MediaTypes, Sources, Status


class HealthView(APIView):
    """Lightweight API health endpoint."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "version": settings.VERSION,
                "time": timezone.now(),
            },
        )


class MetaView(APIView):
    """API metadata for clients."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "version": "v1",
                "media_types": list(MediaTypes.values),
                "sources": {
                    media_type: [source.value for source in config.get_sources(media_type)]
                    for media_type in config.MEDIA_TYPE_CONFIG
                },
                "status_choices": list(Status.values),
                "source_choices": list(Sources.values),
            },
        )
