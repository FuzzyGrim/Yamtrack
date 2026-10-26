from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.authentication import APIKeyAuthentication, BearerAuthentication
from api.changes_history_processor import get_changes_history_entries
from api.helpers import (
    apply_validated_fields,
    check_source_type,
    check_valid_type,
    error_response,
    get_item_lists,
    paginate_data,
    parse_limit_offset,
    require_update_fields,
)
from api.schemas import (
    BadRequestResponse,
    EpisodeNumberParam,
    ForbiddenResponse,
    InternalServerErrorResponse,
    MediaIdParam,
    MediaTypeParam,
    NotFoundResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    SeasonNumberParam,
    SourceCompleteParam,
    TooManyRequestsResponse,
    UnauthorizedResponse,
)
from api.serializers import (
    ApiMessageResponseSerializer,
    ChangesHistoryEntrySerializer,
    CompleteEpisodeSerializer,
    HistorySerializer,
    ListMinimizedSerializer,
    PaginatedChangesHistoryResponseSerializer,
    PaginatedHistoryResponseSerializer,
    PaginatedListsMinimizedResponseSerializer,
    UpdateEpisodeSerializer,
)
from app.models import BasicMedia, Item, MediaTypes
from app.providers import services
from lists.models import CustomList, CustomListItem

