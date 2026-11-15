import logging
from http import HTTPStatus as HTTP  # noqa: N814

from django import forms as django_forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError
from django.db.models import Exists, OuterRef, Subquery
from django.db.utils import OperationalError
from django.utils.timezone import datetime, localdate, make_aware

# FORK: the fork pins django-health-check 3.x (no async HealthCheckView);
# HealthView below is implemented against the 3.x CheckMixin plugin API.
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    PolymorphicProxySerializer,
    extend_schema,
)
from health_check.mixins import CheckMixin
from rest_framework import views as drf_views
from rest_framework.response import Response

from app import history_cache, metadata_utils
from app.activity_builders import (
    _get_game_lengths_refresh_lock,
    _queue_game_lengths_refresh,
    _should_queue_game_lengths_refresh,
)
from app.db_retry import run_retryable_db_operation
from app.forms import ManualItemForm, get_form_class
from app.media_list_filters import (
    MediaListFilterError,
    get_media_list_entries,
    get_next_episode_map,
    parse_media_list_filters,
)
from app.models import (
    BasicMedia,
    CollectionEntry,
    Episode,
    Item,
    MediaTypes,
    MoviePlay,
    Sources,
)
from app.providers import services, tmdb
from app.services import metadata_resolution
from app.services.metadata_sync import enrich_synced_item, sync_podcast_show_from_rss
from app.statistics import (
    get_activity_data,
    get_media_type_distribution,
    get_score_distribution,
    get_status_distribution,
    get_status_pie_chart_data,
    get_timeline,
    get_user_media,
)
from events import tasks
from events.models import Event
from integrations.oauth_scope_info import has_full_account_access
from integrations.state.ratings import rating_edit
from lists.forms import CustomListForm, validate_public_slug
from lists.models import CustomList, CustomListItem
from lists.views_helpers import (
    _build_media_type_breakdown,
    _get_list_completed_counts,
    _get_list_last_watched_dates,
)

from . import fork_helpers  # FORK: history sorting overlay
from .changes_history_processor import (
    delete_changes_history_entry,
    get_changes_history_entries,
    get_changes_history_entry,
)
from .consumption_create import (
    ConsumptionError,
    create_provider_consumption,
    normalize_body,
)
from .contract_serializers import (
    CompleteEpisodeResponseSerializer,
    CompleteGameResponseSerializer,
    CompleteMediaResponseSerializer,
    ConsumptionResponseSerializer,
    DetailErrorSerializer,
    ListItemsEnvelopeSerializer,
    MediaUpdateRequestSerializer,
    SearchEnvelopeSerializer,
    TrackedMediaEnvelopeSerializer,
    TrackedMediaResponseSerializer,
    TrackedMediaUpdateRequestSerializer,
    TrackMediaRequestSerializer,
)
from .errors import (
    database_busy_response,
    failure_response,
    validation_error_code,
)
from .helpers import (
    apply_image_url,
    apply_list_sort,
    build_game_lengths_summary,
    build_lists_by_item_id,
    build_tags_by_item_id,
    check_source_type,
    check_valid_type,
    filter_item_bucket,
    get_item_lists,
    get_media_type_availability,
    paginate_data,
    paginate_list_items,
    parse_limit_offset,
    parse_sort_filter,
    resolve_calendar_date_range,
    resolve_episode_coordinate_for_request,
    resolve_item_queryset,  # FORK: bucket-aware Item resolution
    try_parse_date,
    unsupported_fields,
    validate_body,
)
from .idempotency import idempotent_create
from .schema import (
    LIBRARY_MEDIA_TYPE_PARAM,
    MEDIA_LIST_FILTER_PARAMS,
    MEDIA_LIST_ROOT_PARAMS,
    MEDIA_TYPE_COMPLETE_PARAM,
    MEDIA_TYPE_PARAM,
)
from .serializers import (
    ChangesHistoryEntrySerializer,
    CompleteEpisodeSerializer,
    CompleteMediaSerializer,
    EpisodeSerializer,
    HealthResponseSerializer,
    HistorySerializer,
    InfoSerializer,
    MediaSerializer,
    TimelineItemSerializer,
    UntrackedMediaSerializer,
    serialize_data,
)

logger = logging.getLogger(__name__)


def _resolve_api_episode_coordinate(
    request,
    media_id,
    source,
    season_number,
    episode_number,
    *,
    library_media_type=None,
):
    """Resolve an episode route and translate provider failures to API errors."""
    try:
        return resolve_episode_coordinate_for_request(
            request.user,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=library_media_type,
            language=metadata_resolution.metadata_language_default(request.user),
        )
    except Exception:
        logger.exception("An error occurred while fetching media metadata.")
        return None, Response(
            {
                "detail": "An error occurred while fetching media metadata.",
            },
            status=HTTP.INTERNAL_SERVER_ERROR,
        )


# TODO!: check sorters and filters in paginate_data since data is not serialized yet. Maybe data should be serialized first and then sorted/paginated later?? Sorting/filtering should occur at db search level, pagination should be done right after, always at the db search level, then the data should be serialized.

# TODO!: for children items, it should return an error if user is trying to access a non existing season/episode (for example if it's requested the season 4 of a 2 season show)

# TODO: Implement search for already tracked media (item_id and tracked fields)

# TODO: Implement global search endpoint for every media_type

# TODO: Implement admin commands to manage users (add admins, remove/add users, etc)

# TODO: Move operations on db to `models` file of the relative django app

# TODO!!: since it's possible to add to lists untracked items, the id field can be null, so it's impossible to get these elements from the list, while it should be possible. The untracked added element is in the Items table, but not in the media tables. Add the list of lists an item is in to the model of the medias, so they can be retrieved and computed easily.

# TODO: look into django.core.paginator Paginator

# TODO: Review children endpoints performance and avoid repeated list lookups per item.


