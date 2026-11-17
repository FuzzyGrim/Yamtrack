import json
import logging
from dataclasses import replace

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from app import helpers
from app.bulk_actions import build_bulk_action_data
from app.columns import (
    resolve_column_config,
    resolve_columns,
    resolve_default_column_config,
)
from app.library_query.adapters import filter_values_from_media_list_filters
from app.library_query.alphabet import normalize_initial
from app.library_query.spec import STATUS_MATCH_ANY
from app.media_list_filters import parse_media_list_filters
from app.media_list_views import MEDIA_LIST_NO_STATUS, MEDIA_LIST_NO_STATUS_LABEL
from app.models import Item, MediaTypes
from app.providers import (
    services,  # noqa: F401 — kept so legacy test patches on lists.views.services still work
)
from app.release_years import prefill_display_release_years
from app.table_browser import build_table_browser_props, table_json_url
from integrations import exports
from lists import smart_rules
from lists import tasks as list_tasks
from lists.forms import CustomListForm
from lists.models import CustomList, CustomListItem
from lists.tiers import MAX_TIERS, TIER_BOARD_LIMIT
from lists.views_helpers import (
    _adapt_list_items_for_table,
    _attach_kometa_episode_urls,
    _build_collection_platforms_by_item_id,
    _build_list_count_trigger,
    _build_list_url_template,
    _build_media_type_breakdown,
    _get_completed_item_ids,
    _resolve_list_sort_direction,
    _resolve_list_table_media_type,
    build_tier_columns,
    paginate_list_items,
)
from lists.views_smart_list import _smart_list_detail_response
from users.models import (
    ListDetailLayoutChoices,
    ListDetailSortChoices,
    MediaStatusChoices,
)

logger = logging.getLogger(__name__)


