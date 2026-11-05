from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.examples import (
    KNOWN_FOR_EXAMPLE,
    PERSON_EXAMPLE,
    PERSON_IMAGES_EXAMPLE,
    SEARCH_PEOPLE_EXAMPLE,
)
from api.helpers import (
    error_response,
    filter_known_for,
    paginate_data,
    parse_limit_offset,
)
from api.schemas import (
    BadRequestResponse,
    KnownForDepartmentParam,
    KnownForMediaTypeParam,
    NotFoundResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    PersonIdParam,
    PersonSourceParam,
)
from api.serializers import (
    PaginatedKnownForSerializer,
    PaginatedPersonImagesSerializer,
    PersonSerializer,
    SearchPeopleResponseSerializer,
)
from app.models import MediaTypes, Sources
from app.providers import tmdb


# /api/v1/people/search/
class PeopleSearchView(drf_views.APIView):
    """People search view."""

    serializer_class = SearchPeopleResponseSerializer

    @extend_schema(
        operation_id="people_search_get",
        summary="Search for people",
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
                required=True,
            ),
            OpenApiParameter(
                name="source",
                type=OpenApiTypes.STR,
                enum=["tmdb"],
                location=OpenApiParameter.QUERY,
                description="Source of person data (default: tmdb).",
            ),
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                SearchPeopleResponseSerializer,
                description="Successful response",
                examples=[SEARCH_PEOPLE_EXAMPLE],
            ),
            400: BadRequestResponse,
        },
    )
    def get(self, request):
        """Search for people using the specified provider."""
        search = request.GET.get("search", "").strip()
        source = request.GET.get("source") or Sources.TMDB.value
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if source != Sources.TMDB.value:
            return Response(
                {"detail": f"Cannot query `{source}` for people"},
                status=HTTP.BAD_REQUEST,
            )

        if not search:
            return Response(
                {"detail": "search parameter is required."},
                status=HTTP.BAD_REQUEST,
            )

        results_accum = []
        page = 1
        last_response = None

        try:
            while True:
                last_response = tmdb.search_people(search, page)
                page_results = last_response.get("results", []) or []
                results_accum.extend(page_results)
                if len(results_accum) >= offset + limit:
                    break
                total_pages = last_response.get("total_pages")
                if total_pages and page >= total_pages:
                    break
                if not page_results:
                    break

                page += 1

        except Exception:  # noqa: BLE001
            return Response(
                {"detail": HTTP.INTERNAL_SERVER_ERROR.phrase},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        total = last_response.get("total_results") or len(results_accum)
        paginated_data = paginate_data(
            request,
            results_accum,
            limit,
            offset,
            total=total,
        )
        return Response(
            SearchPeopleResponseSerializer(paginated_data).data,
            status=HTTP.OK,
        )


# /api/v1/people/[source]/[person_id]/
class PersonDetailView(drf_views.APIView):
    """Person detail view."""

    serializer_class = PersonSerializer

    @extend_schema(
        operation_id="person_detail_get",
        summary="Get person",
        parameters=[
            PersonSourceParam,
            PersonIdParam,
        ],
        responses={
            200: OpenApiResponse(
                PersonSerializer,
                description="Successful response",
                examples=[PERSON_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, _, source, person_id):
        """Retrieve the details of a specific person."""
        if source != Sources.TMDB.value:
            return Response(
                {"detail": f"Cannot query `{source}` for people"},
                status=HTTP.BAD_REQUEST,
            )

        try:
            person = tmdb.person(person_id)
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        return Response(PersonSerializer(person).data, status=HTTP.OK)


# /api/v1/people/[source]/[person_id]/images/
class PersonImagesView(drf_views.APIView):
    """Person images view."""

    serializer_class = PaginatedPersonImagesSerializer

    @extend_schema(
        operation_id="person_images_get",
        summary="Get person images",
        parameters=[
            PersonSourceParam,
            PersonIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedPersonImagesSerializer,
                description="Successful response",
                examples=[PERSON_IMAGES_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, source, person_id):
        """Retrieve the profile images of a specific person."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if source != Sources.TMDB.value:
            return Response(
                {"detail": f"Cannot query `{source}` for people"},
                status=HTTP.BAD_REQUEST,
            )

        try:
            images = tmdb.person_images(person_id)
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        paginated = paginate_data(request, images, limit, offset)
        return Response(
            PaginatedPersonImagesSerializer(paginated).data,
            status=HTTP.OK,
        )


# /api/v1/people/[source]/[person_id]/known_for/
class PersonKnownForView(drf_views.APIView):
    """Person known for view."""

    serializer_class = PaginatedKnownForSerializer

    @extend_schema(
        operation_id="person_known_for_get",
        summary="Get person known for",
        parameters=[
            PersonSourceParam,
            PersonIdParam,
            KnownForMediaTypeParam,
            KnownForDepartmentParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedKnownForSerializer,
                description="Successful response",
                examples=[KNOWN_FOR_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, source, person_id):
        """Retrieve the movies and tv series a person is known for as cast or crew."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if source != Sources.TMDB.value:
            return Response(
                {"detail": f"Cannot query `{source}` for people"},
                status=HTTP.BAD_REQUEST,
            )

        media_type = request.GET.get("media_type")
        if media_type and media_type not in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
        ):
            return Response(
                {"detail": "media_type must be 'movie' or 'tv'."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            known_for = tmdb.person_known_for(person_id)
        except Exception as e:  # noqa: BLE001
            return error_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase, HTTP.INTERNAL_SERVER_ERROR, e
            )

        known_for = filter_known_for(
            known_for,
            media_type=media_type,
            department=request.GET.get("department"),
        )

        paginated = paginate_data(request, known_for, limit, offset)
        return Response(PaginatedKnownForSerializer(paginated).data, status=HTTP.OK)
