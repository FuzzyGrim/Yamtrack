"""Personal series ordering with explicit, reviewable history reconciliation."""

import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from app.models import TV, Item
from app.models.episode_order import EpisodeOrder
from app.providers import credentials, episode_orders, services
from app.services import episode_ordering, metadata_resolution
from app.templatetags.app_tags import media_url
from users.templatetags.user_tags import user_date_format


def owned_tv(user, tv_id):
    """Resolve a tracked series only within the requesting user's library."""
    return get_object_or_404(
        TV.objects.select_related("item", "active_episode_order"),
        pk=tv_id, user=user,
    )


def available_orders(tv, user):
    """Discover configured providers using only established series identities."""
    orders, errors = [], []
    for provider in ("tmdb", "tvdb"):
        series_id = metadata_resolution.resolve_provider_media_id(
            tv.item, provider, route_media_type="tv", persist_links=False,
        )
        if not series_id or not credentials.is_configured(provider, user=user):
            continue
        try:
            orders.extend(episode_orders.list_orders(provider, series_id, user=user))
        except (services.ProviderAPIError, requests.RequestException, ValueError, KeyError):
            errors.append(
                _("%(provider)s episode orders are temporarily unavailable.")
                % {"provider": provider.upper()},
            )
    return orders, errors


def selected_order(tv, user, provider, key):
    """Bind a submitted order to the series' verified provider ID."""
    if provider not in {"tmdb", "tvdb"}:
        message = _("Select TMDB or TVDB.")
        raise ValueError(message)
    series_id = metadata_resolution.resolve_provider_media_id(
        tv.item, provider, route_media_type="tv",
    )
    if not series_id:
        message = _("This series has no verified identity for that provider.")
        raise ValueError(message)
    return episode_ordering.load_order(tv.item, provider, series_id, key, user=user)


def _form_resolutions(data, preview):
    watches = {str(row["id"]): row for row in preview["watches"]}
    groups = {}
    for watch_id in watches:
        anchor = data.get(f"combine_{watch_id}") or watch_id
        if anchor not in watches:
            message = _("Choose a viewing from this preview.")
            raise ValueError(message)
        if anchor != watch_id and data.get(f"archive_{watch_id}") == "on":
            message = _("Archive a viewing or combine it with another, not both.")
            raise ValueError(message)
        groups.setdefault(anchor, []).append(int(watch_id))
    resolutions = []
    for anchor, watch_ids in groups.items():
        if int(anchor) not in watch_ids:
            message = _("Combined viewings must point to a viewing kept separate in its own row.")
            raise ValueError(message)
        archive = data.get(f"archive_{anchor}") == "on"
        if archive and len(watch_ids) > 1:
            message = _("Archive a viewing or combine it with another, not both.")
            raise ValueError(message)
        resolution = {
            "watch_ids": watch_ids,
            "episode_ids": [] if archive else data.getlist(f"episodes_{anchor}"),
            "archive": archive,
        }
        if len(watch_ids) > 1:
            # The form explicitly identifies this viewing as the metadata source.
            resolution["fields"] = {
                field: watches[anchor][field] for field in episode_ordering.WATCH_FIELDS
            }
        resolutions.append(resolution)
    return resolutions


def _preview_context(order, preview):
    """Describe each viewing with its pre-selected destination for the review table."""
    titles = dict(Item.objects.filter(
        pk__in=[row["item_id"] for row in preview["watches"]],
    ).values_list("pk", "title"))
    proven = {row["watch_ids"][0]: row["episode_ids"] for row in preview["resolutions"]}
    rows = []
    for watch in preview["watches"]:
        suggestion = preview["suggestions"].get(watch["id"])
        selected, pending = proven[watch["id"]], None
        kind = "matched" if selected else "needs"
        if suggestion and not selected:
            if suggestion["basis"] == "coordinate":
                pending = suggestion["episode_id"]
            else:
                selected, kind = [suggestion["episode_id"]], "suggested"
        rows.append({
            "id": watch["id"], "title": titles[watch["item_id"]],
            "season_number": watch["season_number"],
            "episode_number": watch["episode_number"], "air_date": watch["air_date"],
            "episode_code": f"S{watch['season_number']:02d}E{watch['episode_number']:02d}",
            "start_date": watch["start_date"], "end_date": watch["end_date"],
            "score": watch["score"], "status": watch["status"], "notes": watch["notes"],
            "selected": selected, "pending": pending, "kind": kind,
            "basis": suggestion["basis"] if suggestion else "",
        })
    return {
        "order": order, "preview": preview, "watches": rows,
        "review": {
            "rows": {str(row["id"]): {
                "selected": row["selected"], "pending": row["pending"],
                "kind": row["kind"], "archive": False, "combine": str(row["id"]),
                "label": f"S{row['season_number']:02d}E{row['episode_number']:02d} {row['title']}",
            } for row in rows},
            "episodes": [{
                "id": episode["provider_episode_id"],
                "code": f"S{episode['season_number']:02d}E{episode['episode_number']:02d}",
                "title": episode["title"], "air_date": episode.get("air_date") or "",
            } for episode in order.catalogue["episodes"]],
        },
    }


def _revert_label(tv):
    """Name the order an undo returns to: the one before the latest change."""
    change = episode_ordering.latest_reversible_change(tv)
    previous = change.before_state.get("active_order") if change else None
    label = (
        EpisodeOrder.objects.filter(pk=previous).values_list("label", flat=True).first()
        if previous else None
    )
    return label or _("Original numbering")


def _order_groups(orders):
    """Group provider orders for display, preserving provider order."""
    groups = {}
    for choice in orders:
        groups.setdefault(choice["provider"], []).append(choice)
    return [(provider.upper(), choices) for provider, choices in groups.items()]


def _episode_ordering_labels():
    """Server-localized copy for the shared review presentation."""
    return {
        "title": _("Episode ordering"),
        "lead": _("Choose how this series is numbered in your library. Your viewing history is reviewed before anything changes."),
        "currentOrder": _("Current order:"),
        "originalNumbering": _("Original numbering"),
        "matched": _("Matched"),
        "suggested": _("Suggested"),
        "needs": _("Needs choice"),
        "chosen": _("Chosen"),
        "archived": _("Archived"),
        "combined": _("Combined"),
        "current": _("Current"),
        "reviewChanges": _("Review changes"),
        "undo": _("Undo ordering change"),
        "revertHelp": _("The order before the latest change."),
        "revertBlocked": _("Can't be restored automatically because viewings were added or edited after the change."),
        "noOrders": _("No episode orders are available. Configure a provider credential and a verified series provider identity in metadata settings."),
        "allViewings": _("All viewings"),
        "needsChoice": _("Needs choice"),
        "acceptAll": _("Accept all suggestions"),
        "suggestionWarning": _("Some suggestions only share a season and episode number with the old episode. Split or merged episodes shift later numbers, so check them before accepting."),
        "reviewHelp": _("Pick the episode each viewing belongs to under the new numbering. Matches by identity, air date and title are filled in for you; check anything marked Suggested or Needs choice."),
        "noDate": _("No date"),
        "emptyViewings": _("This series has no viewings to move."),
        "score": _("Score:"),
        "removeEpisode": _("Remove episode"),
        "chooseEpisode": _("Choose episode"),
        "addOrChange": _("Add or change"),
        "useSuggestion": _("Use suggestion:"),
        "options": _("Options"),
        "archiveLabel": _("Archive under the original order, excluding it from active progress"),
        "combineLabel": _("Combine with another viewing and use its episode, dates, score, notes and status"),
        "keepSeparate": _("Keep as a separate viewing"),
        "searchPlaceholder": _("Search by number, title or air date"),
        "apply": _("Apply episode ordering"),
        "cancel": _("Cancel"),
        "needsRemaining": _("viewings still need an episode, or can be archived."),
        "combinedUses": _("Uses the episode, dates, score and status of"),
        "archiveNote": _("Kept under the original order and left out of active progress."),
    }


