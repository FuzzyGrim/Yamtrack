from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.changes_history_processor import get_changes_history_entries
from api.examples import (
    ADDED_TO_LIST_EXAMPLE,
    CONSUMPTION_EXAMPLE,
    CONSUMPTION_HISTORY_EXAMPLE,
    EPISODE_CHANGES_HISTORY_EXAMPLE,
    EPISODE_DETAILS_EXAMPLE,
    MEDIA_LISTS_EXAMPLE,
    METADATA_SYNCED_EXAMPLE,
)
from api.helpers import (
    apply_validated_fields,
    error_response,
    get_item_lists,
    paginate_data,
    parse_limit_offset,
    require_update_fields,
    validate_media_path,
)
from api.schemas import (
    BadRequestResponse,
    EpisodeNumberParam,
    ForbiddenResponse,
    MediaIdParam,
    MediaTypeParam,
    NotFoundResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    SeasonNumberParam,
    SourceCompleteParam,
    TooManyRequestsResponse,
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
            404: NotFoundResponse,
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

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                MediaTypes.EPISODE.value,
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
                examples=[EPISODE_DETAILS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve details of a specific episode for the authenticated user."""
        user = request.user
        episode = None

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        try:
            media_metadata = services.get_media_metadata(
                MediaTypes.SEASON.value,
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
                MediaTypes.EPISODE.value,
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
            MediaTypes.EPISODE.value,
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
                examples=[EPISODE_DETAILS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
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

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                MediaTypes.EPISODE.value,
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
                MediaTypes.SEASON.value,
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
            MediaTypes.EPISODE.value,
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
                examples=[EPISODE_CHANGES_HISTORY_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve changes history timeline entries for a specific episode."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            MediaTypes.EPISODE.value,
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
                examples=[CONSUMPTION_HISTORY_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the history timeline for a specific episode of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.EPISODE.value,
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

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
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
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
        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.EPISODE.value,
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while fetching media.", HTTP.INTERNAL_SERVER_ERROR, e
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
                examples=[CONSUMPTION_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
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
        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.EPISODE.value,
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while fetching media.", HTTP.INTERNAL_SERVER_ERROR, e
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
                examples=[CONSUMPTION_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
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
        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.EPISODE.value,
        )
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.EPISODE.value,
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while fetching media.", HTTP.INTERNAL_SERVER_ERROR, e
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
                examples=[MEDIA_LISTS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the lists that a specific episode is in."""
        user = request.user

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.SEASON.value,
        )
        if error is not None:
            return error

        lists = get_item_lists(
            user,
            media_id,
            source,
            MediaTypes.EPISODE.value,
            season_number=season_number,
            episode_number=episode_number,
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/[list_id]/  # noqa: E501, W505
class MediaEpisodeListDetailView(drf_views.APIView):
    """Episode list detail view."""

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
            403: ForbiddenResponse,
            404: NotFoundResponse,
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

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.SEASON.value,
        )
        if error is not None:
            return error

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
                examples=[ADDED_TO_LIST_EXAMPLE],
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
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

        error = validate_media_path(
            media_type,
            source,
            child_type=MediaTypes.SEASON.value,
        )
        if error is not None:
            return error

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
                media_type=MediaTypes.EPISODE.value,
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
                examples=[METADATA_SYNCED_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
            429: TooManyRequestsResponse,
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