from .seasons import MediaSeasonSyncView


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/
class MediaEpisodeDetailView(drf_views.APIView):
    """Episode view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CompleteEpisodeSerializer

    @extend_schema(
        operation_id="episode_detail_delete",
        summary="Delete episode",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
        ],
        responses={
            204: OpenApiResponse(description="Media item deleted successfully"),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
    ):
        """Delete a tracked episode item for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while fetching media.", HTTP.INTERNAL_SERVER_ERROR, e
            )

        if not user_medias:
            return Response(
                {
                    "detail": "Episode not found or not tracked.",
                },
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    @extend_schema(
        operation_id="episode_detail_get",
        summary="Get episode details",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
        ],
        responses={
            200: OpenApiResponse(
                CompleteEpisodeSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved media details example",
                        description="Retrieved media details example",
                        summary="Retrieved media details example",
                        value={
                            "id": 3612,
                            "media_id": "12345",
                            "source": "tmdb",
                            "source_url": "https://www.themoviedb.org/tv/12345/season/1/episode/1",
                            "media_type": "episode",
                            "title": "Destination Unknown",
                            "max_progress": 1,
                            "image": "https://image.tmdb.org/t/p/original/9xX4Ycm7vN0Qb4JtCjGfC2iC2nH.jpg",
                            "synopsis": "Van der Valk investigates a disappearance...",
                            "genres": ["Drama"],
                            "score": 7.0,
                            "score_count": 3,
                            "details": {
                                "air_date": "1972-09-13",
                                "episode_number": 1,
                                "season_number": 1,
                                "runtime": 50,
                                "episode_type": "standard",
                                "crew": [],
                                "guest_stars": [],
                            },
                            "related": {},
                            "item_id": "tv/tmdb/12345/1/1",
                            "parent_id": "tv/tmdb/12345/1",
                            "tracked": True,
                            "consumptions_number": 1,
                            "consumptions": [
                                {
                                    "consumption_id": 322,
                                    "created": None,
                                    "score": None,
                                    "progress": 1,
                                    "progressed_at": None,
                                    "status": 3,
                                    "start_date": None,
                                    "end_date": "2026-05-11T17:20:38Z",
                                    "notes": "",
                                }
                            ],
                            "lists": [],
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve details of a specific episode for the authenticated user."""
        user = request.user
        episode = None

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while fetching media metadata.",
                HTTP.INTERNAL_SERVER_ERROR,
                e,
            )

        if not media_metadata:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episode = next(
                (
                    obj
                    for obj in media_metadata["episodes"]
                    if obj["episode_number"] == int(episode_number)
                ),
                None,
            )

        if not episode:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while fetching user media.",
                HTTP.INTERNAL_SERVER_ERROR,
                e,
            )

        media_metadata.pop("episodes")

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
        )

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = CompleteEpisodeSerializer(
            data,
        ).data
        return Response(serialized, status=HTTP.OK)

    @extend_schema(
        operation_id="episode_detail_patch",
        summary="Update episode",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
        ],
        request=UpdateEpisodeSerializer,
        responses={
            200: OpenApiResponse(
                CompleteEpisodeSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved media details example",
                        description="Retrieved media details example",
                        summary="Retrieved media details example",
                        value={
                            "id": 3612,
                            "media_id": "12345",
                            "source": "tmdb",
                            "source_url": "https://www.themoviedb.org/tv/12345/season/1/episode/1",
                            "media_type": "episode",
                            "title": "Destination Unknown",
                            "max_progress": 1,
                            "image": "https://image.tmdb.org/t/p/original/9xX4Ycm7vN0Qb4JtCjGfC2iC2nH.jpg",
                            "synopsis": "Van der Valk investigates a disappearance...",
                            "genres": ["Drama"],
                            "score": 7.0,
                            "score_count": 3,
                            "details": {
                                "air_date": "1972-09-13",
                                "episode_number": 1,
                                "season_number": 1,
                                "runtime": 50,
                                "episode_type": "standard",
                                "crew": [],
                                "guest_stars": [],
                            },
                            "related": {},
                            "item_id": "tv/tmdb/12345/1/1",
                            "parent_id": "tv/tmdb/12345/1",
                            "tracked": True,
                            "consumptions_number": 1,
                            "consumptions": [
                                {
                                    "consumption_id": 322,
                                    "created": None,
                                    "score": None,
                                    "progress": 1,
                                    "progressed_at": None,
                                    "status": 3,
                                    "start_date": None,
                                    "end_date": "2026-05-11T17:20:38Z",
                                    "notes": "",
                                }
                            ],
                            "lists": [],
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
    ):
        """Update a tracked episode item."""
        user = request.user
        episode = None

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while fetching user media.",
                HTTP.INTERNAL_SERVER_ERROR,
                e,
            )

        if not user_medias:
            return Response(
                {
                    "detail": "Episode not found or not tracked.",
                },
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        serializer = UpdateEpisodeSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        require_update_fields(data)

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        if not media_metadata:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )

        episode = next(
            (
                obj
                for obj in media_metadata.get("episodes") or []
                if obj["episode_number"] == int(episode_number)
            ),
            None,
        )
        if not episode:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )

        apply_validated_fields(media, data)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return error_response(HTTP.BAD_REQUEST.phrase, HTTP.BAD_REQUEST, e)

        media.refresh_from_db()

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
        )

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = CompleteEpisodeSerializer(data).data
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/changes_history/  # noqa: E501, W505
class MediaEpisodeChangesHistoryView(drf_views.APIView):
    """Changes history episode view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedChangesHistoryResponseSerializer

    @extend_schema(
        operation_id="episode_changes_history_get",
        summary="Get episode changes history",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedChangesHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        value={
                            "pagination": {
                                "total": 2,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": 1226,
                                    "item_id": "tv/tmdb/245703/1/1",
                                    "timestamp": "2026-01-18T15:21:02.851911Z",
                                    "changes": [
                                        {
                                            "field": "end_date",
                                            "old_value": None,
                                            "new_value": "2026-01-18T15:15:00Z",
                                        }
                                    ],
                                },
                                {
                                    "id": 112,
                                    "item_id": "tv/tmdb/245703/1/1",
                                    "timestamp": "2026-01-15T15:33:03.502096Z",
                                    "changes": [
                                        {
                                            "field": "end_date",
                                            "old_value": None,
                                            "new_value": "2025-09-17T09:41:00Z",
                                        }
                                    ],
                                },
                            ],
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve changes history timeline entries for a specific episode."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            "episode",
            source,
            season_number=season_number,
            episode_number=episode_number,
        )

        if not user_medias:
            return Response(
                {"detail": "Episode not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, MediaTypes.EPISODE.value)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = ChangesHistoryEntrySerializer(
            paginated_data["results"],
            many=True,
            context={"media_type": MediaTypes.EPISODE.value},
        ).data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/  # noqa: E501, W505
class MediaEpisodeConsumptionHistoryView(drf_views.APIView):
    """Episode consumption history view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedHistoryResponseSerializer

    @extend_schema(
        operation_id="episode_consumption_history_get",
        summary="Get episode consumption history",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedHistoryResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [
                                {
                                    "consumption_id": 138,
                                    "created": "2026-03-19T10:31:55.759835Z",
                                    "score": None,
                                    "progress": 23,
                                    "progressed_at": "2026-03-19T10:31:00Z",
                                    "status": 3,
                                    "start_date": "2026-03-19T10:31:00Z",
                                    "end_date": "2026-03-19T10:31:00Z",
                                    "notes": "",
                                }
                            ],
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the history timeline for a specific episode of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        # TODO: missing sorting
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = HistorySerializer(
            paginated_data["results"],
            many=True,
        ).data
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/[consumption_id]/  # noqa: E501, W505
class MediaEpisodeConsumptionEntryDetailView(drf_views.APIView):
    """Episode consumption history entry detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = HistorySerializer

    @extend_schema(
        operation_id="episode_consumption_entry_delete",
        summary="Delete an episode consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to delete",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Consumption entry deleted successfully",
                examples=[
                    OpenApiExample(
                        "Consumption entry deleted example",
                        description="Consumption entry deleted example",
                        summary="Consumption entry deleted example",
                        value=None,
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Delete a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{e!s}"},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        consumption.delete()

        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="episode_consumption_entry_get",
        summary="Get an episode consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to retrieve",
            ),
        ],
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def get(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Retrieve a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{e!s}"},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = HistorySerializer(
            consumption,
        ).data
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        operation_id="episode_consumption_entry_patch",
        summary="Update an episode consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to update",
            ),
        ],
        request=UpdateEpisodeSerializer,
        responses={
            200: OpenApiResponse(
                HistorySerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Example response",
                        description="Example response",
                        summary="Example response",
                        value={
                            "consumption_id": 312,
                            "created": "2026-03-19T10:31:55.747255Z",
                            "score": 10.0,
                            "progress": 26,
                            "progressed_at": "2026-03-19T10:41:00Z",
                            "status": 1,
                            "start_date": "2026-03-19T10:31:00Z",
                            "end_date": "2026-03-19T10:41:00Z",
                            "notes": "aSDASDF",
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Update a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Episodes are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{e!s}"},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serializer = UpdateEpisodeSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        require_update_fields(data)

        apply_validated_fields(consumption, data)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return error_response(HTTP.BAD_REQUEST.phrase, HTTP.BAD_REQUEST, e)

        consumption.refresh_from_db()

        serialized_data = HistorySerializer(
            consumption,
        ).data
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/
class MediaEpisodeListsView(drf_views.APIView):
    """Episode lists view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedListsMinimizedResponseSerializer

    @extend_schema(
        operation_id="episode_lists_get",
        summary="Get episode lists",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedListsMinimizedResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieve lists example",
                        description="Retrieve lists example",
                        summary="Retrieve lists example",
                        value={
                            "pagination": {
                                "total": 1,
                                "limit": 20,
                                "offset": 0,
                                "next": None,
                                "previous": None,
                            },
                            "results": [{"list_id": 2, "list_item_id": 28}],
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the lists that a specific episode is in."""
        user = request.user

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": " Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/[list_id]/  # noqa: E501, W505
class MediaEpisodeListDetailView(drf_views.APIView):
    """Episode list detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ListMinimizedSerializer

    @extend_schema(
        operation_id="episode_list_detail_delete",
        summary="Remove episode from list",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Episode removed from the list successfully.",
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        list_id,
    ):
        """Remove a specific episode from a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": " Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item_by_media(
                media_id,
                source,
                MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="episode_list_detail_put",
        summary="Add episode to list",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(
                ListMinimizedSerializer(many=True),
                description="Season added to list successfully",
                examples=[
                    OpenApiExample(
                        "Season added to list example",
                        description="Season added to list example",
                        summary="Season added to list example",
                        value={"list_id": 2, "list_item_id": 28},
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def put(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        list_id,
    ):
        """Add a specific episode to a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": " Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_edit(user):
            return Response(
                {"detail": HTTP.FORBIDDEN.phrase},
                status=HTTP.FORBIDDEN,
            )

        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type="episode",
                season_number=season_number,
                episode_number=episode_number,
            )
        except Item.DoesNotExist:
            return Response(
                {"detail": "Media not found."},
                status=HTTP.NOT_FOUND,
            )

        if user_list.items.filter(id=item.id).exists():
            return Response(
                {"detail": "Media already in the list."},
                status=HTTP.CONFLICT,
            )

        user_list.items.add(item)

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            season_number=season_number,
            episode_number=episode_number,
        )

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/sync/
class MediaEpisodeSyncView(drf_views.APIView):
    """Sync episode view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ApiMessageResponseSerializer

    @extend_schema(
        operation_id="episode_sync_post",
        summary="Sync episode metadata",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            EpisodeNumberParam,
        ],
        request=None,
        responses={
            202: OpenApiResponse(
                ApiMessageResponseSerializer,
                description="Accepted",
                examples=[
                    OpenApiExample(
                        "Metadata sync accepted example",
                        description="Metadata sync accepted example",
                        summary="Metadata sync accepted example",
                        value={"detail": "Metadata synced successfully."},
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            404: NotFoundResponse,
            429: TooManyRequestsResponse,
            500: InternalServerErrorResponse,
        },
    )
    def post(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,  # noqa: ARG002
    ):
        """Redirect episode sync to season sync."""
        season_sync = MediaSeasonSyncView()
        return season_sync.post(
            request,
            media_type=media_type,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )
