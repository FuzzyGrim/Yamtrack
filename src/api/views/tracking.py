from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.serializers.common import media_summary_from_item
from api.serializers.tracking import (
    BookProgressSerializer,
    ConsumeSerializer,
    EpisodeWatchSerializer,
    TrackingWriteSerializer,
)
from api.services import tracking as tracking_service
from app.models import BasicMedia, MediaTypes, Status


class TrackingListView(APIView):
    """List tracked media for the current user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        media_type = request.query_params.get("media_type")
        if not media_type:
            return Response({"media_type": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        status_filter = request.query_params.get("status", "All")
        ordering = request.query_params.get("ordering") or request.query_params.get("sort") or "title"
        search = request.query_params.get("q")
        queryset = BasicMedia.objects.get_media_list(
            request.user,
            media_type,
            status_filter,
            ordering,
            search=search,
        )
        paginator = StandardResultsSetPagination()
        page = list(paginator.paginate_queryset(queryset, request, view=self))
        BasicMedia.objects.annotate_max_progress(page, media_type)
        return paginator.get_paginated_response(
            [
                {
                    "media": media_summary_from_item(media.item, request=request),
                    "tracking": tracking_service.serialize_tracking(media),
                }
                for media in page
            ],
        )


class TrackingDetailView(APIView):
    """Retrieve, upsert, patch, or delete tracking state."""

    permission_classes = [IsAuthenticated]

    def get(self, request, source, media_type, media_id):
        media = tracking_service.get_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=request.query_params.get("season_number"),
            episode_number=request.query_params.get("episode_number"),
        )
        if media is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(tracking_service.serialize_tracking(media))

    def put(self, request, source, media_type, media_id):
        serializer = TrackingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        media = tracking_service.create_or_update_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            data=serializer.validated_data,
            partial=False,
        )
        return Response(tracking_service.serialize_tracking(media))

    def patch(self, request, source, media_type, media_id):
        serializer = TrackingWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        media = tracking_service.create_or_update_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            data=serializer.validated_data,
        )
        return Response(tracking_service.serialize_tracking(media))

    def delete(self, request, source, media_type, media_id):
        tracking_service.delete_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=request.query_params.get("season_number"),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class TrackingActionView(APIView):
    """Generic tracking status actions."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_type, media_id, action):
        if action == "consume":
            serializer = ConsumeSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            media = tracking_service.consume_media(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                consumed_at=serializer.validated_data.get("consumed_at"),
            )
        elif action == "pause":
            media = tracking_service.set_status(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                status=Status.PAUSED.value,
            )
        elif action == "resume":
            media = tracking_service.set_status(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                status=Status.IN_PROGRESS.value,
            )
        elif action == "drop":
            media = tracking_service.set_status(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                status=Status.DROPPED.value,
            )
        else:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(tracking_service.serialize_tracking(media))


class TVStartView(APIView):
    """Start tracking a TV show."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        media = tracking_service.set_status(
            request.user,
            source=source,
            media_type=MediaTypes.TV.value,
            media_id=media_id,
            status=Status.IN_PROGRESS.value,
        )
        return Response(tracking_service.serialize_tracking(media))


class SeasonStartView(APIView):
    """Start tracking a season."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number):
        media = tracking_service.create_or_update_tracking(
            request.user,
            source=source,
            media_type=MediaTypes.SEASON.value,
            media_id=media_id,
            data={"season_number": season_number, "status": Status.IN_PROGRESS.value},
        )
        return Response(tracking_service.serialize_tracking(media))


class SeasonWatchView(APIView):
    """Watch or unwatch a whole season."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number):
        media = tracking_service.watch_season(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )
        return Response(tracking_service.serialize_tracking(media))

    def delete(self, request, source, media_id, season_number):
        media = tracking_service.unwatch_season(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )
        return Response(tracking_service.serialize_tracking(media) if media else {}, status=status.HTTP_200_OK)


class EpisodeWatchView(APIView):
    """Watch or unwatch one episode."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number, episode_number):
        serializer = EpisodeWatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        season = tracking_service.watch_episode(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
            watched_at=serializer.validated_data.get("watched_at"),
        )
        return Response(tracking_service.serialize_tracking(season))

    def delete(self, request, source, media_id, season_number, episode_number):
        season = tracking_service.unwatch_episode(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        )
        return Response(tracking_service.serialize_tracking(season) if season else {}, status=status.HTTP_200_OK)


class BookProgressView(APIView):
    """Log book progress."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        serializer = BookProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        book = tracking_service.log_book_progress(
            request.user,
            source=source,
            media_id=media_id,
            progress_type=serializer.validated_data["progress_type"],
            value=serializer.validated_data["value"],
            notes=serializer.validated_data.get("notes", ""),
        )
        return Response(tracking_service.serialize_tracking(book))


class BookCompleteView(APIView):
    """Mark a book complete."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        media = tracking_service.consume_media(
            request.user,
            source=source,
            media_type=MediaTypes.BOOK.value,
            media_id=media_id,
            consumed_at=request.data.get("completed_at"),
        )
        return Response(tracking_service.serialize_tracking(media))
