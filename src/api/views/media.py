from http import HTTPStatus as HTTP  # noqa: N814

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.changes_history_processor import (
    delete_changes_history_entry,
    get_changes_history_entries,
    get_changes_history_entry,
)
from api.examples import (
    ADDED_TO_LIST_EXAMPLE,
    CHANGES_HISTORY_RECORD_EXAMPLE,
    CONSUMPTION_EXAMPLE,
    CONSUMPTION_HISTORY_EXAMPLE,
    MEDIA_CHANGES_HISTORY_EXAMPLE,
    MEDIA_DETAILS_EXAMPLE,
    MEDIA_LIST_EXAMPLE,
    MEDIA_LISTS_EXAMPLE,
    METADATA_SYNCED_EXAMPLE,
    RECOMMENDATIONS_EXAMPLE,
    SEASONS_EXAMPLE,
    TRACKED_MEDIA_EXAMPLE,
)
from api.helpers import (
    MEDIA_TYPE_COMPLETE_MODEL_MAP,
    InvalidSortError,
    apply_validated_fields,
    build_lists_by_item_id,
    error_response,
    fetch_results_all_types,
    fetch_results_for_type,
    get_item_lists,
    get_lists_by_number,
    paginate_data,
    parse_excluded_items,
    parse_limit_offset,
    parse_sort_filter,
    parse_status_param,
    require_update_fields,
    validate_media_path,
)
from api.schemas import (
    BadRequestResponse,
    ConflictResponse,
    ForbiddenResponse,
    MediaIdParam,
    MediaSortParam,
    MediaTypeCompleteParam,
    MediaTypeCompleteQueryParam,
    MediaTypeParam,
    NotFoundResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    SourceCompleteParam,
    TooManyRequestsResponse,
)
from api.serializers import (
    UPDATE_MEDIA_SERIALIZERS,
    ApiMessageResponseSerializer,
    ChangesHistoryEntrySerializer,
    CompleteMediaSerializer,
    HistorySerializer,
    ListMinimizedSerializer,
    MediaSerializer,
    MixedMediaSerializer,
    PaginatedChangesHistoryResponseSerializer,
    PaginatedHistoryResponseSerializer,
    PaginatedListsMinimizedResponseSerializer,
    PaginatedMediaSerializer,
    RelatedResponseSerializer,
    TrackMediaSerializer,
)
from app.forms import ManualItemForm, get_form_class
from app.models import BasicMedia, Item, MediaTypes, Sources
from app.providers import services
from lists.models import CustomList, CustomListItem
from users.models import MediaStatusChoices


