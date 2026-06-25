from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.diary import DiaryEntryWriteSerializer
from api.services import diary as diary_service
from api.services.social import set_like
from app.models import DiaryEntry
from app.services import delete_diary_entry
from social.models import ContentLike


class DiaryListView(APIView):
    """List or create diary entries."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entries = (
            DiaryEntry.objects.filter(user=request.user)
            .select_related("item", "user")
            .prefetch_related("tags")
            .order_by("-consumed_at", "-id")
        )
        media_type = request.query_params.get("media_type")
        year = request.query_params.get("year")
        item_id = request.query_params.get("item_id")
        tag = request.query_params.get("tag", "").strip().lower()
        has_review = request.query_params.get("has_review") == "true"
        liked = request.query_params.get("liked") == "true"
        if media_type:
            entries = entries.filter(item__media_type=media_type)
        if year:
            entries = entries.filter(consumed_at__year=year)
        if item_id:
            entries = entries.filter(item_id=item_id)
        if tag:
            entries = entries.filter(tags__name=tag)
        if has_review:
            entries = entries.filter(Q(review__gt="") | Q(review_title__gt=""))
        if liked:
            entries = entries.filter(liked=True)
        return Response(
            {
                "count": entries.count(),
                "next": None,
                "previous": None,
                "results": [
                    diary_service.diary_payload(entry, request=request, viewer=request.user)
                    for entry in entries
                ],
            },
        )

    def post(self, request):
        serializer = DiaryEntryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = diary_service.create_entry(request.user, serializer.validated_data)
        entry = DiaryEntry.objects.select_related("item", "user").prefetch_related("tags").get(id=entry.id)
        return Response(
            diary_service.diary_payload(entry, request=request, viewer=request.user),
            status=status.HTTP_201_CREATED,
        )


class DiaryDetailView(APIView):
    """Read, update, or delete a diary entry."""

    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        entry = get_object_or_404(
            DiaryEntry.objects.select_related("item", "user").prefetch_related("tags"),
            id=entry_id,
        )
        if entry.user != request.user and entry.visibility == "private":
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(diary_service.diary_payload(entry, request=request, viewer=request.user))

    def patch(self, request, entry_id):
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        serializer = DiaryEntryWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        entry = diary_service.update_entry(entry, serializer.validated_data)
        entry = DiaryEntry.objects.select_related("item", "user").prefetch_related("tags").get(id=entry.id)
        return Response(diary_service.diary_payload(entry, request=request, viewer=request.user))

    def delete(self, request, entry_id):
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        delete_diary_entry(request.user, entry)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DiaryTagsView(APIView):
    """Tag autocomplete for diary entries."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user if request.query_params.get("mine") == "true" else None
        limit = None if request.query_params.get("all") == "true" else 10
        return Response({"results": diary_service.tag_results(request.query_params.get("q", ""), user=user, limit=limit)})


class DiaryLikeView(APIView):
    """Like/unlike a diary entry."""

    permission_classes = [IsAuthenticated]

    def post(self, request, entry_id):
        return Response(
            set_like(
                request.user,
                target_type=ContentLike.DIARY_ENTRY,
                target_id=entry_id,
                liked=True,
            ),
        )

    def delete(self, request, entry_id):
        return Response(
            set_like(
                request.user,
                target_type=ContentLike.DIARY_ENTRY,
                target_id=entry_id,
                liked=False,
            ),
        )
