from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.examples import (
    CAST_EXAMPLE,
    CHARACTERS_EXAMPLE,
    COMPANIES_EXAMPLE,
    CREATORS_EXAMPLE,
    CREW_EXAMPLE,
)
from api.helpers import (
    CAST_CREW_SOURCES,
    COMPANY_SOURCES,
    GAME_COMPANY_ROLES,
    VOICE_ACTOR_LANGUAGES,
    check_supported_source,
    fetch_cast,
    fetch_companies,
    fetch_crew,
    get_paginated_credits,
    paginate_data,
    parse_limit_offset,
)
from api.schemas import (
    BadRequestResponse,
    CompanyRoleParam,
    CrewDepartmentParam,
    MediaIdParam,
    MediaTypeParam,
    NotFoundResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    SourceCompleteParam,
    VoiceActorLanguageParam,
)
from api.serializers import (
    PaginatedCastSerializer,
    PaginatedCompaniesSerializer,
    PaginatedCreditsSerializer,
    PaginatedCrewSerializer,
)
from app.models import MediaTypes


# /api/v1/media/[media_type]/[source]/[media_id]/cast/
class MediaCastView(drf_views.APIView):
    """Media cast view."""

    serializer_class = PaginatedCastSerializer

    @extend_schema(
        operation_id="media_cast_get",
        summary="Get media cast",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            VoiceActorLanguageParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedCastSerializer,
                description="Successful response",
                examples=[CAST_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the cast of a specific movie, tv serie or anime."""
        limit, offset = parse_limit_offset(request)

        check_supported_source(CAST_CREW_SOURCES, media_type, source, "cast")

        language = request.GET.get("language", "").lower() or None
        if language and (
            media_type != MediaTypes.ANIME.value
            or language not in VOICE_ACTOR_LANGUAGES
        ):
            return Response(
                {
                    "detail": (
                        "language is only supported for anime and must be one of: "
                        f"{', '.join(VOICE_ACTOR_LANGUAGES)}."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        cast = fetch_cast(media_type, source, media_id, language=language)

        paginated = paginate_data(request, cast, limit, offset)
        return Response(PaginatedCastSerializer(paginated).data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/crew/
class MediaCrewView(drf_views.APIView):
    """Media crew view."""

    serializer_class = PaginatedCrewSerializer

    @extend_schema(
        operation_id="media_crew_get",
        summary="Get media crew",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            CrewDepartmentParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedCrewSerializer,
                description="Successful response",
                examples=[CREW_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the crew of a specific movie, tv serie or anime."""
        limit, offset = parse_limit_offset(request)

        check_supported_source(CAST_CREW_SOURCES, media_type, source, "crew")

        crew = fetch_crew(media_type, source, media_id)

        department = request.GET.get("department")
        if department:
            crew = [
                member
                for member in crew
                if (member["department"] or "").lower() == department.lower()
            ]

        paginated = paginate_data(request, crew, limit, offset)
        return Response(PaginatedCrewSerializer(paginated).data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/creators/
class MediaCreatorsView(drf_views.APIView):
    """Media creators view."""

    serializer_class = PaginatedCreditsSerializer

    @extend_schema(
        operation_id="media_creators_get",
        summary="Get media creators",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedCreditsSerializer,
                description="Successful response",
                examples=[CREATORS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the creators of a manga, book, comic or board game."""
        paginated = get_paginated_credits(
            request,
            media_type,
            source,
            media_id,
            "creators",
        )

        return Response(PaginatedCreditsSerializer(paginated).data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/characters/
class MediaCharactersView(drf_views.APIView):
    """Media characters view."""

    serializer_class = PaginatedCreditsSerializer

    @extend_schema(
        operation_id="media_characters_get",
        summary="Get media characters",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedCreditsSerializer,
                description="Successful response",
                examples=[CHARACTERS_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the characters of an anime, manga or comic."""
        paginated = get_paginated_credits(
            request,
            media_type,
            source,
            media_id,
            "characters",
        )

        return Response(PaginatedCreditsSerializer(paginated).data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/companies/
class MediaCompaniesView(drf_views.APIView):
    """Media companies view."""

    serializer_class = PaginatedCompaniesSerializer

    @extend_schema(
        operation_id="media_companies_get",
        summary="Get media companies",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            CompanyRoleParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedCompaniesSerializer,
                description="Successful response",
                examples=[COMPANIES_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the companies involved in a game or board game."""
        limit, offset = parse_limit_offset(request)

        check_supported_source(
            COMPANY_SOURCES,
            media_type,
            source,
            "companies",
        )

        role = request.GET.get("role")
        if role and role not in GAME_COMPANY_ROLES:
            return Response(
                {
                    "detail": (
                        "role must be 'developer', 'publisher', 'porting' or "
                        "'supporting'."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        companies = fetch_companies(source, media_id)

        if role:
            companies = [company for company in companies if role in company["roles"]]

        paginated = paginate_data(request, companies, limit, offset)
        return Response(PaginatedCompaniesSerializer(paginated).data, status=HTTP.OK)