# /api/v1/media/
class MediaListView(drf_views.APIView):
    """List media view."""

    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="media_list_get",
        summary="Get media list",
        parameters=[
            OpenApiParameter(
                name="exclude",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Comma-separated list of media types to exclude (excluded"
                    "by default are seasons and episodes)."
                ),
                enum=[media_type.value for media_type in MediaTypes],
            ),
            MediaTypeCompleteQueryParam,
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by tracking status.",
                enum=[status.value for status in MediaStatusChoices],
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search query to filter media by title, case insensitive.",
            ),
            MediaSortParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedMediaSerializer,
                description="Successful response",
                examples=[MEDIA_LIST_EXAMPLE],
            ),
            400: BadRequestResponse,
        },
    )
    def get(self, request):
        """Retrieve the list of media for the authenticated user."""
        user = request.user
        media_type = request.GET.get("media_type")
        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        exclude = parse_excluded_items(request)

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        status = parse_status_param(status)
        if status is None:
            return Response(
                {"detail": "Invalid status"},
                status=HTTP.BAD_REQUEST,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        if media_type:
            error = validate_media_path(media_type, complete=True)
            if error is not None:
                return error

        try:
            if media_type:
                results = fetch_results_for_type(
                    user,
                    media_type,
                    status,
                    sort,
                    sort_order,
                    search,
                )
            else:
                # Exclude EPISODES and SEASONS from results by default
                # to declutter the results
                results = fetch_results_all_types(
                    user,
                    status,
                    sort,
                    sort_order,
                    search,
                    exclude,
                )
        except InvalidSortError:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.BAD_REQUEST,
            )

        paginated_data = paginate_data(request, results, limit, offset)
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = MixedMediaSerializer(
            paginated_data["results"],
            context={
                "request": request,
                "lists_by_item_id": lists_by_item_id,
            },
            many=True,
        ).data
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/
class MediaTypeListView(drf_views.APIView):
    """List media by type view."""

    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="media_type_list_get",
        summary="Get media list by type",
        parameters=[
            MediaTypeCompleteParam,
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by tracking status.",
                enum=[status.value for status in MediaStatusChoices],
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search query to filter media by title, case insensitive.",
            ),
            MediaSortParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedMediaSerializer,
                description="Successful response",
                examples=[MEDIA_LIST_EXAMPLE],
            ),
            400: BadRequestResponse,
        },
    )
    def get(self, request, media_type):
        """Retrieve the list of media of a specific media type."""
        user = request.user
        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        status = parse_status_param(status)
        if status is None:
            return Response(
                {"detail": "Invalid status"},
                status=HTTP.BAD_REQUEST,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        error = validate_media_path(media_type, complete=True)
        if error is not None:
            return error

        try:
            results = fetch_results_for_type(
                user,
                media_type,
                status,
                sort,
                sort_order,
                search,
            )
        except InvalidSortError:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.BAD_REQUEST,
            )

        paginated_data = paginate_data(request, results, limit, offset)
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = MixedMediaSerializer(
            paginated_data["results"],
            context={
                "request": request,
                "lists_by_item_id": lists_by_item_id,
            },
            many=True,
        ).data
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)

    @extend_schema(
        operation_id="media_type_list_post",
        summary="Track a new media item",
        parameters=[
            MediaTypeCompleteParam,
        ],
        request=TrackMediaSerializer,
        responses={
            201: OpenApiResponse(
                MediaSerializer,
                description="Media item tracked successfully",
                examples=[TRACKED_MEDIA_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
            409: ConflictResponse,
        },
    )
    def post(self, request, media_type):
        """Track a new media item of a specific media type."""
        error = validate_media_path(media_type, complete=True)
        if error is not None:
            return error

        if not request.data:
            return Response(
                {"detail": "Missing body."},
                status=HTTP.BAD_REQUEST,
            )

        serializer = TrackMediaSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        data["media_type"] = media_type
        data["status"] = data.get("status") or MediaStatusChoices.PLANNING

        source = data.get("source", Sources.MANUAL.value)

        if source == Sources.MANUAL.value:
            form = ManualItemForm(data, user=request.user)
            if not form.is_valid():
                return Response(
                    {
                        "detail": "Invalid form data.",
                        "errors": form.errors,
                    },
                    status=HTTP.BAD_REQUEST,
                )

            try:
                item = form.save()
            except IntegrityError:
                media_name = form.cleaned_data.get("title", "item")
                if form.cleaned_data.get("season_number"):
                    media_name += f" - Season {form.cleaned_data['season_number']}"
                if form.cleaned_data.get("episode_number"):
                    media_name += f" - Episode {form.cleaned_data['episode_number']}"
                return Response(
                    {"detail": f"Conflict. {media_name} already exists."},
                    status=HTTP.CONFLICT,
                )

            media_data = dict(data)
            media_data.update({"source": item.source, "media_id": item.media_id})
            media_form = get_form_class(item.media_type)(media_data)
            if not media_form.is_valid():
                item.delete()
                return Response(
                    {
                        "detail": "Invalid media data.",
                        "errors": media_form.errors,
                    },
                    status=HTTP.BAD_REQUEST,
                )

            media_form.instance.user = request.user
            media_form.instance.item = item
            if item.media_type == MediaTypes.SEASON.value:
                media_form.instance.related_tv = form.cleaned_data.get("parent_tv")
            elif item.media_type == MediaTypes.EPISODE.value:
                media_form.instance.related_season = form.cleaned_data.get(
                    "parent_season",
                )

            media_form.save()
            serialized_data = MediaSerializer(media_form.instance).data
            return Response(serialized_data, status=HTTP.CREATED)

        media_id = data.get("media_id")
        if not media_id:
            return Response(
                {
                    "detail": "'media_id' is required for provider sources.",
                },
                status=HTTP.BAD_REQUEST,
            )

        error = validate_media_path(media_type, source, complete=True)
        if error is not None:
            return error

        if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
            return Response(
                {
                    "detail": (
                        f"Tracking `{media_type}` from a provider is not supported yet."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        season_number = data.get("season_number")

        try:
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Internal Server Error.", HTTP.INTERNAL_SERVER_ERROR, e
            )

        defaults = {"title": metadata.get("title"), "image": metadata.get("image")}
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults=defaults,
        )

        try:
            item.save()
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Internal Server Error.", HTTP.INTERNAL_SERVER_ERROR, e
            )

        model = MEDIA_TYPE_COMPLETE_MODEL_MAP.get(media_type)
        if model is None:
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        instance = model(item=item, user=request.user)

        media_data = dict(data)
        media_data.update({"source": item.source, "media_id": item.media_id})
        media_form = get_form_class(media_type)(media_data, instance=instance)
        if not media_form.is_valid():
            return Response(
                {
                    "detail": "Invalid media data.",
                    "errors": media_form.errors,
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                media_form.save()
        except IntegrityError:
            return Response(
                {"detail": f"Conflict. {item.title} is already tracked."},
                status=HTTP.CONFLICT,
            )
        serialized_data = MixedMediaSerializer(media_form.instance).data
        return Response(serialized_data, status=HTTP.CREATED)


# /api/v1/media/[media_type]/[source]/[media_id]/
class MediaDetailView(drf_views.APIView):
    """Media view."""

    serializer_class = CompleteMediaSerializer

    @extend_schema(
        operation_id="media_detail_delete",
        summary="Delete media",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
        ],
        responses={
            204: OpenApiResponse(description="Media item deleted successfully"),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def delete(self, request, media_type, source, media_id):
        """Delete a tracked media item and all its consumptions."""
        user = request.user

        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Internal Server Error.", HTTP.INTERNAL_SERVER_ERROR, e
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    @extend_schema(
        operation_id="media_detail_get",
        summary="Get media details",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
        ],
        responses={
            200: OpenApiResponse(
                CompleteMediaSerializer,
                description="Successful response",
                examples=[MEDIA_DETAILS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve details of a specific media for the authenticated user."""
        user = request.user

        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            media_metadata["related"].pop("recommendations")

        seasons_by_number = None
        if media_type == MediaTypes.TV.value:
            serie_seasons = list(
                BasicMedia.objects.get_serie_seasons(
                    user,
                    media_id,
                    source,
                ),
            )
            season_lists_by_number = (
                BasicMedia.objects.get_serie_season_lists_by_number(
                    user,
                    serie_seasons,
                )
            )
            for tracked in serie_seasons:
                season_number = getattr(tracked.item, "season_number", None)
                if season_number is not None:
                    tracked.lists = season_lists_by_number.get(season_number, [])

            seasons_by_number = {
                tracked.item.season_number: tracked
                for tracked in serie_seasons
                if getattr(tracked, "item", None) is not None
                and tracked.item.season_number is not None
            }

        lists = get_item_lists(user, media_id, source, media_type)

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "seasons": seasons_by_number,
            "lists": lists,
        }

        serialized = CompleteMediaSerializer(
            data,
        ).data
        return Response(serialized, status=HTTP.OK)

    @extend_schema(
        operation_id="media_detail_patch",
        summary="Update media",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
        ],
        request=PolymorphicProxySerializer(
            component_name="HistoryUpdateRequest",
            serializers=list(UPDATE_MEDIA_SERIALIZERS.values()),
            resource_type_field_name=None,
        ),
        responses={
            200: OpenApiResponse(
                CompleteMediaSerializer,
                description="Successful response",
                examples=[MEDIA_DETAILS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def patch(self, request, media_type, source, media_id):
        """Update a tracked media item."""
        user = request.user

        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        serializer_class = UPDATE_MEDIA_SERIALIZERS.get(media_type)
        if serializer_class is None:
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        serializer = serializer_class(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        require_update_fields(data)

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Internal Server Error.", HTTP.INTERNAL_SERVER_ERROR, e
            )

        apply_validated_fields(media, data)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return error_response("Failed to update media.", HTTP.BAD_REQUEST, e)

        media.refresh_from_db()

        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            media_metadata["related"].pop("recommendations")

        lists = get_item_lists(user, media_id, source, media_type)

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = CompleteMediaSerializer(
            data,
        ).data
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/changes_history/
class MediaChangesHistoryView(drf_views.APIView):
    """Media changes history view."""

    serializer_class = PaginatedChangesHistoryResponseSerializer

    @extend_schema(
        operation_id="media_changes_history_get",
        summary="Get media changes history",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedChangesHistoryResponseSerializer,
                description="Successful response",
                examples=[MEDIA_CHANGES_HISTORY_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve changes history timeline entries for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
        )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, media_type)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = ChangesHistoryEntrySerializer(
            paginated_data["results"],
            many=True,
            context={"media_type": media_type},
        ).data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/changes_history/[media_type]/[history_id]
class MediaTypeChangesHistoryDetailView(drf_views.APIView):
    """Changes history record view."""

    serializer_class = ChangesHistoryEntrySerializer

    @extend_schema(
        operation_id="changes_history_entry_get",
        summary="Get changes history record",
        parameters=[
            MediaTypeCompleteParam,
            OpenApiParameter(
                name="history_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="The ID of the changes history record to retrieve.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                ChangesHistoryEntrySerializer,
                description="Successful response",
                examples=[CHANGES_HISTORY_RECORD_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, history_id):
        """Retrieve the changes history record for a specific media."""
        error = validate_media_path(media_type, complete=True)
        if error is not None:
            return error

        try:
            record = get_changes_history_entry(media_type, history_id, request.user)
        except (ObjectDoesNotExist, ValueError):
            return Response(
                {"detail": "History record not found"},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = ChangesHistoryEntrySerializer(
            record, context={"media_type": media_type}
        ).data
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        operation_id="changes_history_entry_delete",
        summary="Delete changes history record",
        parameters=[
            MediaTypeCompleteParam,
            OpenApiParameter(
                name="history_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="The ID of the changes history record to delete.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="History record deleted successfully",
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def delete(self, request, media_type, history_id):
        """Delete the changes history record for a specific media."""
        error = validate_media_path(media_type, complete=True)
        if error is not None:
            return error

        try:
            delete_changes_history_entry(media_type, history_id, request.user)
        except (ObjectDoesNotExist, ValueError):
            return Response(
                {"detail": "History record not found"},
                status=HTTP.NOT_FOUND,
            )

        return Response(status=HTTP.NO_CONTENT)


# /api/v1/media/[media_type]/[source]/[media_id]/history/
class MediaConsumptionHistoryView(drf_views.APIView):
    """Media consumption history view."""

    serializer_class = PaginatedHistoryResponseSerializer

    @extend_schema(
        operation_id="media_consumption_history_get",
        summary="Get media consumption history",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
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
    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Internal Server Error.", HTTP.INTERNAL_SERVER_ERROR, e
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
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
        )
        paginated_data["results"] = consumptions.data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/history/[consumption_id]/
class MediaConsumptionEntryDetailView(drf_views.APIView):
    """Media consumption history entry detail view."""

    serializer_class = HistorySerializer

    @extend_schema(
        operation_id="media_consumption_entry_delete",
        summary="Delete a media consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
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
    def delete(self, request, media_type, source, media_id, consumption_id):
        """Delete a specific consumption history entry for a specific media."""
        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                "Internal Server Error.", HTTP.INTERNAL_SERVER_ERROR, e
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
        operation_id="media_consumption_entry_get",
        summary="Get a media consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
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
    def get(self, request, media_type, source, media_id, consumption_id):
        """Retrieve a specific consumption history entry for a specific media."""
        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": " Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = HistorySerializer(
            consumption,
        ).data
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        operation_id="media_consumption_entry_patch",
        summary="Update a media consumption history entry",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            OpenApiParameter(
                name="consumption_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the consumption entry to update",
            ),
        ],
        request=PolymorphicProxySerializer(
            component_name="HistoryUpdateRequest",
            serializers=list(UPDATE_MEDIA_SERIALIZERS.values()),
            resource_type_field_name=None,
        ),
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
    def patch(self, request, media_type, source, media_id, consumption_id):
        """Update a specific consumption history entry for a specific media."""
        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
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

        serializer_class = UPDATE_MEDIA_SERIALIZERS.get(media_type)
        if serializer_class is None:
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        serializer = serializer_class(data=request.data, partial=True)
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


# /api/v1/media/[media_type]/[source]/[media_id]/lists/
class MediaListsView(drf_views.APIView):
    """Media lists view."""

    serializer_class = PaginatedListsMinimizedResponseSerializer

    @extend_schema(
        operation_id="media_lists_get",
        summary="Get media lists",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
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
    def get(self, request, media_type, source, media_id):
        """Retrieve the lists that a specific media is in."""
        user = request.user

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        error = validate_media_path(media_type, source)
        if error is not None:
            return error
        lists = get_item_lists(user, media_id, source, media_type)
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/[list_id]/
class MediaListDetailView(drf_views.APIView):
    """Media list detail view."""

    serializer_class = ListMinimizedSerializer

    @extend_schema(
        operation_id="media_list_detail_delete",
        summary="Remove media from a specific list",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list.",
            ),
        ],
        responses={
            204: OpenApiResponse(
                description="Media removed from list successfully",
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
        },
    )
    def delete(self, request, media_type, source, media_id, list_id):
        """Remove a specific media from a specific list."""
        user = request.user

        error = validate_media_path(media_type, source)
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
                media_type,
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="media_list_detail_put",
        summary="Put media in a specific list",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
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
                description="Successful response",
                examples=[ADDED_TO_LIST_EXAMPLE],
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
        },
    )
    def put(self, request, media_type, source, media_id, list_id):
        """Add a specific media to a specific list."""
        user = request.user

        error = validate_media_path(media_type, source)
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
                media_type=media_type,
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

        lists = get_item_lists(user, media_id, source, media_type)

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/recommendations/
class MediaRecommendationsView(drf_views.APIView):
    """Media recommendations view."""

    serializer_class = RelatedResponseSerializer

    @extend_schema(
        operation_id="media_recommendations_get",
        summary="Get media recommendations",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
        ],
        responses={
            200: OpenApiResponse(
                RelatedResponseSerializer(many=True),
                description="Successful response",
                examples=[RECOMMENDATIONS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, _, media_type, source, media_id):
        """Retrieve recommendations for a specific media."""
        error = validate_media_path(media_type, source)
        if error is not None:
            return error

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        recommendations = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            recommendations = media_metadata["related"]["recommendations"]

        serialized_data = RelatedResponseSerializer(recommendations, many=True).data
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/seasons/
class MediaSeasonsView(drf_views.APIView):
    """Media seasons view."""

    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="media_seasons_get",
        summary="Get media seasons",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedMediaSerializer,
                description="Successful response",
                examples=[SEASONS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
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

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        seasons = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "seasons" in media_metadata["related"]
        ):
            seasons = media_metadata["related"]["seasons"]

        paginated_data = paginate_data(request, seasons, limit, offset)
        season_numbers = [
            season.get("season_number")
            for season in paginated_data["results"]
            if season.get("season_number") is not None
        ]
        lists_by_number = get_lists_by_number(user, media_id, source, season_numbers)

        items_by_number = {
            item.season_number: item
            for item in Item.objects.filter(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                season_number__in=season_numbers,
            )
        }

        tracked_by_number = {}
        if season_numbers:
            tracked_seasons = BasicMedia.objects.get_serie_seasons(
                user,
                media_id,
                source,
                season_numbers=season_numbers,
            )
            for tracked in tracked_seasons:
                item = getattr(tracked, "item", None)
                tracked_number = getattr(item, "season_number", None)
                if (
                    tracked_number is not None
                    and tracked_number in season_numbers
                    and tracked_number not in tracked_by_number
                ):
                    tracked_by_number[tracked_number] = tracked

        season_media_entries = []
        for season in paginated_data["results"]:
            season_number = season.get("season_number")
            tracked = tracked_by_number.get(season_number)
            lists = lists_by_number.get(season_number, [])

            if tracked is not None:
                tracked.lists = lists
                if getattr(tracked, "item", None) is None:
                    tracked.item = items_by_number.get(season_number)
                season_media_entries.append(tracked)
                continue

            item = items_by_number.get(season_number)
            if item is None:
                item = Item(
                    media_id=media_id,
                    source=source,
                    media_type=MediaTypes.SEASON.value,
                    title=season.get("season_title") or season.get("title") or "",
                    image=season.get("image") or settings.IMG_NONE,
                    season_number=season_number,
                )

            season_media_entries.append(
                type(
                    "TempMedia",
                    (),
                    {
                        "id": None,
                        "item": item,
                        "lists": lists,
                        "created_at": None,
                        "score": None,
                        "status": None,
                        "progress": None,
                        "progressed_at": None,
                        "start_date": None,
                        "end_date": None,
                        "notes": None,
                    },
                )(),
            )

        paginated_data["results"] = MediaSerializer(
            season_media_entries,
            many=True,
        ).data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/sync/
class MediaSyncView(drf_views.APIView):
    """Sync media view."""

    serializer_class = ApiMessageResponseSerializer

    @extend_schema(
        operation_id="media_sync_post",
        summary="Sync media metadata",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
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
    def post(self, _, media_type, source, media_id):
        """Trigger sync of metadata from provider (non-manual sources only)."""
        error = validate_media_path(media_type, source, sync=True)
        if error is not None:
            return error

        cache_key = f"{source}_{media_type}_{media_id}"

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
                media_type,
                media_id,
                source,
            )

            item, _ = Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults={
                    "title": metadata["title"],
                    "image": metadata["image"],
                },
            )

            item.fetch_releases(delay=False)

            return Response(
                {"detail": "Metadata synced successfully."},
                status=HTTP.ACCEPTED,
            )

        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )
