import asyncio
from http import HTTPStatus as HTTP  # noqa: N814

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.utils.timezone import datetime, localdate, make_aware, now
from drf_spectacular.utils import OpenApiResponse, extend_schema
from health_check.views import HealthCheckView
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from app.episode_operations import EpisodeNotFoundError, watch_episode
from app.forms import ManualItemForm, get_form_class
from app.models import BasicMedia, Item, MediaTypes, Sources
from app.providers import services, tmdb
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
from lists.models import CustomList, CustomListItem
from users.models import MediaStatusChoices

from .changes_history_processor import (
    delete_changes_history_entry,
    get_changes_history_entries,
    get_changes_history_entry,
)
from .helpers import (
    MEDIA_TYPE_COMPLETE_MODEL_MAP,
    apply_aggregated_sort,
    apply_list_sort,
    build_lists_by_item_id,
    check_source_type,
    check_valid_type,
    fetch_results_all_types,
    fetch_results_for_type,
    get_item_lists,
    get_media_status,
    get_sorts,
    paginate_data,
    parse_excluded_items,
    parse_limit_offset,
    parse_sort_filter,
    parse_status_param,
    resolve_calendar_date_range,
    try_parse_date,
    validate_body,
)
from .serializers import (
    ChangesHistoryEntrySerializer,
    CompleteEpisodeSerializer,
    CompleteMediaSerializer,
    EpisodeSerializer,
    EpisodeWatchSerializer,
    HealthResponseSerializer,
    HistorySerializer,
    InfoSerializer,
    MediaSerializer,
    TimelineItemSerializer,
    serialize_data,
)

# TODO!: check sorters and filters in paginate_data since data is not serialized yet. Maybe data should be serialized first and then sorted/paginated later?? Sorting/filtering should occur at db search level, pagination should be done right after, always at the db search level, then the data should be serialized.  # noqa: E501, W505

# TODO!: for children items, it should return an error if user is trying to access a non existing season/episode (for example if it's requested the season 4 of a 2 season show)  # noqa: E501, W505

# TODO: Implement search for already tracked media (item_id and tracked fields)  # noqa: E501, FIX002, W505

# TODO: Implement global search endpoint for every media_type  # noqa: E501, FIX002, W505

# TODO: Implement admin commands to manage users (add admins, remove/add users, etc)  # noqa: E501, FIX002, W505

# TODO: Move operations on db to `models` file of the relative django app  # noqa: E501, FIX002, W505

# TODO!!: since it's possible to add to lists untracked items, the id field can be null, so it's impossible to get these elements from the list, while it should be possible. The untracked added element is in the Items table, but not in the media tables. Add the list of lists an item is in to the model of the medias, so they can be retrieved and computed easily.  # noqa: E501, FIX002, W505

# TODO: look into django.core.paginator Paginator  # noqa: FIX002

# TODO: Review children endpoints performance and avoid repeated list lookups per item.  # noqa: E501, FIX002, W505


# /api/v1/calendar/
class CalendarView(drf_views.APIView):
    """Calendar view."""

    permission_classes = [permissions.IsAuthenticated]

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
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Error occurred while fetching events.",
                    "errors": str(e),
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

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """Trigger calendar events update for the authenticated user."""
        tasks.reload_calendar.delay(request.user)
        return Response(
            {"detail": "Task queued"},
            status=HTTP.ACCEPTED,
        )


# /api/v1/changes_history/[media_type]/[history_id]
class MediaTypeChangesHistoryDetailView(drf_views.APIView):
    """Changes history record view."""

    permission_classes = [permissions.IsAuthenticated]

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
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "History record not found",
                    "errors": str(e),
                },
                status=HTTP.NOT_FOUND,
            )

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
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "History record not found",
                    "errors": str(e),
                },
                status=HTTP.NOT_FOUND,
            )


# /api/v1/health/
class HealthView(drf_views.APIView):
    """Health check view."""

    authentication_classes = []
    permission_classes = []

    checks = HealthCheckView.checks

    def get_checks(self):
        """Return instantiated health checks using the installed library."""
        helper_view = HealthCheckView()
        helper_view.checks = self.checks
        return list(helper_view.get_checks())

    async def _collect_health_results(self):
        """Run all health checks and return their results."""
        return await asyncio.gather(
            *(check.get_result() for check in self.get_checks())
        )

    def get(self, request):  # noqa: ARG002
        """Check API health status."""
        # TODO: speed up data collection, right now request takes ~2s
        results = asyncio.run(self._collect_health_results())
        errors = [result.error for result in results if result.error]
        plugins = {}
        for result in results:
            plugin = result.check
            plugin.errors = [result.error] if result.error else []
            plugins[repr(plugin)] = plugin
        health_data = {
            "plugins": plugins,
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

    def get(self, request):  # noqa: ARG002
        """Get application information."""
        info_data = {}
        response_data = serialize_data(
            info_data,
            serializer_class=InfoSerializer,
        )
        return Response(response_data, status=HTTP.OK)


# /api/v1/lists/
class ListsView(drf_views.APIView):
    """Lists view."""

    permission_classes = [permissions.IsAuthenticated]

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
                status=HTTP.NOT_FOUND,
            )

        paginated_data = paginate_data(request, sorted_lists, limit, offset)
        serialized_data = serialize_data(
            paginated_data["results"],
            many=True,
            context={"include_items": False},
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)

    def post(self, request):
        """Create a new custom list for the authenticated user."""
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

        if collaborator_ids and not isinstance(collaborator_ids, list):
            return Response(
                {
                    "detail": "Field 'collaborators' must be an array of user IDs.",
                },
                status=HTTP.BAD_REQUEST,
            )

        try:
            # TODO: move to lists/models.py
            custom_list = CustomList.objects.create(
                name=name,
                description=description,
                owner=user,
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

            serialized_data = serialize_data(
                custom_list,
                context={"include_items": False},
            )
            return Response(serialized_data, status=HTTP.CREATED)

        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "An error occurred while creating the list.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/lists/[list_id]/
class ListDetailView(drf_views.APIView):
    """List detail view."""

    permission_classes = [permissions.IsAuthenticated]

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

        media_objects = []
        for item in items:
            # Shows info about the last consumption of the media if it's tracked
            media = BasicMedia.objects.filter_media_prefetch(
                user,
                item.media_id,
                item.media_type,
                item.source,
                season_number=item.season_number,
                episode_number=item.episode_number,
            ).first()

            media_objects.append(media if media is not None else item)

        if sort_filter:
            sort, sort_order = parse_sort_filter(sort_filter)
            if sort not in get_sorts(None, sort_type="all"):
                return Response(
                    {"detail": "Invalid sorting"},
                    status=HTTP.NOT_FOUND,
                )
            media_objects = apply_aggregated_sort(media_objects, sort)
            if isinstance(media_objects, Response):
                return media_objects
            if sort_order == "desc":
                media_objects.reverse()

        paginated_data = paginate_data(request, media_objects, limit, offset)
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_list = serialize_data(
            user_list,
            context={
                "paginated_items": paginated_data,
                "lists_by_item_id": lists_by_item_id,
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

        name = body.get("name")
        description = body.get("description")
        collaborator_ids = body.get("collaborators")

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

        custom_list.save()
        serialized_data = serialize_data(
            custom_list,
            context={"request": request},
        )
        return Response(serialized_data, status=HTTP.OK)


# /api/v1/lists/[list_id]/items/
class ListItemsView(drf_views.APIView):
    """List items view."""

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

        media_objects = []
        for item in items:
            # Shows info about the last consumption of the media if it's tracked
            media = BasicMedia.objects.filter_media_prefetch(
                user,
                item.media_id,
                item.media_type,
                item.source,
                season_number=item.season_number,
                episode_number=item.episode_number,
            ).first()

            media_objects.append(media if media is not None else item)

        if sort_filter:
            sort, sort_order = parse_sort_filter(sort_filter)
            if sort not in get_sorts(None, sort_type="all"):
                return Response(
                    {"detail": "Invalid sorting"},
                    status=HTTP.NOT_FOUND,
                )
            media_objects = apply_aggregated_sort(media_objects, sort)
            if isinstance(media_objects, Response):
                return media_objects
            if sort_order == "desc":
                media_objects.reverse()

        paginated_data = paginate_data(request, media_objects, limit, offset)
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


# /api/v1/media/
class MediaListView(drf_views.APIView):
    """List media view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Retrieve the list of media for the authenticated user."""
        # TODO: check progress sort might not be working
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
                status=HTTP.NOT_FOUND,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        if media_type:
            if not check_valid_type(media_type, complete=True):
                return Response(
                    {"detail": "Unsupported media type."},
                    status=HTTP.BAD_REQUEST,
                )
            results, has_error = fetch_results_for_type(
                user,
                media_type,
                status,
                sort,
                search,
            )
        else:
            # Exclude EPISODES and SEASONS from results by default
            # to declutter the results
            # TODO: Add an option to return those too? (seasons=true&episodes=false)
            results, has_error = fetch_results_all_types(
                user,
                status,
                sort,
                search,
                exclude,
            )

        if has_error:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.NOT_FOUND,
            )

        if isinstance(results, Response):
            return results

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset)
        # TODO: see if this can be optimized with a single query for all medias instead of one per episode  # noqa: E501, W505
        # TODO: see if lists infos can be saved in the `results` object to avoid using `context` to pass additional parameters  # noqa: E501, W505
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = serialize_data(
            paginated_data["results"],
            context={
                "request": request,
                "lists_by_item_id": lists_by_item_id,
            },
            many=True,
            homogeneous=False,
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/
class MediaTypeListView(drf_views.APIView):
    """List media by type view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

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
                status=HTTP.NOT_FOUND,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )
        results, has_error = fetch_results_for_type(
            user,
            media_type,
            status,
            sort,
            search,
        )

        if has_error:
            return Response(
                {"detail": "Invalid sorting"},
                status=HTTP.NOT_FOUND,
            )

        if isinstance(results, Response):
            return results

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset)
        # TODO: see if this can be optimized with a single query for all medias instead of one per episode  # noqa: E501, W505
        # TODO: see if lists infos can be saved in the `results` object to avoid using `context` to pass additional parameters  # noqa: E501, W505
        lists_by_item_id = build_lists_by_item_id(user, paginated_data["results"])
        serialized_data = serialize_data(
            paginated_data["results"],
            context={
                "request": request,
                "lists_by_item_id": lists_by_item_id,
            },
            many=True,
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=HTTP.OK)

    def post(self, request, media_type):
        """Track a new media item of a specific media type."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        if not request.data:
            return Response(
                {"detail": "Missing body."},
                status=HTTP.BAD_REQUEST,
            )

        body = request.data
        body["media_type"] = media_type
        body["status"] = (
            get_media_status(body["status"], reverse=True)
            if "status" in body
            # default status when tracking a new media will be "planning"
            else MediaStatusChoices.PLANNING
        )

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
            serialized_data = serialize_data(media_form.instance)
            return Response(serialized_data, status=HTTP.CREATED)

        media_id = body.get("media_id")
        if not media_id:
            return Response(
                {
                    "detail": "'media_id' is required for provider sources.",
                },
                status=HTTP.BAD_REQUEST,
            )

        season_number = body.get("season_number")

        try:
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        model = MEDIA_TYPE_COMPLETE_MODEL_MAP.get(media_type)
        if model is None:
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )

        instance = model(item=item, user=request.user)

        media_data = dict(body)
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

        media_form.save()
        serialized_data = serialize_data(media_form.instance)
        return Response(serialized_data, status=HTTP.CREATED)


# /api/v1/media/[media_type]/[source]/[media_id]/
class MediaDetailView(drf_views.APIView):
    """Media view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)

    def patch(self, request, media_type, source, media_id):
        """Update a tracked media item."""
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

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, media_type)

        if error:
            return Response(
                {"detail": f"{error}"},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Failed to update media.",
                    "errors": str(e),
                },
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

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

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/changes_history/
class MediaChangesHistoryView(drf_views.APIView):
    """Media changes history view."""

    permission_classes = [permissions.IsAuthenticated]

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
    permission_classes = [permissions.IsAuthenticated]

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Media not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        # TODO: missing sorting
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


# /api/v1/media/[media_type]/[source]/[media_id]/history/[consumption_id]/
class MediaConsumptionEntryDetailView(drf_views.APIView):
    """Media consumption history entry detail view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Internal Server Error.",
                    "errors": str(e),
                },
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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": " Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

    def patch(self, request, media_type, source, media_id, consumption_id):
        """Update a specific consumption history entry for a specific media."""
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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, media_type)

        if error:
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(error)},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
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
        lists = get_item_lists(user, media_id, source, media_type)
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/[list_id]/
class MediaListDetailView(drf_views.APIView):
    """Media list detail view."""

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
            )
        except CustomListItem.DoesNotExist:
            return Response(
                {"detail": "Media not found in the list."},
                status=HTTP.NOT_FOUND,
            )

        list_item.delete()
        return Response(status=HTTP.NO_CONTENT)

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

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, _, media_type, source, media_id):
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
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
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

    permission_classes = [permissions.IsAuthenticated]

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
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
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
            )

        season_numbers = [
            season.get("season_number")
            for season in paginated_data["results"]
            if season.get("season_number") is not None
        ]

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

        paginated_data["results"] = serialize_data(
            season_media_entries,
            many=True,
            context={
                "request": request,
            },
            serializer_class=MediaSerializer,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/sync/
class MediaSyncView(drf_views.APIView):
    """Sync media view."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, _, media_type, source, media_id):
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
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/
class MediaSeasonDetailView(drf_views.APIView):
    """Season view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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
            "season",
            season_number=season_number,
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "episodes": episodes_by_number,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {"detail": "Season not found or not tracked."},
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {"detail": f"{error}"},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception:  # noqa: BLE001
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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        lists = get_item_lists(
            user,
            media_id,
            source,
            "season",
            season_number=season_number,
        )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
            "lists": lists,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/changes_history/
class MediaSeasonChangesHistoryView(drf_views.APIView):
    """Changes history season view."""

    permission_classes = [permissions.IsAuthenticated]

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

    permission_classes = [permissions.IsAuthenticated]

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Failed to retrieve season episodes.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        episodes = []
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episodes = media_metadata["episodes"]

        paginated = paginate_data(request, episodes, limit, offset)

        # TODO: see if this can be optimized with a single query for all episodes instead of one per episode  # noqa: E501, W505
        # TODO: see if lists infos can be saved in the `episodes` object to avoid using `context` to pass additional parameters  # noqa: E501, W505
        lists_by_number = {}
        for episode in paginated["results"]:
            episode_number = episode.get("episode_number")
            if episode_number is None:
                continue
            lists_by_number[episode_number] = get_item_lists(
                user,
                media_id,
                source,
                "episode",
                season_number=season_number,
                episode_number=episode_number,
            )

        episode_numbers = [
            episode.get("episode_number")
            for episode in paginated["results"]
            if episode.get("episode_number") is not None
        ]

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
        return Response(paginated, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/
class MediaSeasonConsumptionHistoryView(drf_views.APIView):
    """Season consumption history view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # TODO: missing sorting
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


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/[consumption_id]/  # noqa: E501, W505
class MediaSeasonConsumptionEntryDetailView(drf_views.APIView):
    """Season consumption history entry detail view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": "Failed to retrieve consumption entry.",
                    "errors": str(e),
                },
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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

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
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": "Consumption entry not found."},
                status=HTTP.NOT_FOUND,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(error)},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
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
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/lists/[list_id]/
class MediaSeasonListDetailView(drf_views.APIView):
    """Season list detail view."""

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
                media_type="season",
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

    permission_classes = [permissions.IsAuthenticated]

    def post(self, _, media_type, source, media_id, season_number):
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
                "season",
                media_id,
                source,
                [season_number],
            )

            item, _ = Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type="season",
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
            return Response(
                {
                    "detail": "An error occurred while syncing metadata.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/
class MediaEpisodeDetailView(drf_views.APIView):
    """Episode view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

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
            return Response(
                {
                    "detail": "An error occurred while fetching media.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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
            return Response(
                {
                    "detail": "An error occurred while fetching media metadata.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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
            return Response(
                {
                    "detail": "An error occurred while fetching user media.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=HTTP.OK)

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

        body = request.data or {}

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
            return Response(
                {
                    "detail": "An error occurred while fetching user media.",
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        if not user_medias:
            return Response(
                {
                    "detail": "Episode not found or not tracked.",
                },
                status=HTTP.NOT_FOUND,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(error)},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
                status=HTTP.BAD_REQUEST,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
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

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/changes_history/  # noqa: E501, W505
class MediaEpisodeChangesHistoryView(drf_views.APIView):
    """Changes history episode view."""

    permission_classes = [permissions.IsAuthenticated]

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
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"request": request, "media_type": MediaTypes.EPISODE.value},
            serializer_class=ChangesHistoryEntrySerializer,
        )
        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/  # noqa: E501, W505
class MediaEpisodeConsumptionHistoryView(drf_views.APIView):
    """Episode consumption history view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _validate_post_path(media_type, source):
        """Validate media type and source for episode creation."""
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

        return None

    @extend_schema(
        request=EpisodeWatchSerializer,
        responses={
            HTTP.CREATED: HistorySerializer,
            HTTP.BAD_REQUEST: OpenApiResponse(description="Validation error."),
            HTTP.FORBIDDEN: OpenApiResponse(description="Authentication required."),
            HTTP.NOT_FOUND: OpenApiResponse(description="Episode not found."),
            HTTP.INTERNAL_SERVER_ERROR: OpenApiResponse(
                description="Provider or server error.",
            ),
        },
        description=(
            "Create a new consumption for the requested episode. Every successful "
            "request creates another consumption, so repeating the same request "
            "records a rewatch rather than updating an existing record. This "
            "operation is not idempotent; clients should deduplicate retries if "
            "they do not intend to record another viewing. The request body is "
            "optional and end_date defaults to the current timezone-aware time."
        ),
        summary="Create an episode watch history record",
    )
    def post(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
    ):
        """Create a new consumption for a specific episode of a TV series."""
        path_error = self._validate_post_path(media_type, source)
        if path_error is not None:
            return path_error

        request_serializer = EpisodeWatchSerializer(data=request.data or {})
        if not request_serializer.is_valid():
            return Response(
                {
                    "detail": HTTP.BAD_REQUEST.phrase,
                    "errors": request_serializer.errors,
                },
                status=HTTP.BAD_REQUEST,
            )

        end_date = request_serializer.validated_data.get(
            "end_date",
            now().replace(second=0, microsecond=0),
        )

        try:
            episode = watch_episode(
                user=request.user,
                media_id=media_id,
                source=source,
                season_number=season_number,
                episode_number=episode_number,
                end_date=end_date,
            )
        except EpisodeNotFoundError:
            return Response(
                {"detail": "Episode not found."},
                status=HTTP.NOT_FOUND,
            )
        except services.ProviderAPIError as error:
            if error.status_code == HTTP.NOT_FOUND:
                return Response(
                    {"detail": "Episode not found."},
                    status=HTTP.NOT_FOUND,
                )
            return Response(
                {
                    "detail": "Failed to create episode consumption.",
                    "errors": str(error),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        serialized = serialize_data(
            episode,
            serializer_class=HistorySerializer,
        )
        return Response(serialized, status=HTTP.CREATED)

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
            return Response(
                {
                    "detail": HTTP.INTERNAL_SERVER_ERROR.phrase,
                    "errors": str(e),
                },
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        # TODO: missing sorting
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


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/[consumption_id]/  # noqa: E501, W505
class MediaEpisodeConsumptionEntryDetailView(drf_views.APIView):
    """Episode consumption history entry detail view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

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

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=HTTP.OK)

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

        body = request.data or {}

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {"detail": f"{error}"},
                status=HTTP.BAD_REQUEST,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": HTTP.BAD_REQUEST.phrase, "errors": str(e)},
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
        )
        paginated_data = paginate_data(request, lists, limit, offset)

        return Response(paginated_data, status=HTTP.OK)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/lists/[list_id]/  # noqa: E501, W505
class MediaEpisodeListDetailView(drf_views.APIView):
    """Episode list detail view."""

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

    permission_classes = [permissions.IsAuthenticated]

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


# /api/v1/search/[media_type]/
class SearchProviderView(drf_views.APIView):
    """Search view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type):
        """Search for media using the specified provider."""
        search = request.GET.get("search", "")
        source = request.GET.get("source", None)
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

        except Exception:  # noqa: BLE001
            return Response(
                {"detail": HTTP.INTERNAL_SERVER_ERROR.phrase},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        total = (
            last_response.get("total_results")
            if isinstance(last_response, dict)
            else len(results_accum)
        )

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

    permission_classes = [permissions.IsAuthenticated]

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
        score_distribution, top_rated = get_score_distribution(user_media)
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