@login_not_required
@never_cache
@require_GET
def list_detail(request, list_reference):
    """Return the detail page of a custom list."""
    reference = str(list_reference or "").strip()
    custom_list = CustomList.objects.get_by_reference(reference)
    if custom_list is None:
        # List doesn't exist - investigate why it might have been shown on lists page
        if reference.isdigit():
            logger.warning(
                "List ID %s not found. User: %s, Authenticated: %s",
                reference,
                request.user.username if request.user.is_authenticated else "anonymous",
                request.user.is_authenticated,
            )

            # Check if user has any lists that might match (for debugging)
            if request.user.is_authenticated:
                user_lists = CustomList.objects.get_user_lists(request.user)
                logger.info(
                    "User %s has %s accessible lists. Checking if list %s should be in that set...",
                    request.user.username,
                    user_lists.count(),
                    reference,
                )

                # Check if there's a list with similar characteristics that was re-imported
                # This helps identify if it's a re-import issue
                trakt_lists = CustomList.objects.filter(
                    owner=request.user,
                    source="trakt",
                )
                logger.info(
                    "User has %s Trakt lists. Recent list IDs: %s",
                    trakt_lists.count(),
                    list(trakt_lists.order_by("-id")[:5].values_list("id", flat=True)),
                )

                messages.error(
                    request,
                    f"List ID {reference} not found. This may indicate a data inconsistency. "
                    "The list may have been deleted or re-imported with a new ID. "
                    "Please refresh the lists page to see current lists.",
                )
                return redirect("lists")
        # For anonymous users, just show 404
        msg = "List not found"
        raise Http404(msg)

    # Check access: public lists are viewable by anyone, private lists require auth
    if not custom_list.user_can_view(request.user):
        if custom_list.visibility == "private":
            # Private list - show 404 with message
            msg = "This list is private."
            raise Http404(msg)
        # Should not reach here, but handle gracefully
        msg = "List not found"
        raise Http404(msg)

    if custom_list.is_smart:
        # Render current membership now; refresh it in the background so the
        # write-heavy sync never runs inside a GET request.
        list_tasks.schedule_smart_list_sync(custom_list)

    if request.user.is_authenticated and custom_list.user_can_edit(request.user):
        # Quick-add once filed searched podcasts under a second Item; fold
        # those into the tracked show so status and removal work again.
        from app.services.podcast_import import merge_stray_podcast_items

        merge_stray_podcast_items(
            Item.objects.filter(
                customlistitem__custom_list=custom_list,
                media_type=MediaTypes.PODCAST.value,
                episode_number__isnull=True,
                season_number__isnull=True,
            ),
        )

    # Anyone who can open the list without editing sees the owner's view of it.
    can_edit = custom_list.user_can_edit(request.user)
    is_public_view = not can_edit
    public_view = (
        not request.user.is_authenticated and custom_list.is_viewable_by_link
    )

    media_user = custom_list.owner if is_public_view else request.user

    if custom_list.is_smart:
        return _smart_list_detail_response(
            request=request,
            custom_list=custom_list,
            can_edit=can_edit,
            is_public_view=is_public_view,
            public_view=public_view,
            media_user=media_user,
        )

    # Get and process request parameters
    # Handle anonymous users by using default values
    valid_sorts = [choice[0] for choice in ListDetailSortChoices.choices]
    valid_statuses = [choice[0] for choice in MediaStatusChoices.choices]

    if request.user.is_authenticated:
        sort_by = request.user.update_preference(
            "list_detail_sort",
            request.GET.get("sort"),
        )
        if sort_by not in valid_sorts:
            sort_by = "date_added"
    else:
        # Default sort for anonymous users
        sort_by = request.GET.get("sort", "date_added")
        # Validate sort choice
        if sort_by not in valid_sorts:
            sort_by = "date_added"
    direction = _resolve_list_sort_direction(
        sort_by,
        request.GET.get("direction"),
    )

    raw_status_filter = request.GET.getlist("status")
    valid_status_values = (set(valid_statuses) - {MediaStatusChoices.ALL}) | {
        MEDIA_LIST_NO_STATUS,
    }
    if request.user.is_authenticated:
        persisted_status_pref = request.user.list_detail_status
        persisted_status_filter = tuple(
            value
            for value in str(persisted_status_pref or "").split(",")
            if value and value in valid_status_values
        )
        if "status" in request.GET:
            status_filter = tuple(
                dict.fromkeys(
                    value
                    for value in raw_status_filter
                    if value in valid_status_values
                ),
            )
            request.user.update_preference(
                "list_detail_status",
                ",".join(status_filter),
            )
        else:
            status_filter = persisted_status_filter
    else:
        status_filter = tuple(
            dict.fromkeys(
                value for value in raw_status_filter if value in valid_status_values
            ),
        )

    selected_media_types = request.GET.getlist("type")
    if not selected_media_types:
        legacy_media_type = request.GET.get("type", "all")
        if legacy_media_type and legacy_media_type != "all":
            selected_media_types = [legacy_media_type]
    if request.user.is_authenticated:
        layout = request.user.update_preference(
            "list_detail_layout",
            request.GET.get("layout"),
        )
    else:
        layout = request.GET.get("layout", "grid")
    if layout not in ListDetailLayoutChoices.values:
        layout = "grid"
    valid_media_types = set(MediaTypes.values)
    selected_media_types = [
        media_type
        for media_type in selected_media_types
        if media_type in valid_media_types
    ]

    params = {
        "sort_by": sort_by,
        "direction": direction,
        "media_types": selected_media_types,
        "status_filter": status_filter,
        "page": helpers.get_page_number(request),
        "search_query": request.GET.get("q", ""),
    }

    is_tiers = layout == ListDetailLayoutChoices.TIERS
    # Build and filter base queryset
    items = custom_list.items.all()
    # Count memberships directly; counting ``items`` joins app_item for nothing.
    total_items_count = CustomListItem.objects.filter(custom_list=custom_list).count()
    media_type_breakdown = _build_media_type_breakdown(custom_list)

    # Compute completion percentage (titles completed / total titles)
    completion_percent = None
    completed_count = 0
    if total_items_count > 0 and not is_public_view:
        # A subquery, not the ids: a 10,000-item list would otherwise send
        # 10,000 parameters with each per-type query.
        member_ids = CustomListItem.objects.filter(custom_list=custom_list).values(
            "item_id"
        )
        completed_count = len(_get_completed_item_ids(request.user, member_ids))
        completion_percent = round(completed_count / total_items_count * 100)

    if params["media_types"]:
        items = items.filter(media_type__in=params["media_types"])
    elif request.GET.get("type_mode") == "subset":
        # The filter menu's "Hide all" leaves no type selected.
        items = items.none()
    filtered_media_types = list(
        items.order_by().values_list("media_type", flat=True).distinct(),
    )
    # The remaining filters (genre, year, rating, dates, tags...) go through
    # the media list's own parser, so a list page accepts exactly the URL
    # filters the media list and the API do. Type, status and search keep the
    # list page's own handling above.
    # A no-status match includes list items with no tracker row as well as
    # rows whose status is null; other statuses match any of the user's rows.
    status_filter = tuple(params["status_filter"] or ())
    parsed_filters = replace(
        parse_media_list_filters(request, strict=False),
        initial="",  # Applied below with the viewer's title preference.
        # Grouping is an API/library view option, not a custom-list filter.
        group_by="item",
        group="",
        statuses=tuple(v for v in status_filter if v != MEDIA_LIST_NO_STATUS),
        include_no_status=MEDIA_LIST_NO_STATUS in status_filter,
        search=params["search_query"],
    )
    list_filters = replace(
        filter_values_from_media_list_filters(parsed_filters),
        status_match=STATUS_MATCH_ANY,
    )
    items_page, filtered_items_count = paginate_list_items(
        custom_list=custom_list,
        media_user=media_user,
        candidates=items.values("pk"),
        initial=request.GET.get("initial"),
        title_user=request.user,
        include_alphabet=not helpers.is_htmx_fragment(request),
        media_types=filtered_media_types,
        filters=list_filters,
        # The Tiers view lists every tier on one board, in tier order.
        sort_by=ListDetailSortChoices.TIER if is_tiers else params["sort_by"],
        direction="asc" if is_tiers else params["direction"],
        page=1 if is_tiers else params["page"],
        page_size=TIER_BOARD_LIMIT if is_tiers else 16,
    )
    collection_platforms_by_item_id = {}

    _attach_kometa_episode_urls(items_page)
    prefill_display_release_years(items_page)

    if layout == "table":
        if not collection_platforms_by_item_id:
            collection_platforms_by_item_id = _build_collection_platforms_by_item_id(
                media_user, [item.id for item in items_page.object_list]
            )
        _adapt_list_items_for_table(items_page, collection_platforms_by_item_id)

    # Get recommendation count for owners/collaborators
    recommendation_count = 0
    if can_edit and custom_list.allow_recommendations:
        recommendation_count = custom_list.recommendations.count()

    # Base context for both full and partial responses
    chip_sort = "score" if params["sort_by"] == "rating" else params["sort_by"]
    is_partial = helpers.is_htmx_fragment(request)
    is_pagination = is_partial and params["page"] > 1
    current_media_type = _resolve_list_table_media_type(
        params["media_types"],
        filtered_media_types,
    )
    sort_choices = sorted(
        (
            choice
            for choice in ListDetailSortChoices.choices
            if choice[0] != ListDetailSortChoices.PLATFORM
            or current_media_type == MediaTypes.GAME.value
        ),
        key=lambda x: x[1],
    )
    context = {
        "user": request.user,
        "custom_list": custom_list,
        "items": items_page,
        "alphabet_other_initial": items_page.alphabet_other_initial,
        "has_next": items_page.has_next(),
        "next_page_number": items_page.next_page_number()
        if items_page.has_next()
        else None,
        "items_count": total_items_count,
        "filtered_items_count": filtered_items_count,
        "current_sort": params["sort_by"],
        "current_direction": params["direction"],
        "chip_sort": chip_sort,
        "current_statuses": params["status_filter"],
        "current_layout": layout,
        "sort_choices": sort_choices,
        "status_choices": [
            *MediaStatusChoices.choices[:1],
            (MEDIA_LIST_NO_STATUS, MEDIA_LIST_NO_STATUS_LABEL),
            *MediaStatusChoices.choices[1:],
        ],
        "public_view": public_view,
        "public_list_reference": custom_list.public_reference
        if is_public_view
        else "",
        "show_public_notes": not is_public_view or custom_list.include_notes,
        "can_edit": can_edit,
        "enable_bulk_select": can_edit,
        "bulk_action_data": (
            build_bulk_action_data(
                request.user,
                request=request,
                status_url=reverse("bulk_status_update"),
                list_url=reverse("bulk_list_add"),
                collection_url=reverse("bulk_collection_quick_add"),
                tag_url=reverse("tag_bulk_toggle"),
            )
            if can_edit
            else {}
        ),
        "list_ordering_enabled": can_edit
        and params["sort_by"] == ListDetailSortChoices.CUSTOM,
        "is_public_view": is_public_view,
        "list_user_role": custom_list.user_role(request.user),
        "recommendation_count": recommendation_count,
        "base_template": "base_public.html" if public_view else "base.html",
        "is_partial": is_partial,
        "is_pagination": is_pagination,
        "current_media_types": params["media_types"],
        "has_media_type_filter": bool(params["media_types"]),
        "column_config": resolve_column_config(
            current_media_type,
            params["sort_by"],
            request.user,
            "list",
        ),
        "default_column_config": resolve_default_column_config(
            current_media_type,
            params["sort_by"],
            "list",
        ),
        "table_type": "list",
        "table_column_update_url": reverse(
            "list_detail_columns",
            args=[custom_list.id],
        ),
        "table_column_media_type": current_media_type,
        "table_refresh_url": reverse(
            "list_detail", args=[custom_list.public_reference]
        ),
        "table_refresh_target": "#items-view",
        "table_refresh_include_selector": "#filter-form",
        "list_reference": custom_list.public_reference,
        "list_url_template": _build_list_url_template(request),
    }

    if is_tiers:
        tier_columns, tier_unranked = build_tier_columns(
            custom_list,
            items_page.object_list,
        )
        context.update(
            {
                "tier_columns": tier_columns,
                "tier_unranked": tier_unranked,
                "tier_limit": TIER_BOARD_LIMIT,
                "tier_config": {
                    "tiers": [column["tier"] for column in tier_columns],
                    "canEdit": can_edit,
                    "csrfToken": get_token(request) if can_edit else "",
                    "maxTiers": MAX_TIERS,
                    "moveUrl": reverse("list_tier_move", args=[custom_list.id]),
                    "saveUrl": reverse("list_tier_save", args=[custom_list.id]),
                    "canFill": can_edit and request.user.ratings_enabled,
                    "fillUrl": reverse("list_tier_fill", args=[custom_list.id]),
                    "undoUrl": reverse("list_tier_fill_undo", args=[custom_list.id]),
                },
                "tier_board_truncated": filtered_items_count
                > len(items_page.object_list),
            },
        )

    if layout == "table":
        context.update(
            {
                "media_list": items_page,
                "resolved_columns": resolve_columns(
                    current_media_type,
                    params["sort_by"],
                    request.user,
                    "list",
                ),
                "table_body_id": "list-table-body",
                "table_pagination_url": reverse(
                    "list_detail", args=[custom_list.public_reference]
                ),
                "table_target_selector": "#list-table-body",
                "table_include_selector": "#filter-form",
            },
        )
        next_url = (
            table_json_url(request, items_page.next_page_number())
            if items_page.has_next()
            else ""
        )
        context["table_browser_props"] = build_table_browser_props(
            rows=list(items_page.object_list),
            resolved_columns=context["resolved_columns"],
            column_config=context["column_config"],
            default_column_config=context["default_column_config"],
            user=request.user,
            table_type="list",
            media_type=current_media_type,
            current_sort=params["sort_by"],
            current_direction=params["direction"],
            update_url=context["table_column_update_url"],
            refresh_url=context["table_refresh_url"],
            refresh_target="#items-view",
            refresh_include="#filter-form",
            csrf_token=get_token(request),
            public_view=public_view,
            public_list_reference=context["public_list_reference"],
            show_public_notes=context["show_public_notes"],
            next_url=next_url,
        )
        if request.GET.get("table_json") == "1":
            return JsonResponse(
                {
                    "rows": context["table_browser_props"]["table"]["rows"],
                    "nextUrl": next_url,
                    "resultCount": filtered_items_count,
                },
            )

    # Additional context for full page render
    if not is_partial:
        context.update(
            {
                "form": CustomListForm(instance=custom_list, user=request.user)
                if can_edit
                else None,
                "media_types": sorted(
                    MediaTypes.values, key=lambda v: MediaTypes(v).label
                ),
                "collaborators_count": custom_list.collaborators.count() + 1,
                "viewers_count": custom_list.viewers.count(),
                "list_detail_page_model": {
                    "testID": "list-detail-page",
                    "title": custom_list.name,
                },
                "completion_percent": completion_percent,
                "completed_count": completed_count,
                "media_type_breakdown": media_type_breakdown,
                "list_filter_data": smart_rules.build_filter_data_for_items(
                    media_user,
                    custom_list.items.values_list("id", flat=True),
                    [entry["value"] for entry in media_type_breakdown],
                    # A visitor must not see the owner's private tag names.
                    precomputed_tags=[] if is_public_view else None,
                    include_list_options=False,
                ),
                "list_filter_state": {
                    **parsed_filters.menu_state(),
                    "media_types": params["media_types"],
                },
            },
        )
        return render(request, "lists/list_detail.html", context)

    # HTMX partial response
    if layout == "table":
        if is_pagination:
            template_name = "app/components/table_items.html"
        else:
            template_name = "lists/components/list_table.html"
    elif is_tiers:
        template_name = "lists/components/tier_board.html"
    else:
        template_name = "lists/components/media_grid.html"

    if normalize_initial(request.GET.get("initial")) and not items_page and not is_pagination:
        template_name = "app/components/alphabet_results_empty.html"
    response = render(request, template_name, context)
    response["HX-Trigger"] = json.dumps(
        _build_list_count_trigger(total_items_count),
    )
    return response


@login_not_required
@require_GET
def list_export_csv(request, list_reference):
    """Stream a single custom list's contents as a CSV file."""
    reference = str(list_reference or "").strip()
    custom_list = CustomList.objects.get_by_reference(reference)
    msg = "List not found"
    if custom_list is None:
        raise Http404(msg)

    if not custom_list.user_can_view(request.user):
        raise Http404(msg)

    filename = slugify(custom_list.name) or "list"
    return StreamingHttpResponse(
        exports.generate_list_csv(custom_list),
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
