from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.examples import COVER_EXAMPLE, IMAGES_EXAMPLE
from api.helpers import (
    IMAGE_SOURCES,
    IMAGE_TYPES,
    check_supported_source,
    check_valid_type,
    fetch_images,
    fetch_season_images,
    filter_images,
    resolve_cover,
)
from api.schemas import (
    BadRequestResponse,
    MediaIdParam,
    MediaImageTypeParam,
    MediaTypeParam,
    NotFoundResponse,
    SeasonNumberParam,
    SourceCompleteParam,
)
from api.serializers import (
    CoverSerializer,
    CoverUpdateSerializer,
    MediaImagesResponseSerializer,
)
from app.models import MediaTypes, Sources


# /api/v1/media/[media_type]/[source]/[media_id]/images/
class MediaImagesView(drf_views.APIView):
    """Media images view."""

    serializer_class = MediaImagesResponseSerializer

    @extend_schema(
        operation_id="media_images_get",
        summary="Get media images",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            MediaImageTypeParam,
        ],
        responses={
            200: OpenApiResponse(
                MediaImagesResponseSerializer,
                description="Successful response",
                examples=[IMAGES_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve the images of a media."""
        check_supported_source(IMAGE_SOURCES, media_type, source, "images")

        type_filter = request.GET.get("type")
        if type_filter and type_filter not in IMAGE_TYPES:
            return Response(
                {
                    "detail": (
                        "type must be 'posters', 'backdrops', 'screenshots' or 'logos'."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        images = fetch_images(media_type, source, media_id)

        images = filter_images(images, type_filter)
        return Response(MediaImagesResponseSerializer(images).data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/images/
class MediaSeasonImagesView(drf_views.APIView):
    """Season images view."""

    serializer_class = MediaImagesResponseSerializer

    @extend_schema(
        operation_id="season_images_get",
        summary="Get season images",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
            MediaImageTypeParam,
        ],
        responses={
            200: OpenApiResponse(
                MediaImagesResponseSerializer,
                description="Successful response",
                examples=[IMAGES_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the posters of a specific season of a tv serie."""
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

        if source != Sources.TMDB.value:
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        type_filter = request.GET.get("type")
        if type_filter and type_filter not in IMAGE_TYPES:
            return Response(
                {
                    "detail": (
                        "type must be 'posters', 'backdrops', 'screenshots' or 'logos'."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        images = fetch_season_images(media_id, season_number)

        images = filter_images(images, type_filter)
        return Response(MediaImagesResponseSerializer(images).data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/cover/
class MediaCoverView(drf_views.APIView):
    """Media cover view."""

    serializer_class = CoverSerializer

    @extend_schema(
        operation_id="media_cover_get",
        summary="Get the current cover of a media",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
        ],
        responses={
            200: OpenApiResponse(
                CoverSerializer,
                description="Successful response",
                examples=[COVER_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Get the current cover of a media."""
        cover = resolve_cover(request, media_type, source, media_id)

        return Response(CoverSerializer(cover).data, status=HTTP.OK)

    @extend_schema(
        operation_id="media_cover_put",
        summary="Set a custom cover for a media",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
        ],
        request=CoverUpdateSerializer,
        responses={
            200: OpenApiResponse(
                CoverSerializer,
                description="Successful response",
                examples=[COVER_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def put(self, request, media_type, source, media_id):
        """Set a custom cover for a media."""
        cover = resolve_cover(request, media_type, source, media_id)

        return Response(CoverSerializer(cover).data, status=HTTP.OK)

    @extend_schema(
        operation_id="media_cover_delete",
        summary="Reset the cover of a media",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
        ],
        responses={
            200: OpenApiResponse(
                CoverSerializer,
                description="Successful response",
                examples=[COVER_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def delete(self, request, media_type, source, media_id):
        """Reset the cover of a media to the provider image."""
        cover = resolve_cover(request, media_type, source, media_id)

        return Response(CoverSerializer(cover).data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/cover/
class MediaSeasonCoverView(drf_views.APIView):
    """Season cover view."""

    serializer_class = CoverSerializer

    @extend_schema(
        operation_id="season_cover_get",
        summary="Get the current cover of a season",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
        ],
        responses={
            200: OpenApiResponse(
                CoverSerializer,
                description="Successful response",
                examples=[COVER_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Get the current cover of a season."""
        cover = resolve_cover(
            request,
            media_type,
            source,
            media_id,
            season_number=season_number,
        )

        return Response(CoverSerializer(cover).data, status=HTTP.OK)

    @extend_schema(
        operation_id="season_cover_put",
        summary="Set a custom cover for a season",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
        ],
        request=CoverUpdateSerializer,
        responses={
            200: OpenApiResponse(
                CoverSerializer,
                description="Successful response",
                examples=[COVER_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def put(self, request, media_type, source, media_id, season_number):
        """Set a custom cover for a season."""
        cover = resolve_cover(
            request,
            media_type,
            source,
            media_id,
            season_number=season_number,
        )

        return Response(CoverSerializer(cover).data, status=HTTP.OK)

    @extend_schema(
        operation_id="season_cover_delete",
        summary="Reset the cover of a season",
        parameters=[
            MediaTypeParam,
            SourceCompleteParam,
            MediaIdParam,
            SeasonNumberParam,
        ],
        responses={
            200: OpenApiResponse(
                CoverSerializer,
                description="Successful response",
                examples=[COVER_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def delete(self, request, media_type, source, media_id, season_number):
        """Reset the cover of a season to the provider image."""
        cover = resolve_cover(
            request,
            media_type,
            source,
            media_id,
            season_number=season_number,
        )

        return Response(CoverSerializer(cover).data, status=HTTP.OK)
