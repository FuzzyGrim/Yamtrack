from django.http import StreamingHttpResponse
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.imports import ImportSerializer
from api.services.imports import TASKS_BY_SOURCE, queue_import, task_status
from integrations import exports


class ImportsView(APIView):
    """Import history and schedules."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(request.user.get_import_tasks())


class ImportSourceView(APIView):
    """Queue a once-only import."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source):
        if source not in TASKS_BY_SOURCE:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            queue_import(source, request.user, serializer.validated_data, request.FILES),
            status=status.HTTP_202_ACCEPTED,
        )


class ImportTaskView(APIView):
    """Poll import task status."""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        result = task_status(task_id, request.user)
        if result is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(result)


class ImportScheduleView(APIView):
    """Delete an import schedule owned by current user."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, schedule_id):
        PeriodicTask.objects.filter(
            id=schedule_id,
            kwargs__contains=f'"user_id": {request.user.id}',
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExportCSVView(APIView):
    """Stream CSV export."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.localtime()
        return StreamingHttpResponse(
            streaming_content=exports.generate_rows(request.user),
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="spine_{now}.csv"'},
        )
