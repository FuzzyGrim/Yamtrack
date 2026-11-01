from http import HTTPStatus as HTTP  # noqa: N814

from django.conf import settings
from django.core.cache import cache
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.changes_history_processor import (
    get_changes_history_entries,
)
from api.examples import (
    ADDED_TO_LIST_EXAMPLE,
    CONSUMPTION_EXAMPLE,
    CONSUMPTION_HISTORY_EXAMPLE,
    MEDIA_LISTS_EXAMPLE,
    METADATA_SYNCED_EXAMPLE,
    SEASON_CHANGES_HISTORY_EXAMPLE,
    SEASON_DETAILS_EXAMPLE,
    SEASON_EPISODES_EXAMPLE,
)
from api.helpers import (
    apply_validated_fields,
    check_source_type,
    check_valid_type,
    error_response,
    get_item_lists,
    get_lists_by_number,
    paginate_data,
    parse_limit_offset,
    require_update_fields,
)
from api.schemas import (
    BadRequestResponse,
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
    CompleteMediaSerializer,
    EpisodeSerializer,
    HistorySerializer,
    ListMinimizedSerializer,
    PaginatedChangesHistoryResponseSerializer,
    PaginatedEpisodesSerializer,
    PaginatedHistoryResponseSerializer,
    PaginatedListsMinimizedResponseSerializer,
    UpdateSeasonSerializer,
)
from app.models import BasicMedia, Item, MediaTypes, Sources
from app.providers import services, tmdb
from lists.models import CustomList, CustomListItem


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/
class MediaSeasonDetailView(drf_views.APIView):
    """Season view."""

    serializer_class = CompleteMediaSerializer

    @extend_schema(
        operation_id="season_detail_delete",
        summary="Delete season",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
        ],
        responses={
            204: OpenApiResponse(description="Media item deleted successfully"),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def delete(self, request, media_type, source, media_id, season_number):
        """Delete a tracked season item for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                MediaTypes.SEASON.value,
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    @extend_schema(
        operation_id="season_detail_get",
        summary="Get season details",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
        ],
        responses={
            200: OpenApiResponse(
                CompleteMediaSerializer,
                description="Successful response",
                examples=[SEASON_DETAILS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve details of a specific season for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                {"detail": "Season not found."},
                status=HTTP.NOT_FOUND,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                MediaTypes.SEASON.value,
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        season_episodes = list(
            BasicMedia.objects.get_season_episodes(
                user,
                media_id,
                source,
                season_number=season_number,
            ),
        )
        episode_lists_by_number = BasicMedia.objects.get_season_episode_lists_by_number(
            user,
            season_episodes,
        )
        for tracked in season_episodes:
            episode_number = getattr(tracked.item, "episode_number", None)
            if episode_number is not None:
                tracked.lists = episode_lists_by_number.get(episode_number, [])

        episodes_by_number = {
            tracked.item.episode_number: tracked
            for tracked in season_episodes
            if getattr(tracked, "item", None) is not None
            and tracked.item.episode_number is not None
        }

        lists = get_item_lists(
            user,
            media_id,
            source,
            MediaTypes.SEASON.value,
            season_number=season_number,
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "episodes": episodes_by_number,
            "lists": lists,
        }

        serialized = CompleteMediaSerializer(
            data,
        ).data
        return Response(serialized, status=HTTP.OK)

    @extend_schema(
        operation_id="season_detail_patch",
        summary="Update season",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
        ],
        request=UpdateSeasonSerializer,
        responses={
            200: OpenApiResponse(
                CompleteMediaSerializer,
                description="Successful response",
                examples=[SEASON_DETAILS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def patch(self, request, media_type, source, media_id, season_number):
        """Update a tracked season item."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                MediaTypes.SEASON.value,
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        serializer = UpdateSeasonSerializer(data=request.data, partial=True)
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

        apply_validated_fields(media, data)

        try:
            media.save()
        except Exception:  # noqa: BLE001
            return Response(
                {"detail": "Failed to update season."},
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        lists = get_item_lists(
            user,
            media_id,
            source,
            MediaTypes.SEASON.value,
            season_number=season_number,
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = CompleteMediaSerializer(
            data,
        ).data
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/changes_history/
class MediaSeasonChangesHistoryView(drf_views.APIView):
    """Changes history season view."""

    serializer_class = PaginatedChangesHistoryResponseSerializer

    @extend_schema(
        operation_id="season_changes_history_get",
        summary="Get season changes history",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedChangesHistoryResponseSerializer,
                description="Successful response",
                examples=[SEASON_CHANGES_HISTORY_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve changes history timeline entries for a season."""
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

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            MediaTypes.SEASON.value,
            source,
            season_number=season_number,
        )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, MediaTypes.SEASON.value)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = ChangesHistoryEntrySerializer(
            paginated_data["results"],
            many=True,
            context={"media_type": MediaTypes.SEASON.value},
        ).data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/episodes/
class MediaSeasonEpisodesView(drf_views.APIView):
    """Season episodes view."""

    serializer_class = PaginatedEpisodesSerializer

    @extend_schema(
        operation_id="season_episodes_get",
        summary="Get season episodes",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedEpisodesSerializer,
                description="Successful response",
                examples=[SEASON_EPISODES_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the episodes for a specific season of a tv serie."""
        user = request.user
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
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            media_metadata = services.get_media_metadata(
                MediaTypes.SEASON.value,
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Failed to retrieve season episodes.", HTTP.INTERNAL_SERVER_ERROR, e
            )

        episodes = []
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episodes = media_metadata["episodes"]

        paginated = paginate_data(request, episodes, limit, offset)

        episode_numbers = [
            episode.get("episode_number")
            for episode in paginated["results"]
            if episode.get("episode_number") is not None
        ]
        lists_by_number = get_lists_by_number(
            user,
            media_id,
            source,
            episode_numbers,
            season_number=season_number,
        )

        tracked_by_number = {}
        if episode_numbers:
            tracked_episodes = BasicMedia.objects.get_season_episodes(
                user,
                media_id,
                source,
                season_number=season_number,
                episode_numbers=episode_numbers,
            )
            for tracked in tracked_episodes:
                item = getattr(tracked, "item", None)
                tracked_number = getattr(item, "episode_number", None)
                if (
                    tracked_number is not None
                    and tracked_number in episode_numbers
                    and tracked_number not in tracked_by_number
                ):
                    tracked_by_number[tracked_number] = tracked

        paginated["results"] = EpisodeSerializer(
            paginated["results"],
            many=True,
            context={
                "source": source,
                "tracked_episodes": tracked_by_number,
                "lists_by_number": lists_by_number,
            },
        ).data
        return Response(paginated, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/
class MediaSeasonConsumptionHistoryView(drf_views.APIView):
    """Season consumption history view."""

    serializer_class = PaginatedHistoryResponseSerializer

    @extend_schema(
        operation_id="season_consumption_history_get",
        summary="Get season consumption history",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
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
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the history timeline for a specific season of a tv serie."""
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
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.SEASON.value,
                source,
                season_number=season_number,
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


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/[consumption_id]/  # noqa: E501, W505
class MediaSeasonConsumptionEntryDetailView(drf_views.APIView):
    """Season consumption history entry detail view."""

    serializer_class = HistorySerializer

    @extend_schema(
        operation_id="season_consumption_entry_delete",
        summary="Delete a season consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
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
        consumption_id,
    ):
        """Delete a specific consumption history entry for a specific season."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.SEASON.value,
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Failed to retrieve consumption entry.", HTTP.INTERNAL_SERVER_ERROR, e
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
        operation_id="season_consumption_entry_get",
        summary="Get a season consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
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
    def get(self, request, media_type, source, media_id, season_number, consumption_id):
        """Retrieve a specific consumption history entry for a specific season."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.SEASON.value,
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
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
        operation_id="season_consumption_entry_patch",
        summary="Update a season consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to update",
            ),
        ],
        request=UpdateSeasonSerializer,
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
        consumption_id,
    ):
        """Update a specific consumption history entry for a specific season."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                MediaTypes.SEASON.value,
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serializer = UpdateSeasonSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        require_update_fields(data)

        apply_validated_fields(consumption, data)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return error_response(HTTP.BAD_REQUEST.phrase, HTTP.BAD_REQUEST, e)

        consumption.refresh_from_db()

        serialized_data = HistorySerializer(consumption).data
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/lists/
class MediaSeasonListsView(drf_views.APIView):
    """Season lists view."""

    serializer_class = PaginatedListsMinimizedResponseSerializer

    @extend_schema(
        operation_id="season_lists_get",
        summary="Get season lists",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
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
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the lists that a specific season is in."""
        user = request.user

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
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            MediaTypes.SEASON.value,
            season_number=season_number,
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/lists/[list_id]/
class MediaSeasonListDetailView(drf_views.APIView):
    """Season list detail view."""

    serializer_class = ListMinimizedSerializer

    @extend_schema(
        operation_id="season_list_detail_delete",
        summary="Remove season from list",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Season removed from list successfully",
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
        },
    )
    def delete(self, request, media_type, source, media_id, season_number, list_id):
        """Remove a specific season from a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
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
                MediaTypes.SEASON.value,
                season_number=season_number,
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="season_list_detail_put",
        summary="Add season to list",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
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
    def put(self, request, media_type, source, media_id, season_number, list_id):
        """Add a specific season to a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                    "detail": f" Cannot query `{source}` for `{media_type}` media type",
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
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
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
        )

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/sync/
class MediaSeasonSyncView(drf_views.APIView):
    """Sync season."""

    serializer_class = ApiMessageResponseSerializer

    @extend_schema(
        operation_id="season_sync_post",
        summary="Sync season metadata",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
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
    def post(self, _, media_type, source, media_id, season_number):
        """Trigger sync of metadata from provider (non-manual sources only)."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": "Seasons are supported only for 'tv' media type.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if source == Sources.MANUAL.value:
            return Response(
                {"detail": "Manual items cannot be synced."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f" Cannot sync `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        cache_key = f"{source}_season_{media_id}_{season_number}"

        ttl = cache.ttl(cache_key)
        if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
            response = Response(
                {
                    "detail": (
                        "The data was recently synced, please wait a few seconds."
                    ),
                },
                status=HTTP.TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = str(ttl)
            return response

        cache.delete(cache_key)

        try:
            metadata = services.get_media_metadata(
                MediaTypes.SEASON.value,
                media_id,
                source,
                [season_number],
            )

            item, _ = Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                defaults={
                    "title": metadata["title"],
                    "image": metadata["image"],
                },
            )

            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                )
            }

            episodes_to_update = []

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)

            if episodes_to_update:
                Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )

            item.fetch_releases(delay=False)

            return Response(
                {"detail": "Metadata synced successfully."},
                status=HTTP.ACCEPTED,
            )

        except Exception as e:  # noqa: BLE001
            return error_response(
                "An error occurred while syncing metadata.",
                HTTP.INTERNAL_SERVER_ERROR,
                e,
            )
