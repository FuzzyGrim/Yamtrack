from http import HTTPStatus as HTTP  # noqa: N814

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.authentication import APIKeyAuthentication, BearerAuthentication
from api.helpers import (
    InvalidSortError,
    apply_aggregated_sort,
    apply_list_sort,
    build_lists_by_item_id,
    get_latest_medias_by_item_id,
    paginate_data,
    parse_limit_offset,
    parse_sort_filter,
    resolve_collaborators,
)
from api.schemas import (
    BadRequestResponse,
    ForbiddenResponse,
    InternalServerErrorResponse,
    ListSearchParam,
    ListSortParam,
    MediaSortParam,
    NotFoundResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    UnauthorizedResponse,
)
from api.serializers import (
    CompleteEpisodeSerializer,
    CompleteMediaSerializer,
    ListCreateRequestSerializer,
    ListSerializer,
    ListUpdateRequestSerializer,
    MixedMediaSerializer,
    PaginatedListsResponseSerializer,
    PaginatedMediaSerializer,
)
from app.models import MediaTypes
from lists.models import CustomList, CustomListItem

from .episodes import MediaEpisodeDetailView
from .media import MediaDetailView
from .seasons import MediaSeasonDetailView


# /api/v1/lists/
class ListsView(drf_views.APIView):
    """Lists view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedListsResponseSerializer

    @extend_schema(
        operation_id="lists_get",
        summary="Get user's lists",
        parameters=[
            ListSearchParam,
            ListSortParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedListsResponseSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieve lists example",
                        description="Retrieve lists example",
                        summary="Retrieve lists example",
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
                                    "id": 1,
                                    "name": "Test1",
                                    "description": "This is a test list",
                                    "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                                    "owner": {"id": 1, "username": "admin"},
                                    "collaborators": [],
                                    "items_count": 17,
                                    "latest_update": "2026-03-23T21:17:32.705709Z",
                                },
                                {
                                    "id": 2,
                                    "name": "Test2",
                                    "description": "Ciaoo",
                                    "image": "https://image.tmdb.org/t/p/w500/xunXvzFlkf1GGgMkCySA9CCFumB.jpg",
                                    "owner": {"id": 1, "username": "admin"},
                                    "collaborators": [],
                                    "items_count": 27,
                                    "latest_update": "2026-04-08T15:58:11.652660Z",
                                },
                            ],
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            500: InternalServerErrorResponse,
        },
    )
    def get(self, request):
        """Retrieve the lists for the authenticated user."""
        user = request.user
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        custom_lists = CustomList.objects.get_user_lists_with_stats(
            user,
            search=search,
        )

        sort, sort_order = parse_sort_filter(sort_filter)
        sorted_lists = apply_list_sort(custom_lists, sort, sort_order)
        if sorted_lists is None:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.BAD_REQUEST,
            )

        paginated_data = paginate_data(request, sorted_lists, limit, offset)
        paginated_data["results"] = ListSerializer(
            paginated_data["results"],
            many=True,
            context={"include_items": False},
        ).data
        return Response(paginated_data, status=HTTP.OK)

    @extend_schema(
        operation_id="lists_post",
        summary="Create lists",
        request=ListCreateRequestSerializer,
        responses={
            201: OpenApiResponse(
                ListSerializer,
                description="Successfully created",
                examples=[
                    OpenApiExample(
                        "Created list example",
                        description="Created list example",
                        summary="Created list example",
                        value={
                            "id": 1,
                            "name": "Test1",
                            "description": "This is a test list",
                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                            "owner": {"id": 1, "username": "admin"},
                            "collaborators": [],
                            "items_count": 1,
                            "latest_update": "2026-03-23T21:17:32.705709Z",
                            "items": {
                                "pagination": {
                                    "total": 1,
                                    "limit": 20,
                                    "offset": 0,
                                    "next": None,
                                    "previous": None,
                                },
                                "results": [
                                    {
                                        "id": 2902,
                                        "consumption_id": 1,
                                        "item": {
                                            "media_id": "1",
                                            "source": "manual",
                                            "media_type": "comic",
                                            "title": "Comic 1",
                                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                                            "season_number": None,
                                            "episode_number": None,
                                        },
                                        "item_id": "comic/manual/1",
                                        "parent_id": None,
                                        "tracked": True,
                                        "created_at": "2026-03-23T21:16:16.978287Z",
                                        "score": None,
                                        "status": 3,
                                        "progress": 0,
                                        "progressed_at": "2026-03-23T21:16:16.965721Z",
                                        "start_date": None,
                                        "end_date": None,
                                        "notes": "",
                                        "lists": [{"list_id": 1, "list_item_id": 16}],
                                    },
                                ],
                            },
                        },
                    )
                ],
            ),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            500: InternalServerErrorResponse,
        },
    )
    def post(self, request):
        """Create a new custom list."""
        user = request.user

        serializer = ListCreateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        collaborators = resolve_collaborators(data.get("collaborators", []))

        # TODO: move to lists/models.py
        with transaction.atomic():
            custom_list = CustomList.objects.create(
                name=data["name"],
                description=data.get("description", ""),
                owner=user,
            )
            custom_list.collaborators.set(collaborators)

        serialized_data = ListSerializer(custom_list).data
        return Response(serialized_data, status=HTTP.CREATED)


# /api/v1/lists/[list_id]/
class ListDetailView(drf_views.APIView):
    """List detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ListSerializer

    @extend_schema(
        operation_id="list_detail_delete",
        summary="Delete a specific list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to delete.",
            ),
        ],
        responses={
            204: OpenApiResponse(description="List deleted successfully"),
            401: UnauthorizedResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def delete(self, request, list_id):
        """Delete a specific custom list."""
        user = request.user

        try:
            custom_list = CustomList.objects.get(id=list_id)
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not custom_list.user_can_delete(user):
            return Response(
                {
                    "detail": "You do not have permission to delete this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        custom_list.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="list_detail_get",
        summary="Get details of a specific list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search query to filter list items by title.",
            ),
            MediaSortParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                ListSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list example",
                        description="Retrieved list example",
                        summary="Retrieved list example",
                        value={
                            "id": 1,
                            "name": "Test1",
                            "description": "This is a test list",
                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                            "owner": {"id": 1, "username": "admin"},
                            "collaborators": [],
                            "items_count": 1,
                            "latest_update": "2026-03-23T21:17:32.705709Z",
                            "items": {
                                "pagination": {
                                    "total": 0,
                                    "limit": 20,
                                    "offset": 0,
                                    "next": None,
                                    "previous": None,
                                },
                                "results": [],
                            },
                        },
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
    def get(self, request, list_id):
        """Retrieve details and paginated items of a specific list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("collaborators", "items")
                .get(id=list_id)
            )
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not user_list.user_can_view(user):
            return Response(
                {
                    "detail": "You do not have permission to view this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        items = user_list.items.all()

        search_query = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        # TODO: move to lists/models.py
        if search_query:
            items = items.filter(title__icontains=search_query)

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        # Shows info about the last consumption of the media if it's tracked
        items = list(items)
        medias_by_item_id = get_latest_medias_by_item_id(user, items)
        media_objects = [medias_by_item_id.get(item.id, item) for item in items]

        if sort_filter:
            sort, sort_order = parse_sort_filter(sort_filter)
            try:
                media_objects = apply_aggregated_sort(media_objects, sort, sort_order)
            except InvalidSortError:
                return Response(
                    {"detail": "Invalid sorting"},
                    status=HTTP.BAD_REQUEST,
                )

        paginated_data = paginate_data(request, media_objects, limit, offset)
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_list = ListSerializer(
            user_list,
            context={
                "paginated_items": paginated_data,
                "lists_by_item_id": lists_by_item_id,
            },
        ).data

        return Response(serialized_list, status=HTTP.OK)

    @extend_schema(
        operation_id="list_detail_patch",
        summary="Update a specific list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve.",
            ),
        ],
        request=ListUpdateRequestSerializer,
        responses={
            200: OpenApiResponse(
                ListSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list example",
                        description="Retrieved list example",
                        summary="Retrieved list example",
                        value={
                            "id": 1,
                            "name": "Test1",
                            "description": "This is a test list",
                            "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                            "owner": {"id": 1, "username": "admin"},
                            "collaborators": [],
                            "items_count": 1,
                            "latest_update": "2026-03-23T21:17:32.705709Z",
                            "items": {
                                "pagination": {
                                    "total": 0,
                                    "limit": 20,
                                    "offset": 0,
                                    "next": None,
                                    "previous": None,
                                },
                                "results": [],
                            },
                        },
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
    def patch(self, request, list_id):
        """Update a specific custom list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            custom_list = CustomList.objects.get(id=list_id)
        except CustomList.DoesNotExist:
            return Response(
                {"detail": "List not found."},
                status=HTTP.NOT_FOUND,
            )

        if not custom_list.user_can_edit(user):
            return Response(
                {
                    "detail": "You do not have permission to edit this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        serializer = ListUpdateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        name = data.get("name")
        description = data.get("description")
        collaborator_ids = data.get("collaborators")

        collaborators = None
        if collaborator_ids is not None:
            if custom_list.owner != user:
                return Response(
                    {
                        "detail": "Only the list owner can change the collaborators.",
                    },
                    status=HTTP.FORBIDDEN,
                )
            collaborators = resolve_collaborators(collaborator_ids)

        if name is not None:
            custom_list.name = name.strip()
        if description is not None:
            custom_list.description = description

        with transaction.atomic():
            custom_list.save()
            if collaborators is not None:
                custom_list.collaborators.set(collaborators)

        serialized_data = ListSerializer(custom_list).data
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/lists/[list_id]/items/
class ListItemsView(drf_views.APIView):
    """List items view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="list_items_get",
        summary="Get items of a list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve items from.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search query to filter list items by title.",
            ),
            MediaSortParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedMediaSerializer,
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list items example",
                        description="Retrieved list items example",
                        summary="Retrieved list items example",
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
                                    "id": 2902,
                                    "consumption_id": 1,
                                    "item": {
                                        "media_id": "1",
                                        "source": "manual",
                                        "media_type": "comic",
                                        "title": "Comic 1",
                                        "image": "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg",
                                        "season_number": None,
                                        "episode_number": None,
                                    },
                                    "item_id": "comic/manual/1",
                                    "parent_id": None,
                                    "tracked": True,
                                    "created_at": "2026-03-23T21:16:16.978287Z",
                                    "score": None,
                                    "status": 3,
                                    "progress": 0,
                                    "progressed_at": "2026-03-23T21:16:16.965721Z",
                                    "start_date": None,
                                    "end_date": None,
                                    "notes": "",
                                    "lists": [{"list_id": 1, "list_item_id": 16}],
                                },
                            ],
                        },
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
    def get(self, request, list_id):
        """Get items of a list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
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

        if not user_list.user_can_view(user):
            return Response(
                {
                    "detail": "You do not have permission to view this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        items = user_list.items.all()

        search_query = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        # TODO: move to lists/models.py
        if search_query:
            items = items.filter(title__icontains=search_query)

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        # Shows info about the last consumption of the media if it's tracked
        items = list(items)
        medias_by_item_id = get_latest_medias_by_item_id(user, items)
        media_objects = [medias_by_item_id.get(item.id, item) for item in items]

        if sort_filter:
            sort, sort_order = parse_sort_filter(sort_filter)
            try:
                media_objects = apply_aggregated_sort(media_objects, sort, sort_order)
            except InvalidSortError:
                return Response(
                    {"detail": "Invalid sorting"},
                    status=HTTP.BAD_REQUEST,
                )

        paginated_data = paginate_data(request, media_objects, limit, offset)
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = MixedMediaSerializer(
            paginated_data["results"],
            many=True,
            context={
                "serialize_items_as_media": True,
                "lists_by_item_id": lists_by_item_id,
            },
        ).data
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/lists/[list_id]/items/[item_id]/
class ListItemView(drf_views.APIView):
    """List item detail view."""

    authentication_classes = [BearerAuthentication, APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="list_item_delete",
        summary="Delete item from list",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve items from.",
                required=True,
            ),
            OpenApiParameter(
                name="item_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of the item to delete",
                required=True,
            ),
        ],
        responses={
            204: OpenApiResponse(description="Item deleted successfully"),
            400: BadRequestResponse,
            401: UnauthorizedResponse,
            403: ForbiddenResponse,
            404: NotFoundResponse,
            500: InternalServerErrorResponse,
        },
    )
    def delete(self, request, list_id, item_id):
        """Delete an item from a list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
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
                {
                    "detail": "You do not have permission to edit this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item(item_id, include_item=True)
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Item not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        operation_id="list_item_get",
        summary="Get list item details",
        parameters=[
            OpenApiParameter(
                name="list_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="The ID of the list to retrieve items from.",
                required=True,
            ),
            OpenApiParameter(
                name="item_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of the item to retrieve details of.",
                required=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                PolymorphicProxySerializer(
                    component_name="CompleteListItem",
                    serializers=[CompleteMediaSerializer, CompleteEpisodeSerializer],
                    resource_type_field_name=None,
                ),
                description="Successful response",
                examples=[
                    OpenApiExample(
                        "Retrieved list item example",
                        description="Retrieved list item example",
                        summary="Retrieved list item example",
                        value={
                            "id": 313,
                            "media_id": "105248",
                            "source": "tmdb",
                            "source_url": "https://www.themoviedb.org/tv/105248/season/1",
                            "media_type": "season",
                            "title": "Cyberpunk: Edgerunners",
                            "max_progress": 10,
                            "image": "https://image.tmdb.org/t/p/w500/6MMjX9T0L7eoRfZFTnzC6WXYZLK.jpg",
                            "synopsis": "In a dystopia riddled with corruption and cybernetic implants, a talented but reckless street kid strives to become a mercenary outlaw — an edgerunner.",
                            "genres": [
                                "Animation",
                                "Action & Adventure",
                                "Drama",
                                "Sci-Fi & Fantasy",
                            ],
                            "score": 8.3,
                            "score_count": 688,
                            "details": {
                                "first_air_date": "2022-09-12",
                                "last_air_date": "2022-09-13",
                                "episodes": 10,
                                "runtime": "26m",
                                "total_runtime": "4h 25m",
                                "tvdb_id": 384541,
                            },
                            "related": {
                                "episodes": [
                                    {
                                        "id": None,
                                        "consumption_id": None,
                                        "item": {
                                            "media_id": "105248",
                                            "source": "tmdb",
                                            "media_type": "episode",
                                            "title": "Let You Down",
                                            "image": "https://image.tmdb.org/t/p/original/egBHU73t79tMg2qrqj3aJof1ibS.jpg",
                                            "season_number": 1,
                                            "episode_number": 1,
                                        },
                                        "item_id": "tv/tmdb/105248/1/1",
                                        "parent_id": "tv/tmdb/105248/1",
                                        "tracked": False,
                                        "created_at": None,
                                        "score": None,
                                        "status": None,
                                        "progress": None,
                                        "progressed_at": None,
                                        "start_date": None,
                                        "end_date": None,
                                        "notes": None,
                                        "lists": [],
                                    }
                                ]
                            },
                            "item_id": "tv/tmdb/105248/1",
                            "parent_id": "tv/tmdb/105248",
                            "tracked": True,
                            "consumptions_number": 1,
                            "consumptions": [
                                {
                                    "consumption_id": 3,
                                    "created": "2026-01-15T15:33:03.302349Z",
                                    "score": None,
                                    "progress": 1,
                                    "progressed_at": "2026-01-15T20:13:00Z",
                                    "status": 1,
                                    "start_date": "2025-09-17T09:40:00Z",
                                    "end_date": "2026-01-15T20:13:00Z",
                                    "notes": "",
                                }
                            ],
                            "lists": [{"list_id": 1, "list_item_id": 1}],
                        },
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
    def get(self, request, list_id, item_id):
        """Get details of a list item."""
        user = request.user

        try:
            # TODO: move to lists/models.py
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

        if not user_list.user_can_view(user):
            return Response(
                {
                    "detail": "You don't have permission to view this list.",
                },
                status=HTTP.FORBIDDEN,
            )

        try:
            list_item = user_list.get_list_item(item_id, include_item=True)
            item = list_item.item
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Item not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        view_class = MediaDetailView
        extra_kwargs = {"media_type": item.media_type}

        if item.media_type == MediaTypes.SEASON.value:
            view_class = MediaSeasonDetailView
            extra_kwargs = {
                "media_type": MediaTypes.TV.value,
                "season_number": item.season_number,
            }
        elif item.media_type == MediaTypes.EPISODE.value:
            view_class = MediaEpisodeDetailView
            extra_kwargs = {
                "media_type": MediaTypes.TV.value,
                "season_number": item.season_number,
                "episode_number": item.episode_number,
            }

        # Call the appropriate media detail class to avoid code duplication
        return view_class().get(
            request,
            source=item.source,
            media_id=item.media_id,
            **extra_kwargs,
        )
