from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.services import diary as diary_service
from api.services import media as media_service
from api.throttling import SearchRateThrottle
from app import config
from app.forms import ManualItemForm


class MediaSearchView(APIView):
    """Provider-backed media search."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SearchRateThrottle]

    def get(self, request):
        media_type = request.query_params.get("media_type")
        query = request.query_params.get("q", "").strip()
        if not media_type or not query:
            return Response(
                {"media_type": ["This field is required."], "q": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        page = int(request.query_params.get("page", 1))
        results = media_service.search_media(
            media_type=media_type,
            query=query,
            page=page,
            source=request.query_params.get("source"),
            request=request,
            user=request.user,
        )
        return Response({"count": len(results), "next": None, "previous": None, "results": results})


class MediaSourcesView(APIView):
    """Source map by media type."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                media_type: [source.value for source in config.get_sources(media_type)]
                for media_type in config.MEDIA_TYPE_CONFIG
            },
        )


class MediaDiscoverView(APIView):
    """Reserved discovery endpoint."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {"detail": "Media discovery is planned after provider support is normalized."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class ManualMediaView(APIView):
    """Create a manual media item."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {
            "media_type": request.data.get("media_type"),
            "title": request.data.get("title"),
            "image": request.data.get("image_url") or settings.IMG_NONE,
            "season_number": request.data.get("season_number"),
            "episode_number": request.data.get("episode_number"),
        }
        form = ManualItemForm(data, user=request.user)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        item = form.save()
        from api.serializers.common import media_summary_from_item

        return Response(media_summary_from_item(item, request=request, user=request.user), status=status.HTTP_201_CREATED)


class MediaDetailView(APIView):
    """Provider-backed media detail."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, media_type, media_id):
        return Response(
            media_service.media_detail(
                source=source,
                media_type=media_type,
                media_id=media_id,
                season_number=request.query_params.get("season_number"),
                episode_number=request.query_params.get("episode_number"),
                request=request,
                user=request.user if request.user.is_authenticated else None,
            ),
        )


class MediaReviewsView(APIView):
    """Public diary reviews for a media identity."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_type, media_id):
        from app.models import DiaryEntry, Item

        item = Item.objects.filter(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=request.query_params.get("season_number"),
            episode_number=request.query_params.get("episode_number"),
        ).first()
        if item is None:
            return Response({"count": 0, "next": None, "previous": None, "results": []})

        entries = (
            DiaryEntry.objects.filter(item=item)
            .exclude(visibility="private")
            .exclude(review="")
            .select_related("item", "user")
            .prefetch_related("tags")
        )
        if request.query_params.get("sort", "popular") == "recent":
            entries = entries.order_by("-created_at")
        else:
            entries = entries.order_by("-liked", "-created_at")

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(entries, request, view=self)
        viewer = request.user if request.user.is_authenticated else None
        return paginator.get_paginated_response(
            [diary_service.diary_payload(entry, request=request, viewer=viewer) for entry in page],
        )


class MediaPostersView(APIView):
    """Selectable poster images for TMDB movie/TV media."""

    permission_classes = [IsAuthenticated]

    def get(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.poster_options(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    request=request,
                    user=request.user,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MediaPosterPreferenceView(APIView):
    """Save the viewer's selected poster."""

    permission_classes = [IsAuthenticated]

    def put(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.save_poster_preference(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    poster_url=request.data.get("poster_url"),
                    user=request.user,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class TVSeasonsView(APIView):
    """TV season summaries."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_id):
        return Response(
            media_service.tv_seasons(
                source=source,
                media_id=media_id,
                request=request,
                user=request.user if request.user.is_authenticated else None,
            ),
        )


class SeasonDetailView(APIView):
    """TV season detail."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_id, season_number):
        return Response(
            media_service.season_detail(
                source=source,
                media_id=media_id,
                season_number=season_number,
                request=request,
                user=request.user if request.user.is_authenticated else None,
            ),
        )


class SeasonEpisodesView(APIView):
    """TV season episodes."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_id, season_number):
        return Response(
            media_service.season_episodes(
                source=source,
                media_id=media_id,
                season_number=season_number,
                request=request,
                user=request.user if request.user.is_authenticated else None,
            ),
        )


class CommunityStatsView(APIView):
    """Community aggregate placeholder."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_type, media_id):
        return Response(
            media_service.community_stats(
                source=source,
                media_type=media_type,
                media_id=media_id,
                season_number=request.query_params.get("season_number"),
            ),
        )