def _episode_ordering_props(request, tv, context):
    """Describe the chooser or review for the shared React presentation."""
    labels = _episode_ordering_labels()
    model = {
        "mode": "review" if context.get("preview") else "chooser",
        "itemTitle": tv.item.title,
        "itemUrl": media_url(tv.item),
        "currentOrderLabel": (
            tv.active_episode_order.label if tv.active_episode_order
            else labels["originalNumbering"]
        ),
        "hasActiveOrder": bool(tv.active_episode_order),
        "canRevert": context.get("can_revert", False),
        "revertLabel": context.get("revert_label", ""),
        "providerErrors": context.get("provider_errors", []),
        "orderGroups": [
            {"provider": provider, "choices": [
                {
                    "provider": choice["provider"],
                    "key": choice["key"],
                    "label": choice["label"],
                    "current": bool(choice.get("current")),
                }
                for choice in choices
            ]}
            for provider, choices in context.get("order_groups", [])
        ],
        "cancelUrl": request.path,
    }
    props = {
        "labels": labels,
        "model": model,
        "csrf": get_token(request),
        "actions": {
            "review": request.path,
            "revert": request.path,
            "apply": request.path,
        },
    }
    if not context.get("preview"):
        return props

    watches = []
    seasons = {}
    for watch in context["watches"]:
        start = user_date_format(watch["start_date"], request.user)
        end = user_date_format(watch["end_date"], request.user)
        if start:
            date_label = f"{start} \u2013 {end or labels['noDate']}"
        else:
            date_label = end or labels["noDate"]
        watches.append({
            "id": watch["id"],
            "label": f"S{watch['season_number']:02d}E{watch['episode_number']:02d} · {watch['title']}",
            "seasonNumber": watch["season_number"],
            "episodeNumber": watch["episode_number"],
            "title": watch["title"],
            "dateLabel": date_label,
            "score": watch["score"],
            "status": watch["status"],
            "notes": watch["notes"] or "",
            "selected": list(watch["selected"]),
            "pending": watch["pending"],
            "kind": watch["kind"],
        })
        season = seasons.setdefault(watch["season_number"], [])
        season.append(watch["id"])
    order = context["order"]
    model["review"] = {
        "label": _("Moving to %(label)s") % {"label": order.label},
        "orderId": order.pk,
        "token": context["preview"]["token"],
        "watches": watches,
        "seasons": [
            {
                "number": number,
                "label": _("Season %(number)s") % {"number": number},
                "watches": watch_ids,
            }
            for number, watch_ids in seasons.items()
        ],
        "episodes": [
            {
                "id": episode["id"],
                "code": episode["code"],
                "title": episode["title"],
                "airDate": episode.get("air_date") or "",
            }
            for episode in context["review"]["episodes"]
        ],
    }
    return props


@login_required
@require_http_methods(["GET", "POST"])
def episode_ordering_settings(request, tv_id):
    """Preview and apply episode ordering without changing another user's items."""
    tv = owned_tv(request.user, tv_id)
    context = {"tv": tv}
    status = 200
    if request.method == "POST":
        action, order = request.POST.get("action"), None
        try:
            if action == "revert":
                label = _revert_label(tv)
                episode_ordering.revert_change(tv)
                messages.success(
                    request, _("Episode ordering restored to %(order)s.") % {"order": label},
                )
                return redirect(media_url(tv.item))
            if action == "apply":
                order = get_object_or_404(EpisodeOrder, pk=request.POST.get("order_id"), show=tv.item)
                preview = episode_ordering.preview_change(tv, order)
                episode_ordering.apply_change(
                    tv, order, token=request.POST.get("token", ""),
                    resolutions=_form_resolutions(request.POST, preview),
                )
                messages.success(request, _("Episode ordering updated."))
                return redirect(media_url(tv.item))
            order = selected_order(
                tv, request.user, request.POST.get("provider"), request.POST.get("key"),
            )
            context.update(_preview_context(order, episode_ordering.preview_change(tv, order)))
        except (ValueError, ValidationError) as error:
            messages.error(request, str(error))
            status = 400
            if action == "apply" and order is not None:
                # Stay in the review, with a fresh preview, rather than losing the order.
                context.update(_preview_context(order, episode_ordering.preview_change(tv, order)))
        except (services.ProviderAPIError, requests.RequestException, KeyError):
            messages.error(
                request,
                _("Provider data is unavailable. Your current ordering has been preserved."),
            )
            status = 503
    orders, context["provider_errors"] = available_orders(tv, request.user)
    orders = [dict(choice) for choice in orders]
    active = tv.active_episode_order
    for choice in orders:
        choice["current"] = bool(
            active and active.provider == choice["provider"] and active.key == choice["key"],
        )
    context["order_groups"] = _order_groups(orders)
    context["can_revert"] = episode_ordering.can_revert(tv)
    context["revert_label"] = _revert_label(tv)
    context["episode_ordering_props"] = _episode_ordering_props(request, tv, context)
    return render(request, "app/episode_ordering.html", context, status=status)