# /api/v1/calendar/
class CalendarView(drf_views.APIView):
    """Calendar view."""

    def get(self, request):
        """Retrieve calendar events for the authenticated user."""
        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")
        month_q = request.GET.get("month")
        year_q = request.GET.get("year")

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        try:
            first_day, last_day = resolve_calendar_date_range(
                start_date,
                end_date,
                month_q,
                year_q,
            )
        except (TypeError, ValueError):
            return Response(
                {"detail": "Invalid date format."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            releases = Event.objects.get_user_events(request.user, first_day, last_day)
        except Exception:
            logger.exception("Error occurred while fetching events.")
            return Response(
                {
                    "detail": "Error occurred while fetching events.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        paginated_data = paginate_data(request, releases, limit, offset)
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"request": request},
        )

        return Response(paginated_data)


# /api/v1/calendar/update/
class CalendarUpdateView(drf_views.APIView):
    """Update calendar view."""

    def post(self, request):
        """Trigger calendar events update for the authenticated user."""
        tasks.reload_calendar.delay(user_id=request.user.pk)
        return Response(
            {"detail": "Task queued"},
            status=HTTP.ACCEPTED,
        )


# /api/v1/changes_history/[media_type]/[history_id]
class MediaTypeChangesHistoryDetailView(drf_views.APIView):
    """Changes history record view."""

    @extend_schema(parameters=[MEDIA_TYPE_COMPLETE_PARAM])
    def get(self, request, media_type, history_id):
        """Retrieve the changes history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            record = get_changes_history_entry(media_type, history_id, request.user)
            serialized_data = serialize_data(
                record,
                context={"media_type": media_type},
                serializer_class=ChangesHistoryEntrySerializer,
            )
            return Response(serialized_data, status=HTTP.OK)
        except Exception:
            logger.exception("History record not found")
            return Response(
                {
                    "detail": "History record not found",
                },
                status=HTTP.NOT_FOUND,
            )

    @extend_schema(parameters=[MEDIA_TYPE_COMPLETE_PARAM])
    def delete(self, request, media_type, history_id):
        """Delete the changes history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            delete_changes_history_entry(media_type, history_id, request.user)
            return Response(
                {"detail": "Record removed correctly"},
                status=HTTP.NO_CONTENT,
            )
        except Exception:
            logger.exception("History record not found")
            return Response(
                {
                    "detail": "History record not found",
                },
                status=HTTP.NOT_FOUND,
            )


# /api/v1/health/
class HealthView(CheckMixin, drf_views.APIView):
    """Health check view."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        """Check API health status."""
        # FORK: django-health-check 3.x — run_check() populates each plugin's
        # errors and returns the critical-service error list.
        errors = self.run_check()
        health_data = {
            "plugins": dict(self.plugins),
            "errors": errors,
        }
        response_data = serialize_data(
            health_data,
            serializer_class=HealthResponseSerializer,
        )
        status_code = HTTP.INTERNAL_SERVER_ERROR if errors else HTTP.OK
        return Response(response_data, status=status_code)


# /api/v1/info/
class InfoView(drf_views.APIView):
    """Info endpoint."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        """Get application information."""
        info_data = {}
        response_data = serialize_data(
            info_data,
            serializer_class=InfoSerializer,
        )
        return Response(response_data, status=HTTP.OK)


def _sort_lists_by_last_watched(user, custom_lists, sort_order):
    """Return the lists ordered by their latest watch date (newest first by default)."""
    rows = list(custom_lists.values_list("id", "name"))
    last_watched = _get_list_last_watched_dates(user, [row[0] for row in rows])
    sign = 1 if sort_order == "asc" else -1
    rows.sort(
        key=lambda row: (
            row[0] not in last_watched,
            last_watched[row[0]].timestamp() * sign if row[0] in last_watched else 0,
            row[1].casefold(),
            row[0],
        ),
    )
    by_id = {custom_list.id: custom_list for custom_list in custom_lists}
    return [by_id[row[0]] for row in rows]


# /api/v1/lists/
class ListsView(drf_views.APIView):
    """Lists view."""

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

        # Lists holding at least one item of this media type, like the web
        # hub's "All Types" filter.
        media_type = request.GET.get("media_type", "")
        if media_type and media_type != "all":
            if media_type not in MediaTypes.values:
                return Response(
                    {"detail": "Invalid media type"},
                    status=HTTP.BAD_REQUEST,
                )
            custom_lists = custom_lists.filter(
                Exists(
                    CustomListItem.objects.filter(
                        custom_list_id=OuterRef("pk"),
                        item__media_type=media_type,
                    ),
                ),
            )

        sort, sort_order = parse_sort_filter(sort_filter)
        if sort == "watched":
            # The web's "Last Watched" sort: the newest watch date among a
            # list's items; lists never watched come last.
            sorted_lists = _sort_lists_by_last_watched(
                user,
                custom_lists,
                sort_order,
            )
            paginated_data = paginate_data(
                request,
                sorted_lists,
                limit,
                offset,
                total=len(sorted_lists),
                already_sliced=False,
            )
        else:
            sorted_lists = apply_list_sort(custom_lists, sort, sort_order)
            if sorted_lists is None:
                return Response(
                    {"detail": "Invalid sorting"},
                    status=HTTP.NOT_FOUND,
                )
            paginated_data = paginate_data(request, sorted_lists, limit, offset)
        completed_counts = _get_list_completed_counts(
            user,
            [custom_list.id for custom_list in paginated_data["results"]],
        )
        serialized_data = serialize_data(
            paginated_data["results"],
            many=True,
            context={"include_items": False, "completed_counts": completed_counts},
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)

    def post(self, request):
        """Create a new custom list for the authenticated user."""
        if getattr(request.auth, "writable_list_ids", None):
            return Response(
                {"detail": "This token may only write to its bound lists."},
                status=HTTP.FORBIDDEN,
            )
        user = request.user
        body = request.data

        if not body:
            return Response(
                {"detail": "Missing body."},
                status=HTTP.BAD_REQUEST,
            )

        name = body.get("name", "").strip()
        if not name:
            return Response(
                {"detail": "Field 'name' is required."},
                status=HTTP.BAD_REQUEST,
            )
        description = body.get("description", "")
        collaborator_ids = body.get("collaborators", [])
        viewer_ids = body.get("viewers", [])

        if collaborator_ids and not isinstance(collaborator_ids, list):
            return Response(
                {
                    "detail": "Field 'collaborators' must be an array of user IDs.",
                },
                status=HTTP.BAD_REQUEST,
            )
        if viewer_ids and not isinstance(viewer_ids, list):
            return Response(
                {
                    "detail": "Field 'viewers' must be an array of user IDs.",
                },
                status=HTTP.BAD_REQUEST,
            )

        is_public = body.get("is_public", False)
        if not isinstance(is_public, bool):
            return Response(
                {"detail": "Field 'is_public' must be a boolean."},
                status=HTTP.BAD_REQUEST,
            )
        visibility = body.get("visibility")
        if visibility is None:
            visibility = "public" if is_public else "private"
        if visibility not in dict(CustomList.VISIBILITY_CHOICES):
            return Response(
                {"detail": "Field 'visibility' must be public, unlisted or private."},
                status=HTTP.BAD_REQUEST,
            )
        allow_recommendations = body.get("allow_recommendations", False)
        if not isinstance(allow_recommendations, bool):
            return Response(
                {"detail": "Field 'allow_recommendations' must be a boolean."},
                status=HTTP.BAD_REQUEST,
            )
        try:
            public_slug = validate_public_slug(body.get("public_slug", ""))
        except django_forms.ValidationError as e:
            return Response({"detail": e.messages[0]}, status=HTTP.BAD_REQUEST)

        if visibility != "public":
            allow_recommendations = False
        # FORK: tags, notes and smart creation for native clients.
        tags = CustomListForm._normalize_tags(body.get("tags"))
        include_notes = bool(body.get("include_notes", False))
        if visibility not in ("public", "unlisted"):
            include_notes = False
        is_smart = bool(body.get("is_smart", False))
        smart_fields = {}
        if is_smart:
            from app.library_query.adapters import (
                SMART_RULES_CURRENT_SEMANTICS,
                SMART_RULES_SEMANTICS_KEY,
            )

            smart_fields = {
                "is_smart": True,
                "smart_filters": {
                    SMART_RULES_SEMANTICS_KEY: str(SMART_RULES_CURRENT_SEMANTICS),
                },
            }

        try:
            # TODO: move to lists/models.py
            custom_list = CustomList.objects.create(
                name=name,
                description=description,
                owner=user,
                visibility=visibility,
                public_slug=public_slug,
                allow_recommendations=allow_recommendations,
                tags=tags,
                include_notes=include_notes,
                **smart_fields,
            )

            if collaborator_ids:
                collaborators = get_user_model().objects.filter(id__in=collaborator_ids)

                if collaborators.count() != len(collaborator_ids):
                    custom_list.delete()
                    return Response(
                        {
                            "detail": "One or more collaborator IDs are invalid.",
                        },
                        status=HTTP.BAD_REQUEST,
                    )

                custom_list.collaborators.set(collaborators)

            if viewer_ids:
                viewers = get_user_model().objects.filter(id__in=viewer_ids)

                if viewers.count() != len(viewer_ids):
                    custom_list.delete()
                    return Response(
                        {
                            "detail": "One or more viewer IDs are invalid.",
                        },
                        status=HTTP.BAD_REQUEST,
                    )

                custom_list.viewers.set(viewers)

            serialized_data = serialize_data(
                custom_list,
                context={"include_items": False},
            )
            return Response(serialized_data, status=HTTP.CREATED)

        except Exception:
            logger.exception("An error occurred while creating the list.")
            return Response(
                {
                    "detail": "An error occurred while creating the list.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/lists/[list_id]/
class ListDetailView(drf_views.APIView):
    """List detail view."""

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

    def get(self, request, list_id):
        """Retrieve details and paginated items of a specific list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            user_list = (
                CustomList.objects.select_related("owner")
                .prefetch_related("collaborators", "viewers")
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

        media_user = user if user_list.user_can_edit(user) else user_list.owner
        paginated_data, err = paginate_list_items(request, media_user, user_list)
        if err:
            return err
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        item_ids = [getattr(entry, "item_id", None) or entry.pk for entry in paginated_data["results"]]
        list_added_by_item_id = dict(
            CustomListItem.objects.filter(custom_list=user_list, item_id__in=item_ids)
            .values_list("item_id", "date_added")
        )
        serialized_list = serialize_data(
            user_list,
            context={
                "paginated_items": paginated_data,
                "lists_by_item_id": lists_by_item_id,
                "list_added_by_item_id": list_added_by_item_id,
                "completed_counts": _get_list_completed_counts(media_user, [user_list.id]),
                "media_type_breakdown": _build_media_type_breakdown(user_list),
            },
        )

        return Response(serialized_list, status=HTTP.OK)

    def patch(self, request, list_id):
        """Update a specific custom list."""
        user = request.user
        body = request.data

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

        # External tokens never write smart lists (CanWriteBoundList), so they
        # may not create one by converting a manual list either.
        if (
            "is_smart" in body
            and hasattr(request.auth, "has_scope")
            and not has_full_account_access(request.auth)
            and bool(body.get("is_smart")) != custom_list.is_smart
        ):
            return Response(
                {"detail": "Only the account owner may change a list's type."},
                status=HTTP.FORBIDDEN,
            )

        name = body.get("name")
        description = body.get("description")
        collaborator_ids = body.get("collaborators")
        viewer_ids = body.get("viewers")
        is_public = body.get("is_public")
        visibility = body.get("visibility")
        allow_recommendations = body.get("allow_recommendations")

        if name is not None:
            custom_list.name = name.strip()
        if description is not None:
            custom_list.description = description
        if collaborator_ids is not None:
            if not isinstance(collaborator_ids, list):
                return Response(
                    {
                        "detail": "Field 'collaborators' must be an array of user IDs.",
                    },
                    status=HTTP.BAD_REQUEST,
                )
            collaborators = get_user_model().objects.filter(id__in=collaborator_ids)
            if collaborators.count() != len(collaborator_ids):
                return Response(
                    {
                        "detail": "One or more collaborator IDs are invalid.",
                    },
                    status=HTTP.BAD_REQUEST,
                )
            custom_list.collaborators.set(collaborators)
        if viewer_ids is not None:
            if not isinstance(viewer_ids, list):
                return Response(
                    {
                        "detail": "Field 'viewers' must be an array of user IDs.",
                    },
                    status=HTTP.BAD_REQUEST,
                )
            viewers = get_user_model().objects.filter(id__in=viewer_ids)
            if viewers.count() != len(viewer_ids):
                return Response(
                    {
                        "detail": "One or more viewer IDs are invalid.",
                    },
                    status=HTTP.BAD_REQUEST,
                )
            custom_list.viewers.set(viewers)
        if visibility is not None:
            if visibility not in dict(CustomList.VISIBILITY_CHOICES):
                return Response(
                    {
                        "detail": (
                            "Field 'visibility' must be public, unlisted or private."
                        ),
                    },
                    status=HTTP.BAD_REQUEST,
                )
            custom_list.visibility = visibility
        elif is_public is not None:
            if not isinstance(is_public, bool):
                return Response(
                    {"detail": "Field 'is_public' must be a boolean."},
                    status=HTTP.BAD_REQUEST,
                )
            custom_list.visibility = "public" if is_public else "private"
        if "tags" in body:
            custom_list.tags = CustomListForm._normalize_tags(
                body.get("tags"),
            )
        if "include_notes" in body:
            custom_list.include_notes = bool(body.get("include_notes"))
        if "is_smart" in body:
            # Mirrors CustomListForm.save: a list that turns smart is
            # evaluated with the current semantics; one that stops being smart
            # drops its rules (its items stay).
            make_smart = bool(body.get("is_smart"))
            if make_smart and not custom_list.is_smart:
                from app.library_query.adapters import (
                    SMART_RULES_CURRENT_SEMANTICS,
                    SMART_RULES_SEMANTICS_KEY,
                )

                custom_list.smart_filters = {
                    **(custom_list.smart_filters or {}),
                    SMART_RULES_SEMANTICS_KEY: str(SMART_RULES_CURRENT_SEMANTICS),
                }
            if not make_smart:
                custom_list.smart_media_types = []
                custom_list.smart_excluded_media_types = []
                custom_list.smart_filters = {}
            custom_list.is_smart = make_smart
        if "public_slug" in body:
            try:
                custom_list.public_slug = validate_public_slug(
                    body.get("public_slug", ""),
                    exclude_pk=custom_list.pk,
                )
            except django_forms.ValidationError as e:
                return Response({"detail": e.messages[0]}, status=HTTP.BAD_REQUEST)
        if allow_recommendations is not None:
            if not isinstance(allow_recommendations, bool):
                return Response(
                    {"detail": "Field 'allow_recommendations' must be a boolean."},
                    status=HTTP.BAD_REQUEST,
                )
            custom_list.allow_recommendations = allow_recommendations

        if custom_list.visibility != "public":
            custom_list.allow_recommendations = False
        if custom_list.visibility not in ("public", "unlisted"):
            custom_list.include_notes = False

        custom_list.save()
        serialized_data = serialize_data(
            custom_list,
            context={"request": request},
        )
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/lists/[list_id]/items/
class ListItemsView(drf_views.APIView):
    """List items view."""

    @extend_schema(responses={200: ListItemsEnvelopeSerializer})
    def get(self, request, list_id):
        """Get items of a list."""
        user = request.user

        try:
            # TODO: move to lists/models.py
            # No items prefetch: this view paginates the list at the database
            # layer, so hydrating every item up front is work it never reads.
            user_list = CustomList.objects.select_related("owner").get(id=list_id)
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

        media_user = user if user_list.user_can_edit(user) else user_list.owner
        paginated_data, err = paginate_list_items(request, media_user, user_list)
        if err:
            return err
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = serialize_data(
            paginated_data["results"],
            many=True,
            context={
                "serialize_items_as_media": True,
                "lists_by_item_id": lists_by_item_id,
            },
            homogeneous=False,
        )
        ranks = dict(
            CustomListItem.objects.filter(
                custom_list=user_list,
                item_id__in=[entry.get("id") for entry in serialized_data],
            ).values_list("item_id", "rank"),
        )
        for entry in serialized_data:
            entry["rank"] = ranks.get(entry.get("id"))
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/lists/[list_id]/items/[item_id]/
class ListItemView(drf_views.APIView):
    """List item detail view."""

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


def _rehydrate_deferred_items(page_entries):
    """Refetch full Item rows for one page of entries.

    `get_media_list`/`get_media_list_entries` defer several rarely-used Item
    fields (trakt/provider metadata) to keep the whole-library scan cheap.
    `ItemSerializer` (via `MediaSerializer`/`UntrackedMediaSerializer`) reads
    every Item field, so each deferred field on a paginated instance would
    otherwise trigger its own single-row query — a full-page-sized N+1. One
    bulk, undeferred fetch of just the page's items avoids that without
    touching the list-scan's defer optimization.
    """
    item_ids = {entry.item.id for entry in page_entries if entry.item is not None}
    if not item_ids:
        return
    fresh_items_by_id = {item.pk: item for item in Item.objects.filter(pk__in=item_ids)}
    for entry in page_entries:
        fresh_item = fresh_items_by_id.get(entry.item.id if entry.item else None)
        if fresh_item is None:
            continue
        entry.item = fresh_item
        if entry.media is not None:
            entry.media.item = fresh_item


# /api/v1/media/
def _media_list_response(request, media_type=None):
    """Build a filtered and serialized media-list response."""
    limit, offset, error_response = parse_limit_offset(request)
    if error_response:
        return error_response

    if media_type is not None and not check_valid_type(media_type, complete=True):
        return Response(
            {"detail": "Unsupported media type."},
            status=HTTP.BAD_REQUEST,
        )

    try:
        filters = parse_media_list_filters(request)
        entries, total = get_media_list_entries(
            request.user,
            media_type,
            filters,
            limit=limit,
            offset=offset,
        )
    except MediaListFilterError as error:
        return Response(
            {"detail": f"Invalid {error.parameter}: {error.message}"},
            status=HTTP.BAD_REQUEST,
        )

    paginated_data = paginate_data(
        request,
        entries,
        limit,
        offset,
        total=total,
        already_sliced=total is not None,
    )
    page_entries = paginated_data["results"]
    _rehydrate_deferred_items(page_entries)
    lists_by_item_id = build_lists_by_item_id(request.user, page_entries)
    next_episode_by_item_id = get_next_episode_map(page_entries)
    BasicMedia.objects.annotate_episode_progress(
        [entry.media for entry in page_entries if entry.media is not None],
        media_type,
    )
    serializer_context = {
        "request": request,
        "lists_by_item_id": lists_by_item_id,
        "next_episode_by_item_id": next_episode_by_item_id,
        "tags_by_item_id": build_tags_by_item_id(request.user, page_entries),
    }
    paginated_data["results"] = [
        (
            {**MediaSerializer(entry.media, context=serializer_context).data,
             **({"entry_id": entry.media.pk} if filters.separate_entries else {})}
            if entry.media is not None
            else UntrackedMediaSerializer(entry.item, context=serializer_context).data
        )
        for entry in page_entries
    ]
    return Response(paginated_data, status=HTTP.OK)


class MediaListView(drf_views.APIView):
    """List media with the shared web/API filter contract."""

    serializer_class = MediaSerializer

    @extend_schema(
        parameters=MEDIA_LIST_ROOT_PARAMS,
        operation_id="listTrackedMedia",
        responses={200: TrackedMediaEnvelopeSerializer},
    )
    def get(self, request):
        """Retrieve the filtered media list for the authenticated user."""
        return _media_list_response(request, request.query_params.get("media_type"))


# /api/v1/media/[media_type]/
class MediaTypeListView(drf_views.APIView):
    """List media by type with the shared web/API filter contract."""

    serializer_class = MediaSerializer

    @extend_schema(
        parameters=[MEDIA_TYPE_COMPLETE_PARAM, *MEDIA_LIST_FILTER_PARAMS],
        operation_id="listTrackedMediaByType",
        responses={200: TrackedMediaEnvelopeSerializer},
    )
    def get(self, request, media_type):
        """Retrieve the filtered media list of a specific media type."""
        return _media_list_response(request, media_type)

    @extend_schema(
        parameters=[
            MEDIA_TYPE_COMPLETE_PARAM,
            OpenApiParameter(
                name="Idempotency-Key",
                type=str,
                location=OpenApiParameter.HEADER,
                required=False,
                description=(
                    "Optional. Repeating the same key and body replays the "
                    "first success (`idempotent_replayed: true`) instead of "
                    "appending another play; the same key with a different "
                    "body returns 422 `IDEMPOTENCY_KEY_REUSED`. Records last "
                    "24 hours. Use after a timeout or 499, when the write may "
                    "have committed."
                ),
            ),
        ],
        operation_id="trackMedia",
        request=TrackMediaRequestSerializer,
        responses={
            201: TrackedMediaResponseSerializer,
            400: DetailErrorSerializer,
            403: DetailErrorSerializer,
            404: DetailErrorSerializer,
            409: DetailErrorSerializer,
            500: DetailErrorSerializer,
        },
    )
    @idempotent_create
    @rating_edit
    def post(self, request, media_type):
        """Create a new consumption for a media item.

        This append-oriented endpoint keeps its historical default: omitted
        status means Planning. Clients updating an existing play should first
        read its consumption_id and use the exact history route instead.

        Send an `Idempotency-Key` header to make a retry safe: repeating the
        same key and body replays the first success (header
        `Idempotent-Replayed: true` and `idempotent_replayed: true` in the body) instead of appending another play. The
        same key with a different body is refused with 422.

        `progress` set here (and returned by every media/history endpoint) is
        always this one consumption's own value, never a sum across a user's
        other entries for the same item -- the response's `progress_scope`
        field is explicitly "entry" for this reason. Its unit varies by
        media_type (e.g. minutes for games, episodes for tv/season/anime,
        plays for boardgame/music) and is named in the response's
        `progress_unit` field.
        """
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if media_type == MediaTypes.VIDEO.value:
            # No provider can look a video up, so a new one is created by its
            # first play instead.
            return Response(
                {
                    "detail": (
                        "Videos are created by posting a play to "
                        "/api/v1/videos/{source}/{media_id}/plays/."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        if not request.data:
            return Response(
                {"detail": "Missing body."},
                status=HTTP.BAD_REQUEST,
            )

        if request.FILES:
            return Response(
                {
                    "detail": (
                        "File uploads are not supported. "
                        "Set `image_url` to an image URL instead."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        # QueryDict (multipart/form-urlencoded) stores values as lists internally and
        # is immutable without file parts, so flatten to a plain mutable dict first.
        body = normalize_body(media_type, request.data)

        source = body.get("source", Sources.MANUAL.value)

        if source == Sources.MANUAL.value:
            form = ManualItemForm(body, user=request.user)
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

            media_data = dict(body)
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
            apply_image_url(item, media_form.cleaned_data.get("image_url"))
            BasicMedia.objects.annotate_episode_progress(
                [media_form.instance],
                media_type,
            )
            serialized_data = serialize_data(media_form.instance)
            return Response(serialized_data, status=HTTP.CREATED)

        if media_type == MediaTypes.EPISODE.value:
            return Response(
                {
                    "detail": (
                        "Provider episodes are recorded as plays "
                        "(POST /media/tv/{source}/{media_id}/{season_number}"
                        "/episodes/{episode_number}/watch/) or rated with "
                        "PATCH .../episodes/{episode_number}/score/."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            instance = create_provider_consumption(request.user, media_type, body)
        except ConsumptionError as error:
            return Response(error.payload, status=error.status)
        except IntegrityError:
            # api.middleware maps the tracked-media duplicate constraint to 409.
            raise
        except Exception as error:
            busy = database_busy_response(error, request=request)
            if busy is not None:
                return busy
            logger.exception("Internal Server Error.")
            return Response(
                {
                    "detail": "Internal Server Error.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )
        return Response(serialize_data(instance), status=HTTP.CREATED)


# /api/v1/media/[media_type]/[source]/[media_id]/
class MediaDetailView(drf_views.APIView):
    """Media view."""

    serializer_class = MediaSerializer

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def delete(self, request, media_type, source, media_id):
        """Delete a tracked media item and all its consumptions."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                source,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception("Internal Server Error.")
            return Response(
                {
                    "detail": "Internal Server Error.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        operation_id="retrieveMediaItem",
        responses={
            200: PolymorphicProxySerializer(
                component_name="CompleteMediaItemResponse",
                serializers=[
                    CompleteMediaResponseSerializer,
                    CompleteGameResponseSerializer,
                ],
                resource_type_field_name=None,
            ),
            400: DetailErrorSerializer,
            403: DetailErrorSerializer,
            404: DetailErrorSerializer,
            500: DetailErrorSerializer,
        },
    )
    def get(self, request, media_type, source, media_id):
        """Retrieve details of a specific media for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                media_id,
                source,
                language=metadata_resolution.metadata_language_default(request.user),
                user=user,
            )
        except services.ProviderAPIError as error:
            if error.status_code == HTTP.NOT_FOUND:
                return Response(
                    {"detail": "Media not found.", "code": "MEDIA_NOT_FOUND"},
                    status=HTTP.NOT_FOUND,
                )
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        library_media_type = request.query_params.get("library_media_type")
        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                media_type,
                source,
                library_media_type=library_media_type,
                annotate_progress=False,
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        BasicMedia.objects.annotate_episode_progress(user_medias, media_type)

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
                    library_media_type=library_media_type,
                    annotate_progress=False,
                ),
            )
            BasicMedia.objects.annotate_episode_progress(
                serie_seasons,
                MediaTypes.SEASON.value,
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

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            library_media_type=request.query_params.get("library_media_type"),
        )

        if media_type == MediaTypes.GAME.value:
            game_length_item = (
                user_medias[0].item
                if user_medias
                else resolve_item_queryset(
                    media_id,
                    source,
                    media_type,
                    library_media_type=library_media_type,
                ).first()
            )
            if game_length_item is None and source == Sources.IGDB.value:
                try:
                    game_length_item = Item.objects.create(
                        media_id=media_id,
                        source=source,
                        media_type=media_type,
                        image=media_metadata.get("image") or "",
                        **Item.title_fields_from_metadata(media_metadata),
                    )
                except Exception:
                    logger.warning(
                        "game_length_item_create_failed media_id=%s",
                        media_id,
                        exc_info=True,
                    )
                    game_length_item = None

            if game_length_item:
                queued = False
                if _should_queue_game_lengths_refresh(game_length_item):
                    queued = _queue_game_lengths_refresh(
                        game_length_item,
                        force=False,
                        fetch_hltb=True,
                    )

                payload = dict(game_length_item.provider_game_lengths or {})
                if payload:
                    state = "ready"
                elif queued or _get_game_lengths_refresh_lock(game_length_item):
                    state = "pending"
                else:
                    state = "unavailable"
                payload["state"] = state
                media_metadata["provider_game_lengths"] = payload

        # FORK: resolve the top-level Item so CompleteMediaSerializer can
        # expose imdb_rating/imdb_rating_count alongside the TMDB-based score.
        if media_type == MediaTypes.GAME.value:
            top_level_item = game_length_item
        else:
            top_level_item = (
                user_medias[0].item
                if user_medias
                else resolve_item_queryset(
                    media_id,
                    source,
                    media_type,
                    library_media_type=library_media_type,
                ).first()
            )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "seasons": seasons_by_number,
            "lists": lists,
            "item": top_level_item,
            "library_media_type": library_media_type,
            "media_type_status": get_media_type_availability(
                user,
                library_media_type or media_type,
            ),
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        operation_id="updateMediaItem",
        request=TrackedMediaUpdateRequestSerializer,
        responses={
            200: CompleteMediaResponseSerializer,
            400: DetailErrorSerializer,
            403: DetailErrorSerializer,
            404: DetailErrorSerializer,
            500: DetailErrorSerializer,
        },
    )
    @rating_edit
    def patch(self, request, media_type, source, media_id):
        """Update the convenience/default tracked row for a media item.

        Use the history endpoint when the caller needs to target one exact
        consumption entry.
        """
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        raw_body = request.data or {}
        body = raw_body.dict() if hasattr(raw_body, "dict") else dict(raw_body)
        # `image` lives on the shared Item, not the media row, so it bypasses
        # validate_body's per-media-type field filter. `image` is accepted as an
        # alias of the canonical `image_url`.
        image_alias = body.pop("image", None)
        image_url = body.pop("image_url", None)
        if image_url is None:
            image_url = image_alias
        if image_url is not None:
            try:
                URLValidator()(image_url)
            except ValidationError:
                return Response(
                    {"detail": "Invalid image_url."},
                    status=HTTP.BAD_REQUEST,
                )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        if body or image_url is None:
            validated_body, error = validate_body(body, media_type)

            if error:
                return Response(
                    {"detail": f"{error}", **validation_error_code(error)},
                    status=HTTP.BAD_REQUEST,
                )
        else:
            # image-only update: nothing to validate on the media row itself
            validated_body = {}

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception:
            logger.exception("Failed to update media.")
            return Response(
                {
                    "detail": "Failed to update media.",
                },
                status=HTTP.BAD_REQUEST,
            )

        apply_image_url(media.item, image_url)
        media.refresh_from_db()
        BasicMedia.objects.annotate_episode_progress(user_medias, media_type)

        try:
            media_metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except Exception:
            logger.exception("Internal Server Error.")
            return Response(
                {
                    "detail": "Internal Server Error.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            media_metadata["related"].pop("recommendations")

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            library_media_type=request.query_params.get("library_media_type"),
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "lists": lists,
            "item": media.item,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/changes_history/
class MediaChangesHistoryView(drf_views.APIView):
    """Media changes history view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def get(self, request, media_type, source, media_id):
        """Retrieve changes history timeline entries for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
            media_type,
            source,
            library_media_type=request.query_params.get("library_media_type"),
        )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, media_type)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"media_type": media_type},
            serializer_class=ChangesHistoryEntrySerializer,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/history/
class MediaConsumptionHistoryView(drf_views.APIView):
    """Media consumption history view."""

    serializer_class = HistorySerializer

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                source,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception("Internal Server Error.")
            return Response(
                {
                    "detail": "Internal Server Error.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        # FORK: movie rewatch support (issue #577) — once a movie has real
        # MoviePlay rows, history is served from those instead of the single
        # tracker row. Untouched movies keep today's single-entry behavior.
        movie_plays = fork_helpers.movie_plays_for_history(user_medias, media_type)
        history_rows = movie_plays if movie_plays is not None else list(user_medias)
        podcast_plays = fork_helpers.podcast_plays_for_history(user_medias, media_type)
        if podcast_plays is not None:
            history_rows = podcast_plays

        # FORK: was "TODO: missing sorting"
        history_rows, sort_err = fork_helpers.sort_history_results(
            request,
            history_rows,
        )
        if sort_err:
            return sort_err
        paginated_data = paginate_data(
            request,
            history_rows,
            limit,
            offset,
        )
        consumptions = serialize_data(
            paginated_data["results"],
            serializer_class=HistorySerializer,
            many=True,
        )
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/history/[consumption_id]/
class MediaConsumptionEntryDetailView(drf_views.APIView):
    """Media consumption history entry detail view."""

    serializer_class = HistorySerializer

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def delete(self, request, media_type, source, media_id, consumption_id):
        """Delete a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                source,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception("Internal Server Error.")
            return Response(
                {
                    "detail": "Internal Server Error.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # FORK: movie rewatch support (issue #577) — resolve the entry the same
        # way `.../history/` lists it, so a colliding Movie id cannot shadow a
        # MoviePlay id and delete the whole movie.
        consumption = fork_helpers.resolve_consumption_entry(
            user_medias,
            media_type,
            consumption_id,
        )
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        if isinstance(consumption, MoviePlay):
            consumption.movie.remove_play(consumption)
        else:
            consumption.delete()

        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def get(self, request, media_type, source, media_id, consumption_id):
        """Retrieve a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                source,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # FORK: movie rewatch support (issue #577) — resolve the entry the same
        # way `.../history/` lists it, so a colliding Movie id cannot shadow a
        # MoviePlay id.
        consumption = fork_helpers.resolve_consumption_entry(
            user_medias,
            media_type,
            consumption_id,
        )
        if not consumption:
            return Response(
                {
                    "detail": " Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        operation_id="updateMediaConsumption",
        request=MediaUpdateRequestSerializer,
        responses={
            200: ConsumptionResponseSerializer,
            400: DetailErrorSerializer,
            403: DetailErrorSerializer,
            404: DetailErrorSerializer,
            500: DetailErrorSerializer,
        },
    )
    @rating_edit
    def patch(self, request, media_type, source, media_id, consumption_id):
        """Update one exact consumption history entry for a media item."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                source,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # FORK: movie rewatch support (issue #577) — resolve the entry the same
        # way `.../history/` lists it, so a colliding Movie id cannot shadow a
        # MoviePlay id.
        consumption = fork_helpers.resolve_consumption_entry(
            user_medias,
            media_type,
            consumption_id,
        )
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, media_type)

        if error:
            return Response(
                {
                    "detail": HTTP.BAD_REQUEST.phrase,
                    "errors": str(error),
                    **validation_error_code(error),
                },
                status=HTTP.BAD_REQUEST,
            )

        unsupported = unsupported_fields(consumption, validated_body)
        if unsupported:
            return Response(
                {
                    "detail": HTTP.BAD_REQUEST.phrase,
                    "errors": "Fields not stored on this entry: "
                    + ", ".join(unsupported),
                    "code": "UNSUPPORTED_FIELDS",
                },
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception:
            logger.exception(HTTP.BAD_REQUEST.phrase)
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase},
                status=HTTP.BAD_REQUEST,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/
class MediaListsView(drf_views.APIView):
    """Media lists view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def get(self, request, media_type, source, media_id):
        """Retrieve the lists that a specific media is in."""
        user = request.user

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )
        # TODO: if media doesn't exist in the provider it should return 404
        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            library_media_type=request.query_params.get("library_media_type"),
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/[list_id]/
class MediaListDetailView(drf_views.APIView):
    """Media list detail view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM])
    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def delete(self, request, media_type, source, media_id, list_id):
        """Remove a specific media from a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )
        except CustomListItem.MultipleObjectsReturned:
            return Response(
                {
                    "detail": "The list holds this media in several library "
                    "buckets; pass library_media_type to choose one.",
                    "code": "AMBIGUOUS_LIBRARY_BUCKET",
                },
                status=HTTP.CONFLICT,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def put(self, request, media_type, source, media_id, list_id):
        """Add a specific media to a specific list."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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

        # FORK: bucket-aware, deterministic item resolution (get() can raise
        # MultipleObjectsReturned when grouped-anime bucket rows coexist). An
        # item Floppy has not seen yet is created from provider metadata.
        item, error = fork_helpers.get_or_create_provider_item(
            media_type,
            source,
            media_id,
            user=request.user,
            library_media_type=request.data.get("library_media_type")
            or request.query_params.get("library_media_type"),
        )
        if error:
            return error

        if user_list.items.filter(id=item.id).exists():
            return Response(
                {"detail": "Media already in the list."},
                status=HTTP.CONFLICT,
            )

        try:
            run_retryable_db_operation(
                lambda: user_list.items.add(item),
                operation_name="add item to list",
                operation_logger=logger,
            )
        except OperationalError as error:
            busy = database_busy_response(error, request=request, report=False)
            if busy is not None:
                return busy
            return Response(
                {"detail": "Database is busy. Please try again in a moment."},
                status=HTTP.SERVICE_UNAVAILABLE,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            library_media_type=request.query_params.get("library_media_type"),
        )

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/recommendations/
class MediaRecommendationsView(drf_views.APIView):
    """Media recommendations view."""

    serializer_class = MediaSerializer

    @extend_schema(parameters=[MEDIA_TYPE_PARAM])
    def get(self, request, media_type, source, media_id):
        """Retrieve recommendations for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                media_type,
                media_id,
                source,
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        recommendations = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            recommendations = media_metadata["related"]["recommendations"]

        return Response(recommendations, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/seasons/
class MediaSeasonsView(drf_views.APIView):
    """Media seasons view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
        user = request.user
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type) or media_type != MediaTypes.TV.value:
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
                media_type,
                media_id,
                source,
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        seasons = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "seasons" in media_metadata["related"]
        ):
            seasons = media_metadata["related"]["seasons"]

        paginated_data = paginate_data(request, seasons, limit, offset)
        lists_by_number = {}
        for season in paginated_data["results"]:
            season_number = season.get("season_number")
            if season_number is None:
                continue

            lists_by_number[season_number] = get_item_lists(
                user,
                media_id,
                source,
                MediaTypes.SEASON.value,
                season_number=season_number,
                library_media_type=request.query_params.get("library_media_type"),
            )

        season_numbers = [
            season.get("season_number")
            for season in paginated_data["results"]
            if season.get("season_number") is not None
        ]

        # FORK: scope to one library bucket so grouped-anime rows can't
        # nondeterministically overwrite the TV rows in this mapping.
        season_items_qs = Item.objects.filter(
            media_id=media_id,
            source=source,
            media_type=MediaTypes.SEASON.value,
            season_number__in=season_numbers,
        )
        season_bucket = request.query_params.get("library_media_type")
        if season_bucket:
            season_items_qs = season_items_qs.filter(
                library_media_type=season_bucket,
            )
        else:
            season_items_qs = season_items_qs.exclude(
                library_media_type=MediaTypes.ANIME.value,
            )
        items_by_number = {
            item.season_number: item for item in season_items_qs.order_by("id")
        }

        tracked_by_number = {}
        if season_numbers:
            tracked_seasons = BasicMedia.objects.get_serie_seasons(
                user,
                media_id,
                source,
                season_numbers=season_numbers,
                library_media_type=season_bucket,
                annotate_progress=False,
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
                        "updated_at": None,
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

        BasicMedia.objects.annotate_episode_progress(
            [entry for entry in season_media_entries if getattr(entry, "id", None)],
            MediaTypes.SEASON.value,
        )

        paginated_data["results"] = serialize_data(
            season_media_entries,
            many=True,
            context={
                "request": request,
            },
            serializer_class=MediaSerializer,
        )
        paginated_data["media_type_status"] = get_media_type_availability(
            user,
            season_bucket or media_type,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/sync/
class MediaSyncView(drf_views.APIView):
    """Sync media view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM])
    def post(self, request, media_type, source, media_id):  # FORK: was `_`
        """Trigger sync of metadata from provider (non-manual sources only)."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Unsupported media type."},
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
                    "detail": f"Cannot sync `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        if media_type == MediaTypes.PODCAST.value:
            # A podcast show's provider is its RSS feed, and it has no Item row
            # of its own (Items are per-episode), so it never reaches the
            # generic path below without minting a bogus one.
            synced_show = sync_podcast_show_from_rss(media_id, source)
            if synced_show is not None:
                return Response(
                    {"detail": "Metadata synced successfully."},
                    status=HTTP.ACCEPTED,
                )

        language = metadata_resolution.metadata_language_default(request.user)
        provider_cache_keys = metadata_utils.provider_metadata_cache_keys(
            source,
            media_type,
            media_id,
            language=language,
        )
        cache_key = provider_cache_keys[0]

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

        cache.delete_many(provider_cache_keys)

        try:
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                language=language,
            )

            # FORK: bucket-aware resolution + localized title fields, mirroring
            # app/metadata_sync_views.py. A raw update_or_create can clobber the
            # wrong grouped-anime bucket row and drops original/localized titles.
            library_media_type = request.data.get("library_media_type") or None
            item = resolve_item_queryset(
                media_id,
                source,
                media_type,
                library_media_type=library_media_type,
            ).first()
            item_fields = {
                **Item.title_fields_from_metadata(metadata),
                "image": metadata["image"],
            }
            if item is None:
                item = Item.objects.create(
                    media_id=media_id,
                    source=source,
                    media_type=media_type,
                    library_media_type=library_media_type or "",
                    **item_fields,
                )
            else:
                Item.objects.filter(pk=item.pk).update(**item_fields)
                item.refresh_from_db()

            sync_warnings, _preferred_provider_synced = enrich_synced_item(
                item,
                metadata,
                source=source,
                route_media_type=media_type,
                tracking_media_type=media_type,
                season_number=None,
                user=request.user,
            )
            for sync_warning in sync_warnings:
                logger.warning(
                    "metadata_sync_partial_failure item_id=%s warning=%s",
                    item.id,
                    sync_warning,
                )

            item.fetch_releases(delay=False)

            return Response(
                {"detail": "Metadata synced successfully."},
                status=HTTP.ACCEPTED,
            )

        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/
class MediaSeasonDetailView(drf_views.APIView):
    """Season view."""

    serializer_class = MediaSerializer

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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
                "season",
                source,
                season_number=season_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        operation_id="retrieveMediaSeason",
        responses={
            200: CompleteMediaResponseSerializer,
            400: DetailErrorSerializer,
            403: DetailErrorSerializer,
            404: DetailErrorSerializer,
            500: DetailErrorSerializer,
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
                "season",
                media_id,
                source,
                [season_number],
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not media_metadata:
            return Response(
                {"detail": "Season not found.", "code": "MEDIA_NOT_FOUND"},
                status=HTTP.NOT_FOUND,
            )

        library_media_type = request.query_params.get("library_media_type")
        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
                library_media_type=library_media_type,
                annotate_progress=False,
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        BasicMedia.objects.annotate_episode_progress(
            user_medias,
            MediaTypes.SEASON.value,
        )

        season_episodes = list(
            BasicMedia.objects.get_season_episodes(
                user,
                media_id,
                source,
                season_number=season_number,
                library_media_type=library_media_type,
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
            "season",
            season_number=season_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        # FORK: resolve the season's own Item so CompleteMediaSerializer can
        # expose imdb_rating/imdb_rating_count alongside the TMDB-based score.
        season_item = (
            user_medias[0].item
            if user_medias
            else resolve_item_queryset(
                media_id,
                source,
                MediaTypes.SEASON.value,
                season_number=season_number,
                library_media_type=library_media_type,
            ).first()
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "episodes": episodes_by_number,
            "lists": lists,
            "item": season_item,
            "library_media_type": library_media_type,
            "media_type_status": get_media_type_availability(
                user,
                library_media_type or media_type,
            ),
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {"detail": f"{error}", **validation_error_code(error)},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception:
            return Response(
                {"detail": "Failed to update season."},
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        BasicMedia.objects.annotate_episode_progress(
            user_medias,
            MediaTypes.SEASON.value,
        )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "season",
            season_number=season_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "lists": lists,
            "item": media.item,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/changes_history/
class MediaSeasonChangesHistoryView(drf_views.APIView):
    """Changes history season view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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
            "season",
            source,
            season_number=season_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, MediaTypes.SEASON.value)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"media_type": MediaTypes.SEASON.value},
            serializer_class=ChangesHistoryEntrySerializer,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/episodes/
class MediaSeasonEpisodesView(drf_views.APIView):
    """Season episodes view."""

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={
            400: DetailErrorSerializer,
            404: DetailErrorSerializer,
            500: DetailErrorSerializer,
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
                "season",
                media_id,
                source,
                [season_number],
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except services.ProviderAPIError as error:
            if error.status_code == HTTP.NOT_FOUND:
                return Response(
                    {"detail": "Season not found.", "code": "MEDIA_NOT_FOUND"},
                    status=HTTP.NOT_FOUND,
                )
            logger.exception("Failed to retrieve season episodes.")
            return Response(
                {
                    "detail": "Failed to retrieve season episodes.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )
        except Exception:
            logger.exception("Failed to retrieve season episodes.")
            return Response(
                {
                    "detail": "Failed to retrieve season episodes.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        episodes = []
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episodes = media_metadata["episodes"]

        paginated = paginate_data(request, episodes, limit, offset)

        # TODO: see if lists infos can be saved in the `episodes` object to avoid using `context` to pass additional parameters
        episode_numbers = [
            episode.get("episode_number")
            for episode in paginated["results"]
            if episode.get("episode_number") is not None
        ]

        lists_by_number = {}
        tracked_by_number = {}
        if episode_numbers:
            episode_items = filter_item_bucket(
                Item.objects.filter(
                    media_id=media_id,
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=season_number,
                    episode_number__in=episode_numbers,
                ),
                MediaTypes.EPISODE.value,
                library_media_type=request.query_params.get("library_media_type"),
            ).order_by("id")
            item_ids_by_number = {}
            for episode_number, item_id in episode_items.values_list(
                "episode_number",
                "id",
            ):
                item_ids_by_number.setdefault(episode_number, item_id)
            lists_by_item_id = CustomListItem.objects.get_user_item_lists_map(
                user,
                item_ids_by_number.values(),
            )
            lists_by_number = {
                episode_number: lists_by_item_id.get(item_id, [])
                for episode_number, item_id in item_ids_by_number.items()
            }

            tracked_episodes = BasicMedia.objects.get_season_episodes(
                user,
                media_id,
                source,
                season_number=season_number,
                episode_numbers=episode_numbers,
                library_media_type=request.query_params.get("library_media_type"),
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

        provider_rows = {
            episode.get("episode_number"): episode for episode in paginated["results"]
        }
        paginated["results"] = serialize_data(
            paginated["results"],
            many=True,
            context={
                "source": source,
                "tracked_episodes": tracked_by_number,
                "lists_by_number": lists_by_number,
            },
            serializer_class=EpisodeSerializer,
        )
        # FORK: the web season cards print the provider's overview, air date and
        # runtime, and whether the viewer owns a copy; the stored item has none.
        collected_numbers = set()
        ratings_by_number = {}
        if episode_numbers:
            owned = set(
                CollectionEntry.objects.filter(
                    user=user, item_id__in=item_ids_by_number.values(),
                ).values_list("item_id", flat=True),
            )
            collected_numbers = {
                number for number, item_id in item_ids_by_number.items() if item_id in owned
            }
            rating_rows = Episode.ratings.filter(
                related_season__user=user, item_id=OuterRef("pk"), rating_only=True,
            ).order_by("-scored_at", "-pk")
            ratings_by_number = {
                item.episode_number: item.preview_score
                for item in Item.objects.filter(pk__in=item_ids_by_number.values()).annotate(
                    preview_score=Subquery(rating_rows.values("score")[:1]),
                ).only("episode_number") if item.preview_score is not None
            }
        token = request.auth
        can_write = not hasattr(token, "has_scope") or token.has_scope("watchlist:write")
        can_list = not hasattr(token, "has_scope") or token.has_scope("lists:write")
        tracked_season = BasicMedia.objects.get_serie_seasons(
            user, media_id, source, season_numbers=[season_number],
            library_media_type=request.query_params.get("library_media_type"),
            annotate_progress=False,
        ).exists()
        for row in paginated["results"]:
            number = (row.get("item") or {}).get("episode_number")
            provider = provider_rows.get(number) or {}
            row["overview"] = provider.get("overview") or provider.get("synopsis") or ""
            row["air_date"] = provider.get("air_date") or ""
            row["runtime"] = provider.get("runtime")
            row["collected"] = number in collected_numbers
            tracked = tracked_by_number.get(number)
            score = getattr(tracked, "score", None)
            if score is None:
                score = ratings_by_number.get(number)
            row["score"] = float(score) if score is not None else None
            row["display_score"] = float(user.scale_score_for_display(score)) if user.ratings_enabled and score is not None else None
            row["scale_max"] = user.rating_scale_max
            row["can_rate"] = bool(can_write and user.ratings_enabled and (tracked_season or tracked or number in ratings_by_number))
            row["can_watch"] = can_write
            row["can_collect"] = can_write
            row["can_list"] = can_list and bool(row.get("item"))
        paginated["media_type_status"] = get_media_type_availability(
            user,
            request.query_params.get("library_media_type") or media_type,
        )
        return Response(paginated, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/
class MediaSeasonConsumptionHistoryView(drf_views.APIView):
    """Season consumption history view."""

    serializer_class = HistorySerializer

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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
                "season",
                source,
                season_number=season_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # FORK: was "TODO: missing sorting"
        user_medias, sort_err = fork_helpers.sort_history_results(
            request,
            list(user_medias),
        )
        if sort_err:
            return sort_err
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = serialize_data(
            paginated_data["results"],
            serializer_class=HistorySerializer,
            many=True,
        )
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/[consumption_id]/
class MediaSeasonConsumptionEntryDetailView(drf_views.APIView):
    """Season consumption history entry detail view."""

    serializer_class = HistorySerializer

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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
                "season",
                source,
                season_number=season_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception("Failed to retrieve consumption entry.")
            return Response(
                {
                    "detail": "Failed to retrieve consumption entry.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        consumption.delete()

        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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
                "season",
                source,
                season_number=season_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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
                "season",
                source,
                season_number=season_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {
                    "detail": HTTP.BAD_REQUEST.phrase,
                    "errors": str(error),
                    **validation_error_code(error),
                },
                status=HTTP.BAD_REQUEST,
            )

        unsupported = unsupported_fields(consumption, validated_body)
        if unsupported:
            return Response(
                {
                    "detail": HTTP.BAD_REQUEST.phrase,
                    "errors": "Fields not stored on this entry: "
                    + ", ".join(unsupported),
                    "code": "UNSUPPORTED_FIELDS",
                },
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception:
            logger.exception(HTTP.BAD_REQUEST.phrase)
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase},
                status=HTTP.BAD_REQUEST,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/lists/
class MediaSeasonListsView(drf_views.APIView):
    """Season lists view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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
            "season",
            season_number=season_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/lists/[list_id]/
class MediaSeasonListDetailView(drf_views.APIView):
    """Season list detail view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM])
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

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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

        # FORK: bucket-aware, deterministic item resolution (get() can raise
        # MultipleObjectsReturned when grouped-anime bucket rows coexist).
        item = resolve_item_queryset(
            media_id,
            source,
            "season",
            season_number=season_number,
            library_media_type=request.data.get("library_media_type")
            or request.query_params.get("library_media_type"),
        ).first()
        if item is None:
            return Response(
                {"detail": "Media not found.", "code": "MEDIA_NOT_FOUND"},
                status=HTTP.NOT_FOUND,
            )

        if user_list.items.filter(id=item.id).exists():
            return Response(
                {"detail": "Media already in the list."},
                status=HTTP.CONFLICT,
            )

        try:
            run_retryable_db_operation(
                lambda: user_list.items.add(item),
                operation_name="add item to list",
                operation_logger=logger,
            )
        except OperationalError as error:
            busy = database_busy_response(error, request=request, report=False)
            if busy is not None:
                return busy
            return Response(
                {"detail": "Database is busy. Please try again in a moment."},
                status=HTTP.SERVICE_UNAVAILABLE,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            season_number=season_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/sync/
class MediaSeasonSyncView(drf_views.APIView):
    """Sync season."""

    # FORK: request arg was `_` upstream
    @extend_schema(parameters=[MEDIA_TYPE_PARAM])
    def post(self, request, media_type, source, media_id, season_number):
        """Trigger sync of metadata from provider (non-manual sources only)."""
        # TODO: see if it can be simplified reducing the number of return statements
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

        language = metadata_resolution.metadata_language_default(request.user)
        provider_cache_keys = metadata_utils.provider_metadata_cache_keys(
            source,
            MediaTypes.SEASON.value,
            media_id,
            season_number=season_number,
            language=language,
        )
        cache_key = provider_cache_keys[0]

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

        cache.delete_many(provider_cache_keys)

        try:
            metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
                language=language,
            )

            # FORK: bucket-aware resolution + localized title fields, mirroring
            # app/metadata_sync_views.py (see the tv-level sync above).
            library_media_type = request.data.get("library_media_type") or None
            item = resolve_item_queryset(
                media_id,
                source,
                "season",
                season_number=season_number,
                library_media_type=library_media_type,
            ).first()
            item_fields = {
                **Item.title_fields_from_metadata(metadata),
                "image": metadata["image"],
            }
            if item is None:
                item = Item.objects.create(
                    media_id=media_id,
                    source=source,
                    media_type="season",
                    season_number=season_number,
                    library_media_type=library_media_type or "",
                    **item_fields,
                )
            else:
                Item.objects.filter(pk=item.pk).update(**item_fields)
                item.refresh_from_db()

            sync_warnings, _preferred_provider_synced = enrich_synced_item(
                item,
                metadata,
                source=source,
                route_media_type=MediaTypes.SEASON.value,
                tracking_media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                user=request.user,
            )
            for sync_warning in sync_warnings:
                logger.warning(
                    "metadata_sync_partial_failure item_id=%s warning=%s",
                    item.id,
                    sync_warning,
                )

            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )
            # FORK: episode Item rows live in a translated library bucket
            # (season bucket unless it is "season", else "episode" — see
            # app/models/tv.py) and store the EPISODE title, not the show
            # title. Upstream's loop would clobber every episode title with
            # the season title and could hit the wrong grouped-anime rows.
            episode_bucket = (
                item.library_media_type
                if item.library_media_type
                and item.library_media_type != MediaTypes.SEASON.value
                else MediaTypes.EPISODE.value
            )
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                    library_media_type=episode_bucket,
                ).order_by("id")
            }

            episodes_to_update = []
            episode_item_ids_with_title_changes = set()

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_title_fields = Item.title_fields_from_episode_metadata(
                        episode_data,
                    )
                    if episode_title_fields["title"]:
                        if any(
                            getattr(episode_item, field) != value
                            for field, value in episode_title_fields.items()
                        ):
                            episode_item_ids_with_title_changes.add(episode_item.pk)
                        for field, value in episode_title_fields.items():
                            setattr(episode_item, field, value)
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)

            if episodes_to_update:
                Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "original_title", "localized_title", "image"],
                    batch_size=100,
                )

            if episode_item_ids_with_title_changes:
                history_user_ids = (
                    Episode.objects.filter(
                        item_id__in=episode_item_ids_with_title_changes,
                    )
                    .values_list("related_season__user_id", flat=True)
                    .distinct()
                )
                for user_id in history_user_ids:
                    history_cache.invalidate_history_cache(user_id, force=True)

            item.fetch_releases(delay=False)

            return Response(
                {"detail": "Metadata synced successfully."},
                status=HTTP.ACCEPTED,
            )

        except Exception:
            logger.exception("An error occurred while syncing metadata.")
            return Response(
                {
                    "detail": "An error occurred while syncing metadata.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/
class MediaEpisodeDetailView(drf_views.APIView):
    """Episode view."""

    serializer_class = MediaSerializer

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={204: None, 404: DetailErrorSerializer},
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

        _, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception("An error occurred while fetching media.")
            return Response(
                {
                    "detail": "An error occurred while fetching media.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {
                    "detail": "Episode not found or not tracked.",
                    "code": "MEDIA_NOT_TRACKED",
                },
                status=HTTP.NOT_FOUND,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=HTTP.NO_CONTENT,
        )

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        operation_id="retrieveMediaEpisode",
        responses={
            200: CompleteEpisodeResponseSerializer,
            400: DetailErrorSerializer,
            403: DetailErrorSerializer,
            404: DetailErrorSerializer,
            500: DetailErrorSerializer,
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

        coordinate, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error
        media_metadata = dict(coordinate.season_metadata)
        episode = coordinate.episode

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
                annotate_progress=False,
            )
        except Exception:
            logger.exception("An error occurred while fetching user media.")
            return Response(
                {
                    "detail": "An error occurred while fetching user media.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        media_metadata.pop("episodes", None)

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        # FORK: resolve the episode's own Item so CompleteEpisodeSerializer
        # can expose imdb_rating/imdb_rating_count alongside the TMDB score.
        episode_item = (
            user_medias[0].item
            if user_medias
            else resolve_item_queryset(
                media_id,
                source,
                MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
            ).first()
        )

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
            "lists": lists,
            "item": episode_item,
            "media_type_status": get_media_type_availability(
                user,
                request.query_params.get("library_media_type") or media_type,
            ),
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=HTTP.OK)

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={404: DetailErrorSerializer},
    )
    @rating_edit
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

        _, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception("An error occurred while fetching user media.")
            return Response(
                {
                    "detail": "An error occurred while fetching user media.",
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {
                    "detail": "Episode not found or not tracked.",
                    "code": "MEDIA_NOT_TRACKED",
                },
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {
                    "detail": HTTP.BAD_REQUEST.phrase,
                    "errors": str(error),
                    **validation_error_code(error),
                },
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception:
            logger.exception(HTTP.BAD_REQUEST.phrase)
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase},
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
                language=metadata_resolution.metadata_language_default(request.user),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not media_metadata:
            return Response(
                {"detail": "Episode not found.", "code": "MEDIA_NOT_FOUND"},
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
                    {"detail": "Episode not found.", "code": "MEDIA_NOT_FOUND"},
                    status=HTTP.NOT_FOUND,
                )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
            "lists": lists,
            "item": media.item,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/changes_history/
class MediaEpisodeChangesHistoryView(drf_views.APIView):
    """Changes history episode view."""

    serializer_class = ChangesHistoryEntrySerializer

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={404: DetailErrorSerializer},
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

        _, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            "episode",
            source,
            season_number=season_number,
            episode_number=episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        if not user_medias:
            return Response(
                {"detail": "Episode not found or not tracked.", "code": "MEDIA_NOT_TRACKED"},
                status=HTTP.NOT_FOUND,
            )

        entries = get_changes_history_entries(user_medias, MediaTypes.EPISODE.value)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"request": request, "media_type": MediaTypes.EPISODE.value},
            serializer_class=ChangesHistoryEntrySerializer,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/
class MediaEpisodeConsumptionHistoryView(drf_views.APIView):
    """Episode consumption history view."""

    serializer_class = HistorySerializer

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={404: DetailErrorSerializer},
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

        _, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception:
            logger.exception(HTTP.INTERNAL_SERVER_ERROR.phrase)
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # FORK: was "TODO: missing sorting"
        user_medias, sort_err = fork_helpers.sort_history_results(
            request,
            list(user_medias),
        )
        if sort_err:
            return sort_err
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = serialize_data(
            paginated_data["results"],
            serializer_class=HistorySerializer,
            many=True,
        )
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/[consumption_id]/
class MediaEpisodeConsumptionEntryDetailView(drf_views.APIView):
    """Episode consumption history entry detail view."""

    serializer_class = HistorySerializer

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={404: DetailErrorSerializer},
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

        _, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception as e:
            return failure_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase,
                e,
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        consumption.delete()

        return Response(status=HTTP.NO_CONTENT)

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={404: DetailErrorSerializer},
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

        _, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception as e:
            return failure_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase,
                e,
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

    @extend_schema(
        parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM],
        responses={404: DetailErrorSerializer},
    )
    @rating_edit
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

        _, coordinate_error = _resolve_api_episode_coordinate(
            request,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )
        if coordinate_error:
            return coordinate_error

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
                library_media_type=request.query_params.get("library_media_type"),
            )
        except Exception as e:
            return failure_response(
                HTTP.INTERNAL_SERVER_ERROR.phrase,
                e,
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {
                    "detail": "Consumption entry not found.",
                    "code": "CONSUMPTION_NOT_FOUND",
                },
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {"detail": f"{error}", **validation_error_code(error)},
                status=HTTP.BAD_REQUEST,
            )

        unsupported = unsupported_fields(consumption, validated_body)
        if unsupported:
            return Response(
                {
                    "detail": HTTP.BAD_REQUEST.phrase,
                    "errors": "Fields not stored on this entry: "
                    + ", ".join(unsupported),
                    "code": "UNSUPPORTED_FIELDS",
                },
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception:
            logger.exception(HTTP.BAD_REQUEST.phrase)
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase},
                status=HTTP.BAD_REQUEST,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/
class MediaEpisodeListsView(drf_views.APIView):
    """Episode lists view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the lists that a specific season is in."""
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
            library_media_type=request.query_params.get("library_media_type"),
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/[list_id]/
class MediaEpisodeListDetailView(drf_views.APIView):
    """Episode list detail view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM])
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

    @extend_schema(parameters=[MEDIA_TYPE_PARAM, LIBRARY_MEDIA_TYPE_PARAM])
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

        # FORK: bucket-aware, deterministic item resolution (get() can raise
        # MultipleObjectsReturned when grouped-anime bucket rows coexist).
        item = resolve_item_queryset(
            media_id,
            source,
            "episode",
            season_number=season_number,
            episode_number=episode_number,
            library_media_type=request.data.get("library_media_type")
            or request.query_params.get("library_media_type"),
        ).first()
        if item is None:
            return Response(
                {"detail": "Media not found.", "code": "MEDIA_NOT_FOUND"},
                status=HTTP.NOT_FOUND,
            )

        if user_list.items.filter(id=item.id).exists():
            return Response(
                {"detail": "Media already in the list."},
                status=HTTP.CONFLICT,
            )

        try:
            run_retryable_db_operation(
                lambda: user_list.items.add(item),
                operation_name="add item to list",
                operation_logger=logger,
            )
        except OperationalError as error:
            busy = database_busy_response(error, request=request, report=False)
            if busy is not None:
                return busy
            return Response(
                {"detail": "Database is busy. Please try again in a moment."},
                status=HTTP.SERVICE_UNAVAILABLE,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            media_type,
            season_number=season_number,
            episode_number=episode_number,
            library_media_type=request.query_params.get("library_media_type"),
        )

        return Response(lists, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/sync/
class MediaEpisodeSyncView(drf_views.APIView):
    """Sync episode view."""

    @extend_schema(parameters=[MEDIA_TYPE_PARAM])
    def post(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
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


# /api/v1/search/[media_type]/
class SearchProviderView(drf_views.APIView):
    """Search view."""

    serializer_class = MediaSerializer

    @extend_schema(
        operation_id="searchMedia",
        parameters=[
            MEDIA_TYPE_PARAM,
            OpenApiParameter(
                name="search",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Search query passed to the provider.",
            ),
            OpenApiParameter(
                name="source",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Provider to search (e.g. `tmdb`, `igdb`). Defaults to "
                "the viewer's preferred provider for the media type (Settings > "
                "Metadata), falling back to the media type's default provider "
                "when that one is not configured.",
            ),
            OpenApiParameter(
                name="limit",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Maximum number of results to return (default: 20).",
            ),
            OpenApiParameter(
                name="offset",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Offset for pagination (default: 0).",
            ),
        ],
        responses={
            200: SearchEnvelopeSerializer,
            400: DetailErrorSerializer,
            403: DetailErrorSerializer,
            500: DetailErrorSerializer,
        },
        examples=[
            OpenApiExample(
                "Game search result (igdb)",
                description="Game results include `platforms` and `year` so a "
                "client can pick the right release without a follow-up detail "
                "fetch. If a title is ambiguous across platforms, all "
                "candidates are returned rather than a single guess.",
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
                            "media_id": "2256",
                            "source": "igdb",
                            "media_type": "game",
                            "title": "Super Mario Strikers",
                            "image": "https://images.igdb.com/igdb/image/upload/"
                            "t_original/example.jpg",
                            "year": 2005,
                            "platforms": ["Nintendo GameCube"],
                        },
                    ],
                },
                response_only=True,
                media_type="application/json",
            ),
        ],
    )
    def get(self, request, media_type):
        """Search for media using the specified provider."""
        search = request.GET.get("search", "")
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )
        if media_type in ("season", "episode"):
            # Since data of seasons and episodes (title, author, description,
            # etc.) is not saved in the db but retrieved every time, it's not
            # possible to search for them
            return Response(
                {
                    "detail": f"Search for {media_type} is not supported.",
                },
                status=HTTP.BAD_REQUEST,
            )

        # API clients don't know which providers the instance can serve, so an
        # omitted source follows the viewer's preferred provider for this media
        # type (Settings > Metadata) and falls back to a configured one, as the
        # web search does (#1856).
        source = request.GET.get("source")
        if not source:
            source = metadata_resolution.metadata_default_source(
                request.user,
                media_type,
            )

        results_accum = []
        page = 1
        last_response = None

        try:
            while True:
                last_response = services.search(
                    media_type,
                    search,
                    page,
                    source,
                    limit=limit,
                    offset=offset,
                    user=request.user,
                    language=metadata_resolution.metadata_language_default(
                        request.user
                    ),
                )
                if (
                    not isinstance(last_response, dict)
                    or "results" not in last_response
                ):
                    break
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

        except services.ProviderAPIError as e:
            logger.exception(
                "Provider search failed for media_type=%s source=%s search=%r",
                media_type,
                source,
                search,
            )
            return Response(
                {"detail": f"{e.provider_label} lookup failed."},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )
        except Exception:
            logger.exception(
                "Unexpected error during search for media_type=%s source=%s search=%r",
                media_type,
                source,
                search,
            )
            return Response(
                {"detail": HTTP.INTERNAL_SERVER_ERROR.phrase},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        total = (
            last_response.get("total_results")
            if isinstance(last_response, dict)
            else len(results_accum)
        )

        if media_type == MediaTypes.GAME.value:
            media_ids = [
                r.get("media_id")
                for r in results_accum
                if r.get("source") == Sources.IGDB.value
            ]
            lengths_by_media_id = {
                item.media_id: item.provider_game_lengths
                for item in Item.objects.filter(
                    media_type=MediaTypes.GAME.value,
                    source=Sources.IGDB.value,
                    media_id__in=media_ids,
                ).exclude(provider_game_lengths={})
            }
            for r in results_accum:
                summary = build_game_lengths_summary(
                    lengths_by_media_id.get(r.get("media_id")),
                )
                if summary:
                    r["provider_game_lengths_summary"] = summary

        resolved_total = total or len(results_accum)
        paginated_data = paginate_data(
            request,
            results_accum,
            limit,
            offset,
            total=resolved_total,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/statistics/
class StatisticsView(drf_views.APIView):
    """Statistics view."""

    def get(self, request):
        """Retrieve statistics for the authenticated user."""
        # TODO: Possibly don't use WebUI needed statistics but compute them for API
        timeformat = "%Y-%m-%d"
        today = localdate()
        one_year_ago = today.replace(year=today.year - 1).strftime(timeformat)
        today = today.strftime(timeformat)

        user = request.user
        start_date = request.GET.get("start_date", one_year_ago)
        end_date = request.GET.get("end_date", today)
        if not start_date:
            start_date = one_year_ago
        if not end_date:
            end_date = today

        if start_date == "all" and end_date == "all":
            start_date = None
            end_date = None
        else:
            try:
                start_date = try_parse_date(start_date)
                end_date = try_parse_date(end_date)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Invalid date format."},
                    status=HTTP.BAD_REQUEST,
                )

            if start_date and end_date:
                start_date = make_aware(
                    datetime.combine(start_date, datetime.min.time()),
                )
                end_date = make_aware(
                    datetime.combine(end_date, datetime.max.time()),
                )
        user_media, media_count = get_user_media(
            user,
            start_date,
            end_date,
        )
        media_type_distribution = get_media_type_distribution(
            media_count,
        )
        # FORK: the fork's get_score_distribution returns a third value
        # (per-media-type top rated); unpack it to keep the payload shape.
        score_distribution, top_rated, _top_rated_by_type = get_score_distribution(
            user_media,
        )
        status_distribution = get_status_distribution(user_media)
        status_pie_chart_data = get_status_pie_chart_data(
            status_distribution,
        )
        timeline = get_timeline(user_media)
        activity_data = get_activity_data(request.user, start_date, end_date)

        statistics = {
            "start_date": start_date,
            "end_date": end_date,
            "media_count": media_count,
            "activity_data": activity_data,
            "media_type_distribution": media_type_distribution,
            "score_distribution": score_distribution,
            "top_rated": serialize_data(top_rated, many=True),
            "status_distribution": status_distribution,
            "status_pie_chart_data": status_pie_chart_data,
            "timeline": {
                month: serialize_data(
                    items,
                    many=True,
                    context={"request": request},
                    serializer_class=TimelineItemSerializer,
                )
                for month, items in (timeline or {}).items()
            },
        }

        return Response(statistics, status=HTTP.OK)
