"""Structured, permission-aware detail data for non-browser clients."""

import logging
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus as HTTP  # noqa: N814

from django.apps import apps
from django.db.models import Case, F, Max, Min, Q, When
from django.db.models.functions import Coalesce, Greatest
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, ngettext
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import views as drf_views
from rest_framework.response import Response

from app import carousel
from app import models as app_models
from app.activity_builders import (
    _build_detail_activity_state,
    _normalize_detail_episode_actions,
    _paginate_detail_episodes,
)
from app.artwork_sizes import card_image_url
from app.detail_builders import (
    _apply_cached_hltb_link,
    _build_detail_link_sections,
    _build_detail_person_rows,
    _build_game_lengths_context,
    _build_imdb_rating_context,
    _build_mal_rating_context,
    _build_opencritic_context,
    _build_trakt_popularity_context,
    build_book_formats,
)
from app.detail_related import enrich_detail_related_cards, enrich_detail_seasons
from app.models import CollectionEntry, Item, MediaTypes
from app.providers import manual, services, tmdb
from app.services import metadata_resolution, youtube_game_watch
from app.services.metadata_fallback import stored_metadata_fallback
from app.tag_views import _build_detail_tag_sections
from app.templatetags.app_tags import (
    country_code,
    detail_promoted_facts,
    iso_date_format,
    media_url,
    safe_count,
    slug,
    translate_detail_value,
    translate_language_code,
)
from integrations import seerr_api
from integrations.imports import helpers as import_helpers
from users.appearance import resolved_detail_layouts
from users.templatetags.ui_tags import _people_cast_props
from users.templatetags.user_tags import detail_layout_family, user_date_format

from .authentication import HasScope
from .contract_serializers import DetailErrorSerializer
from .fork_serializers import CollectionEntrySerializer
from .helpers import check_source_type, check_valid_type
from .schema import MEDIA_TYPE_TV_ONLY_PARAM
from .serializers import serialize_data

logger = logging.getLogger(__name__)


class PublicDetailRead(HasScope):
    """Allow public metadata while retaining scope checks for authenticated tokens."""

    def has_permission(self, request, view):
        """Permit anonymous reads; scoped tokens still need the mapped scope."""
        if not getattr(request.user, "is_authenticated", False):
            return True
        return super().has_permission(request, view)


def _owned_media(user, item):
    """Return the caller's tracking row without exposing another user's state."""
    if not getattr(user, "is_authenticated", False) or item is None:
        return None
    try:
        model = apps.get_model("app", item.media_type)
    except LookupError:
        return None
    queryset = model.objects.filter(item=item)
    if item.media_type == app_models.MediaTypes.EPISODE.value:
        return queryset.filter(related_season__user=user).first()
    return queryset.filter(user=user).first()


def _nonempty_list(value):
    if isinstance(value, (tuple, list)):
        return [entry for entry in value if entry not in (None, "", [], {})]
    return [value] if value not in (None, "", [], {}) else []


def _tracking_entries(viewer, item, *, public_view, can_write, media_type):
    """Return bounded actual consumptions, separate from the audit change log."""
    if public_view or viewer is None or item is None:
        return [], 0
    queryset = app_models.BasicMedia.objects.filter_media_prefetch(
        viewer, item.media_id, item.media_type, item.source,
        season_number=item.season_number, episode_number=item.episode_number,
        library_media_type=media_type, annotate_progress=False,
    ).filter(item=item)
    count = queryset.count()
    start_field, end_field = "start_date", "end_date"
    if item.media_type == "season":
        queryset = queryset.annotate(detail_entry_start=Min("episodes__end_date"), detail_entry_end=Max("episodes__end_date"))
        start_field, end_field = "detail_entry_start", "detail_entry_end"
    elif queryset.model == app_models.TV:
        queryset = queryset.annotate(
            detail_main_start=Min("seasons__episodes__end_date", filter=~Q(seasons__item__season_number=0)),
            detail_any_start=Min("seasons__episodes__end_date"),
            detail_entry_end=Max("seasons__episodes__end_date"),
        ).annotate(detail_entry_start=Coalesce(
            "detail_main_start", "detail_any_start",
            Case(When(status=app_models.Status.IN_PROGRESS.value, then=F("created_at"))),
        ))
        start_field, end_field = "detail_entry_start", "detail_entry_end"
    # Match helpers.entry_activity_key while limiting rows before materialization.
    entries = list(queryset.annotate(
        detail_activity=Greatest(Coalesce(end_field, start_field, "created_at"), Coalesce(start_field, end_field, "created_at")),
        detail_start=Coalesce(start_field, "created_at"),
    ).order_by("-detail_activity", "-detail_start", "-created_at", "-pk")[:50])
    app_models.BasicMedia.objects.annotate_max_progress(entries, item.media_type)
    return [{
        "id": entry.pk,
        "identity": {
            "mediaType": media_type, "detailType": item.media_type,
            "source": item.source, "mediaId": str(item.media_id),
            "seasonNumber": item.season_number, "episodeNumber": item.episode_number,
            "instanceId": entry.pk,
        },
        "media_type": item.media_type,
        "status": entry.status,
        "score": _primitive(entry.score),
        "display_score": _primitive(viewer.scale_score_for_display(entry.score)) if viewer.ratings_enabled else None,
        "scale_max": viewer.rating_scale_max,
        "progress": entry.progress,
        "formatted_progress": entry.formatted_progress,
        "progress_unit": getattr(entry, "progress_unit", ""),
        "progress_is_percentage": getattr(entry, "progress_is_percentage", False),
        "max_progress": getattr(entry, "max_progress", None),
        "completed_episode_count": entry.completed_episode_count if item.media_type in {"tv", "season"} else None,
        "reading_time": getattr(entry, "reading_time", None),
        "start_date": _primitive(entry.start_date), "end_date": _primitive(entry.end_date),
        "notes": entry.notes,
        "can_edit": can_write,
    } for entry in entries], count


def _primitive(value):
    """Convert builder output to JSON-safe values without leaking model objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _primitive(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(entry) for entry in value]
    return str(value)


def _secondary_detail(metadata, item, source, media_type, route_media_type, viewer, book_formats):
    """Project the web detail sidebar without rendering HTML or doing writes."""
    authors = []
    if media_type in {"book", "comic", "manga"}:
        if item is not None:
            authors = [
                {
                    "source": credit.person.source,
                    "person_id": credit.person.source_person_id,
                    "name": credit.person.name,
                }
                for credit in item.person_credits.filter(
                    role_type=app_models.CreditRoleType.AUTHOR.value,
                ).select_related("person").order_by("sort_order", "person__name")
            ]
        if not authors:
            authors = [
                {"source": source, "person_id": str(author.get("person_id") or author.get("id")), "name": author["name"].strip()}
                for author in _nonempty_list(metadata.get("authors_full"))
                if isinstance(author, dict) and (author.get("person_id") or author.get("id")) is not None and (author.get("name") or "").strip()
            ]
    for author in authors:
        author["href"] = reverse("person_detail", kwargs={
            "source": author["source"], "person_id": author["person_id"], "name": slug(author["name"]),
        })

    studios = []
    if item is not None:
        studios = [
            {
                "source": credit.studio.source,
                "studio_id": credit.studio.source_studio_id,
                "name": credit.studio.name,
                "logo": credit.studio.logo,
            }
            for credit in item.studio_credits.select_related("studio").order_by("sort_order", "studio__name")
        ]
    if not studios:
        studios = [
            {"source": source, "studio_id": str(studio.get("studio_id") or studio.get("id")), "name": studio["name"].strip(), "logo": studio.get("logo") or ""}
            for studio in _nonempty_list(metadata.get("studios_full"))
            if isinstance(studio, dict) and (studio.get("studio_id") or studio.get("id")) is not None and (studio.get("name") or "").strip()
        ]
    for studio in studios:
        studio["href"] = reverse("studio_detail", kwargs={
            "source": studio["source"], "studio_id": studio["studio_id"], "name": slug(studio["name"]),
        })

    region = getattr(viewer, "watch_provider_region", None)
    watch_providers = None
    if media_type in {"movie", "tv", "anime", "season"} and region not in (None, "", "UNSET"):
        payload = metadata.get("providers")
        if payload is None and item is not None:
            payload = item.watch_providers or None
        mapped_id = None
        mapped_type = route_media_type
        try:
            if route_media_type == "anime" and source == "mal" and not payload:
                identity = metadata_resolution.resolve_mal_tmdb_identity(metadata.get("media_id"))
                if identity is not None:
                    mapped_id, mapped_type = identity.media_id, identity.media_type
            elif item is not None and route_media_type in {"tv", "anime"} and (not payload or source == "tvdb"):
                mapped_id = metadata_resolution.resolve_provider_media_id(
                    item, app_models.Sources.TMDB.value, route_media_type=route_media_type,
                    # Detail reads do not persist a newly resolved provider link.
                    persist_links=False,
                )
            if mapped_id:
                mapped = services.get_media_metadata(
                    mapped_type, mapped_id, app_models.Sources.TMDB.value,
                    language=metadata_resolution.metadata_language_default(viewer, item),
                    user=viewer,
                )
                payload = mapped.get("providers") or payload
        except (services.ProviderAPIError, TypeError, ValueError):
            logger.warning("Unable to enrich detail watch providers for %s/%s", source, metadata.get("media_id"))
        if payload is not None or media_type == "season":
            watch_providers = tmdb.filter_providers(payload, region)
        pinned = tmdb.pinned_provider_matches(item, viewer.pinned_watch_providers)
        if pinned:
            merged = {provider["provider_id"]: provider for provider in watch_providers or []}
            for provider in pinned:
                merged.setdefault(provider["provider_id"], provider)
            watch_providers = sorted(merged.values(), key=lambda provider: provider.get("display_priority", 999))

    details = metadata.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    display_details = {}
    for key, value in details.items():
        if key in {"first_air_date", "last_air_date"}:
            display = user_date_format(value, viewer) if viewer else value
            if key == "first_air_date" and details.get("last_air_date"):
                end = details["last_air_date"]
                display = f"{display} \u2013 {user_date_format(end, viewer) if viewer else end}"
        elif key in {"total_runtime", "runtime"} and details.get("episodes"):
            count = details["episodes"]
            display = str(value if value is not None else gettext("Unknown")) + " · " + ngettext("%(count)s episode", "%(count)s episodes", safe_count(count)) % {"count": count}
        elif key in {"start_date", "end_date", "publish_date", "release_date", "last_updated"}:
            display = iso_date_format(value, viewer)
        elif key == "language":
            display = translate_language_code(value)
        elif key in {"season", "broadcast", "source", "players"}:
            display = translate_detail_value(value)
        elif key == "format" and media_type == "anime":
            display = gettext("Anime")
        elif key in {"format", "status", "country", "countries", "production_countries", "origin_country", "languages"}:
            display = [gettext(str(entry)) if entry else gettext("Unknown") for entry in value] if isinstance(value, list) else gettext(str(value)) if value else gettext("Unknown")
        else:
            display = value
        display_details[key] = _primitive(display)
    promotion = detail_promoted_facts(media_type, details)
    for field in promotion["fields"]:
        key, value = field["key"], field["value"]
        if key == "locale":
            languages = ", ".join(translate_detail_value(entry) for entry in _nonempty_list(value.get("languages")))
            countries = ", ".join(translate_detail_value(country_code(entry)) for entry in _nonempty_list(value.get("country")))
            display = f"{languages} ({countries})" if languages and countries else languages or countries
        elif key == "air_dates":
            display = " \u2013 ".join(str(user_date_format(entry, viewer) if viewer else entry) for entry in value if entry)
        elif key == "release_date":
            display = iso_date_format(value, viewer)
        elif key == "episodes":
            display = ngettext("%(count)s episode", "%(count)s episodes", safe_count(value)) % {"count": value}
        elif key == "total_runtime" and media_type == "tv" and details.get("episodes"):
            count = details["episodes"]
            display = str(value) + " · " + ngettext("%(count)s episode", "%(count)s episodes", safe_count(count)) % {"count": count}
        elif isinstance(value, list):
            display = ", ".join(str(entry) for entry in value)
        else:
            display = display_details.get(key, value)
        field["display_value"] = _primitive(display)
    return {
        "details": _primitive(details),
        "display_details": display_details,
        "labels": {
            "unknown": gettext("Unknown"),
            "airDates": gettext("AIR DATES"),
            "runtime": gettext("RUNTIME"),
            "anime": gettext("Anime"),
            "format": gettext("Format"),
            "author": gettext("AUTHOR"),
            "authors": gettext("AUTHORs"),
            "details": gettext("Details"),
            "noDetails": gettext("No details available"),
            "streaming": gettext("STREAMING"),
            "noWatchProviders": gettext("No watch providers"),
        },
        "authors_linked": authors,
        "studios_linked": studios,
        "author_detail_keys": ["author", "authors", "people"],
        "studio_detail_keys": ["studios", "companies"],
        "book_formats_replaces": "format" if book_formats else "",
        "promotion": {
            "fields": _primitive(promotion["fields"]),
            "suppressed_keys": sorted(promotion["suppressed_keys"]),
        },
        "watch_providers": _primitive(watch_providers),
        "watch_provider_region": region,
    }


def _media_card(entry, *, public_view, viewer=None, can_write=False):
    """Build the shared MediaCard model used by related/recommendation rails."""
    raw = entry.get("item") if isinstance(entry, dict) and "item" in entry else entry
    tracked = entry.get("media") if isinstance(entry, dict) else None
    if not isinstance(raw, dict):
        return None
    media_type = raw.get("media_type") or "movie"
    source = raw.get("source")
    media_id = str(raw.get("media_id") or "")
    title = raw.get("card_title") or raw.get("title") or ""
    image = raw.get("image") or ""
    if not source or not media_id or not title:
        return None
    try:
        href = media_url(raw)
    except (KeyError, TypeError, ValueError):
        href = ""
    status = getattr(tracked, "status", None)
    score = getattr(tracked, "score", None)
    ratings_enabled = bool(viewer and viewer.ratings_enabled and not public_view)
    display_score = viewer.scale_score_for_display(score) if ratings_enabled else None
    progress = getattr(tracked, "progress", None)
    max_progress = getattr(tracked, "max_progress", None)
    release_date = raw.get("release_date") or raw.get("release_datetime") or ""
    year = str(release_date)[:4] if release_date else ""
    return {
        "key": f"{media_type}-{source}-{media_id}-{raw.get('season_number', '')}",
        "active": False,
        "model": {
            "cardKey": f"{media_type}-{source}-{media_id}",
            "identity": {"mediaType": media_type, "source": source, "mediaId": media_id},
            "mediaType": media_type,
            "shape": "square" if media_type in {"music", "podcast"} else "poster",
            "href": href,
            "image": {"src": card_image_url(image), "fallback": "", "source": "primary"},
            "title": title,
            "altTitle": raw.get("original_title") or raw.get("alternative_title"),
            "statusChip": {"kind": "status", "status": status, "hideOnMobile": False}
            if status and not public_view
            else None,
            "cornerChips": [],
            "titleLinks": [],
            "progressBar": None,
            "lines": [{"join": "plain", "parts": [{"text": year}]}] if year else [],
            "rating": {"label": str(display_score), "value": _primitive(display_score), "scaleMax": viewer.rating_scale_max, "rateLabel": "Rate"}
            if display_score is not None
            else None,
            "ratingEditable": bool(tracked and ratings_enabled and can_write),
            "actions": None,
            "readOnly": public_view,
            "trackedProgress": progress,
            "maxProgress": max_progress,
        },
    }


def _child_row(raw, *, media_type, parent_media_type, source, media_id, public_view, can_write, viewer=None):
    """Normalize provider season/episode data for the shared detail child cards."""
    if not isinstance(raw, dict):
        return None
    tracking = raw.get("media") if isinstance(raw.get("item"), dict) else None
    raw = raw.get("item") if isinstance(raw.get("item"), dict) else raw
    number = raw.get("episode_number") if media_type == "episode" else raw.get("season_number")
    identity = {
        "mediaType": parent_media_type,
        "detailType": media_type,
        "source": source,
        "mediaId": str(raw.get("media_id") or media_id),
        "seasonNumber": raw.get("season_number"),
        "episodeNumber": raw.get("episode_number"),
    }
    title = raw.get("title") or raw.get("name") or raw.get("season_title") or (
        f"Season {number}" if media_type == "season" and number is not None else
        f"Episode {number}" if number is not None else ""
    )
    image = raw.get("image") or ""
    date_value = raw.get("air_date") or raw.get("first_air_date") or raw.get("release_date") or ""
    overview = raw.get("overview") or raw.get("description") or ""
    progress = getattr(tracking, "progress", None)
    max_progress = getattr(tracking, "max_progress", None)
    try:
        percent = min(100, round(progress / max_progress * 100)) if progress is not None and max_progress else None
    except (TypeError, ZeroDivisionError):
        percent = None
    editable_episode = can_write and not public_view and media_type == "episode" and parent_media_type == "tv"
    plays = raw.get("history") or []
    watched = bool(raw.get("watched") or plays)
    score = raw.get("score")
    if score is None and plays:
        score = getattr(plays[0], "score", None)
    can_rate = bool(editable_episode and viewer and viewer.ratings_enabled)
    season_card = None
    if media_type == "season" and number is not None:
        from dataclasses import asdict

        from app.card_surfaces import SURFACES, _card_render_context
        from app.media_card_model import _corner_chips, _progress_bar, _status_chip

        item = {**raw, "media_type": "season", "source": source,
                "media_id": identity["mediaId"], "title": title,
                "season_number": number, "library_media_type": parent_media_type}
        context = {**asdict(SURFACES["home"]), "item": item,
                   "media": None if public_view else tracking, "user": viewer,
                   "card_media_type": "season", "public_view": public_view}
        context.update(_card_render_context(context))
        season_card = {"statusChip": _status_chip(context),
                       "cornerChips": _corner_chips(context, "season"),
                       "progressBar": _progress_bar(context, "season")}
    return {
        "key": f"{media_type}-{source}-{identity['mediaId']}-{number}",
        "number": number,
        "identity": identity,
        "title": title,
        "image": {"src": card_image_url(image), "fallback": ""},
        "overview": overview,
        "dateLabel": date_value,
        "card": season_card,
        "code": raw.get("code") or (f"S{raw['season_number']}E{raw['episode_number']}" if media_type == "episode" and raw.get("season_number") is not None and raw.get("episode_number") is not None else ""),
        "runtime": raw.get("runtime") or "",
        "countLabel": raw.get("episode_count") or raw.get("details", {}).get("episodes") or "",
        "statusLabel": getattr(tracking, "status", None) if not public_view else None,
        "dropped": getattr(tracking, "status", None) == app_models.Status.DROPPED.value if not public_view else False,
        "percent": percent,
        "score": _primitive(viewer.scale_score_for_display(score)) if can_rate else None,
        "scaleMax": viewer.rating_scale_max if viewer and not public_view else 10,
        "canRate": can_rate,
        "actions": ([{"key": "unwatch", "label": "Mark unwatched", "tone": "surface"}]
                    if editable_episode and watched else
                    [{"key": "watch", "label": "Mark watched", "tone": "primary"}]
                    if editable_episode else []),
        "unreleased": bool(raw.get("unreleased")),
        "publicView": public_view,
    }


class DetailPageView(drf_views.APIView):
    """Project provider detail metadata and the caller's own tracking state."""

    permission_classes = [PublicDetailRead]

    @extend_schema(
        parameters=[
            OpenApiParameter("public_view", bool, required=False),
            OpenApiParameter("detail_type", str, required=False),
            OpenApiParameter("season_number", int, required=False),
            OpenApiParameter("episode_number", int, required=False),
            OpenApiParameter("episode_page", int, required=False),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id):
        """Return serializable detail sections and permission capabilities."""
        detail_type = request.query_params.get("detail_type") or media_type
        season_number = request.query_params.get("season_number")
        episode_number = request.query_params.get("episode_number")
        provider_metadata = {}
        try:
            season_number = int(season_number) if season_number is not None else None
            episode_number = int(episode_number) if episode_number is not None else None
        except (TypeError, ValueError):
            return Response({"detail": "Invalid season or episode number."}, status=HTTP.BAD_REQUEST)
        is_video = media_type == app_models.MediaTypes.VIDEO.value
        if not check_valid_type(media_type) and not is_video:
            return Response({"detail": "Unsupported media type."}, status=HTTP.BAD_REQUEST)
        if detail_type in {
            app_models.MediaTypes.SEASON.value,
            app_models.MediaTypes.EPISODE.value,
        }:
            if media_type not in {
                app_models.MediaTypes.TV.value,
                app_models.MediaTypes.ANIME.value,
            }:
                return Response({"detail": "Unsupported detail type."}, status=HTTP.BAD_REQUEST)
            if season_number is None or (
                detail_type == app_models.MediaTypes.EPISODE.value
                and episode_number is None
            ):
                return Response({"detail": "A season and episode number are required."}, status=HTTP.BAD_REQUEST)
        elif detail_type != media_type:
            return Response({"detail": "Unsupported detail type."}, status=HTTP.BAD_REQUEST)
        if (is_video and source != app_models.Sources.YOUTUBE.value) or (
            not is_video and not check_source_type(media_type, source)
        ):
            return Response(
                {"detail": f"Cannot query `{source}` for `{media_type}` media type"},
                status=HTTP.BAD_REQUEST,
            )

        item_query = app_models.Item.objects.filter(
            media_type=detail_type,
            source=source,
            media_id=media_id,
        )
        if detail_type in {
            app_models.MediaTypes.SEASON.value,
            app_models.MediaTypes.EPISODE.value,
        } and season_number is not None:
            item_query = item_query.filter(
                season_number=season_number,
            )
            if media_type == app_models.MediaTypes.ANIME.value:
                item_query = item_query.filter(library_media_type=app_models.MediaTypes.ANIME.value)
            else:
                item_query = item_query.exclude(library_media_type=app_models.MediaTypes.ANIME.value)
        if (
            detail_type == app_models.MediaTypes.EPISODE.value
            and episode_number is not None
        ):
            item_query = item_query.filter(
                episode_number=episode_number,
            )
        item = item_query.first()
        viewer = request.user if getattr(request.user, "is_authenticated", False) else None
        public_view = request.query_params.get("public_view") == "1" or viewer is None
        tracked = None if public_view else _owned_media(viewer, item)

        try:
            with services.interactive_request_scope():
                is_child_detail = detail_type in {
                    app_models.MediaTypes.SEASON.value,
                    app_models.MediaTypes.EPISODE.value,
                }
                provider_metadata = services.get_media_metadata(
                    "tv_with_seasons" if is_child_detail else media_type,
                    media_id,
                    source,
                    language=metadata_resolution.metadata_language_default(viewer, item),
                    user=viewer,
                    **({"season_numbers": [season_number]} if is_child_detail else {}),
                )
                if is_child_detail:
                    metadata = provider_metadata.get(f"season/{season_number}")
                    if not metadata:
                        if item is None:
                            return Response({"detail": "Media not found."}, status=HTTP.NOT_FOUND)
                        metadata = stored_metadata_fallback(item)
                    else:
                        metadata = dict(metadata)
                        season_items = app_models.Item.objects.filter(
                            media_type="season", source=source, media_id=media_id, season_number=season_number,
                        )
                        season_items = (season_items.filter(library_media_type="anime") if media_type == "anime"
                                        else season_items.exclude(library_media_type="anime"))
                        season_item = item if detail_type == "season" else season_items.first()
                        season = None if public_view else _owned_media(viewer, season_item)
                        if metadata.get("episodes"):
                            processor = manual.process_episodes if source == "manual" else tmdb.process_episodes
                            metadata["episodes"] = processor(
                                metadata, season.episodes.all() if season else [], season=season,
                            )
                        if detail_type == "episode":
                            episode = next((entry for entry in metadata.get("episodes", [])
                                            if entry.get("episode_number") == episode_number), None)
                            if episode is None and item is None:
                                return Response({"detail": "Media not found."}, status=HTTP.NOT_FOUND)
                            metadata = dict(episode or stored_metadata_fallback(item))
                            metadata.setdefault("synopsis", metadata.get("overview") or "")
                else:
                    metadata = provider_metadata
        except services.ProviderAPIError as error:
            if item is not None:
                metadata = stored_metadata_fallback(item)
            elif error.status_code == HTTP.NOT_FOUND:
                return Response({"detail": "Media not found."}, status=HTTP.NOT_FOUND)
            else:
                return Response(
                    {"detail": "The provider could not load this detail."},
                    status=HTTP.BAD_GATEWAY,
                )
        except Exception:
            logger.exception("Structured detail projection failed for %s/%s", media_type, source)
            if item is None:
                return Response(
                    {"detail": "The provider could not load this detail."},
                    status=HTTP.BAD_GATEWAY,
                )
            metadata = stored_metadata_fallback(item)

        metadata = metadata if isinstance(metadata, dict) else {}
        metadata.setdefault("source", source)
        metadata.setdefault("media_type", media_type)
        metadata.setdefault("media_id", media_id)
        if not isinstance(metadata.get("related"), dict):
            metadata["related"] = {}
        if media_type in {app_models.MediaTypes.TV.value, app_models.MediaTypes.ANIME.value}:
            try:
                enrich_detail_seasons(
                    metadata,
                    media_id=media_id,
                    source=source,
                    user=viewer,
                    detail_item=item,
                    render_secondary_only=True,
                )
            except services.ProviderAPIError:
                logger.warning("Unable to enrich detail seasons for %s/%s", source, media_id)
        if viewer is not None and not getattr(viewer, "show_recommendations", True):
            metadata["related"].pop("recommendations", None)
        if not public_view:
            try:
                enrich_detail_related_cards(
                    request,
                    metadata,
                    media_type=media_type,
                    tracking_user=viewer,
                )
            except Exception:
                logger.exception("Unable to enrich detail cards for %s/%s", source, media_id)

        # The PWA fills missing TV runtimes from the same local season index,
        # then adds the bounded model's total runtime before formatting details.
        if detail_type in {"movie", "tv", "anime"} and item is not None:
            from app.media_details_views import _get_tv_runtime_display_fallback

            detail_values = metadata.setdefault("details", {})
            if isinstance(detail_values, dict):
                if media_type == "tv" and not detail_values.get("runtime"):
                    fallback_runtime = _get_tv_runtime_display_fallback(item, metadata)
                    if fallback_runtime:
                        detail_values["runtime"] = fallback_runtime
                runtime_media = tracked or apps.get_model("app", media_type)(item=item)
                app_models.BasicMedia.objects.annotate_max_progress([runtime_media], media_type)
                total_runtime = runtime_media.formatted_total_runtime
                if total_runtime and total_runtime != "--":
                    ordered = {}
                    for key, value in detail_values.items():
                        ordered[key] = value
                        if key == "runtime":
                            ordered["total_runtime"] = total_runtime
                    ordered.setdefault("total_runtime", total_runtime)
                    metadata["details"] = ordered

        title_fields = app_models.Item.title_fields_from_metadata(
            metadata,
            fallback_title=item.title if item else "",
        )
        facts = []
        details = metadata.get("details") or {}
        if isinstance(details, dict):
            facts = [
                {"label": str(label), "value": str(value)}
                for label, value in details.items()
                if value not in (None, "", [], {})
            ]
        try:
            gallery = carousel.resolve_carousel_media(
                detail_type if detail_type in {"season", "episode"} else media_type,
                source,
                media_id,
                season_number=season_number,
                cached_only=True,
            )
        except Exception:
            logger.exception("Unable to resolve detail gallery for %s/%s", source, media_id)
            gallery = None
        if media_type == app_models.MediaTypes.VIDEO.value and not metadata.get("image"):
            metadata["image"] = f"https://i.ytimg.com/vi/{media_id}/hqdefault.jpg"

        score_chips = []
        score_builders = (
            ("Trakt", _build_trakt_popularity_context(item, media_type)),
            ("IMDb", _build_imdb_rating_context(item, media_type)),
            ("MyAnimeList", _build_mal_rating_context(item, media_type)),
            ("OpenCritic", _build_opencritic_context(item, media_type)),
        )
        for label, score in score_builders:
            if score:
                score_chips.append({"label": label, **_primitive(score)})
        if not public_view and tracked is not None and tracked.score is not None:
            score_chips.insert(0, {"label": "Your score", "value": _primitive(tracked.score), "kind": "viewer"})

        related_sections = []
        token = request.auth
        can_write = not hasattr(token, "has_scope") or token.has_scope("watchlist:write")
        related = metadata.get("related") or {}
        section_labels = {
            "recommendations": "Recommendations",
            "sequels": "Sequels",
            "prequels": "Prequels",
            "similar": "Similar media",
            "related": "Related media",
            "dlcs": "DLCs",
            "expansions": "Standalone expansions",
            "remakes": "Remakes",
            "soundtracks": "Soundtracks",
            "collections": "Collections",
        }
        if isinstance(related, dict):
            for key, entries in related.items():
                if key == "seasons":
                    continue
                cards = [card for entry in _nonempty_list(entries) if (card := _media_card(entry, public_view=public_view, viewer=viewer, can_write=can_write))]
                if cards:
                    related_sections.append({"id": key, "label": section_labels.get(key, key.replace("_", " ").title()), "items": cards})

        person_rows = _build_detail_person_rows(metadata, item=item)
        people = {
            kind: _people_cast_props(
                {"user": None if public_view else viewer},
                person_rows.get(f"{kind}_row", {}), source, "",
            )["items"]
            for kind in ("cast", "crew")
        }

        raw_episodes = _normalize_detail_episode_actions(metadata.get("episodes") or [])
        requested_page = request.query_params.get("episode_page", 1)
        try:
            episode_page = max(1, int(requested_page))
        except (TypeError, ValueError):
            episode_page = 1
        episode_rows, episode_load_more = _paginate_detail_episodes(request, raw_episodes)
        children_kind = "issue" if media_type == app_models.MediaTypes.COMIC.value else "episode"
        child_media_type = "issue" if children_kind == "issue" else "episode"
        children = {
            "kind": children_kind,
            "seasons": [row for value in _nonempty_list(related.get("seasons")) if (row := _child_row(value, media_type="season", parent_media_type=media_type, source=source, media_id=media_id, public_view=public_view, can_write=can_write, viewer=viewer))],
            "episodes": [row for value in episode_rows if (row := _child_row(value, media_type=child_media_type, parent_media_type=media_type, source=source, media_id=media_id, public_view=public_view, can_write=can_write, viewer=viewer))],
            "next_page": episode_page + 1 if episode_load_more else None,
            "next_label": episode_load_more.get("label") if episode_load_more else None,
        }

        tag_sections = _build_detail_tag_sections(
            metadata,
            item,
            None if public_view else viewer,
            genre_list_media_type=media_type,
        )
        links = _build_detail_link_sections(
            metadata,
            media_type,
            source,
            metadata.get("display_provider") or source,
            item=item,
        )
        _apply_cached_hltb_link(metadata, item)
        game_lengths = _build_game_lengths_context(item) if media_type == app_models.MediaTypes.GAME.value else None
        youtube_watch = None
        if media_type == app_models.MediaTypes.GAME.value and tracked is not None and not public_view:
            try:
                youtube_watch = youtube_game_watch.watched_for_game(viewer, item)
            except Exception:
                logger.exception("Unable to build game YouTube activity for %s/%s", source, media_id)
        book_formats = [] if public_view else build_book_formats(viewer, item)
        providers = _primitive(metadata.get("providers") or {})
        activity_subtitle = None
        if tracked is not None and detail_type == "season":
            activity_subtitle = {
                "primary_text": f"Progress: {tracked.completed_episode_count}"
                + (f"/{metadata['max_progress']}" if metadata.get("max_progress") else ""),
                "date_start": tracked.start_date,
                "date_end": tracked.end_date,
            }
        elif tracked is not None and detail_type == media_type:
            try:
                _play_stats, activity_subtitle = _build_detail_activity_state(
                    media_type,
                    metadata,
                    current_instance=tracked,
                    user_medias=[tracked],
                )
            except Exception:
                logger.exception("Unable to build detail activity for %s/%s", source, media_id)
        collections = []
        if not public_view and viewer is not None and item is not None:
            collections = list(
                app_models.CollectionEntry.objects.filter(user=viewer, item=item)
                .order_by("-updated_at")
                .values(
                    "media_type",
                    "resolution",
                    "hdr",
                    "is_3d",
                    "audio_codec",
                    "audio_channels",
                    "bitrate",
                    "purchase_price",
                    "purchase_location",
                    "collected_at",
                ),
            )

        sections = []
        for section_id, label, value in (
            ("genres", "Genres", metadata.get("genres")),
            ("studios", "Studios", metadata.get("studios_full") or metadata.get("studios")),
        ):
            values = _nonempty_list(value)
            if values:
                sections.append({"id": section_id, "title": label, "items": _primitive(values)})

        progress = getattr(tracked, "progress", None)
        tracking = None if tracked is None else {
            "status": getattr(tracked, "status", None),
            "score": getattr(tracked, "score", None),
            "progress": progress,
            "max_progress": metadata.get("max_progress"),
            "notes": getattr(tracked, "notes", ""),
        }
        history = []
        if tracked is not None and not public_view:
            history = [
                {
                    "date": _primitive(entry.history_date),
                    "end_date": _primitive(getattr(entry, "end_date", None)),
                    "status": getattr(entry, "status", None),
                    "progress": getattr(entry, "progress", None),
                    "score": _primitive(getattr(entry, "score", None)),
                    "history_type": getattr(entry, "history_type", ""),
                }
                for entry in tracked.history.all().order_by("-history_date")[:8]
            ]
        tracking_entries, tracking_entries_count = _tracking_entries(
            viewer, item, public_view=public_view, can_write=can_write, media_type=media_type,
        )
        is_authenticated = bool(viewer)
        layout_family = {
            "episode": "episode",
            "comicissue": "comic_issue",
        }.get(detail_type) or detail_layout_family(detail_type)
        detail_layouts = resolved_detail_layouts(
            viewer.detail_page_layouts if viewer is not None and not public_view else {},
        )
        return Response({
            "identity": {
                "media_type": detail_type,
                "parent_media_type": media_type if detail_type != media_type else None,
                "source": source,
                "media_id": media_id,
                "season_number": season_number,
                "episode_number": episode_number,
            },
            "title": title_fields.get("title") or metadata.get("title") or "",
            "alt_title": metadata.get("original_title") or "",
            "subtitle": metadata.get("subtitle") or metadata.get("series_name") or "",
            "season_title": metadata.get("season_header_title") or metadata.get("season_title") or "",
            "season_options": [
                row for value in _nonempty_list((provider_metadata.get("related") or {}).get("seasons"))
                if (row := _child_row(value, media_type="season", parent_media_type=media_type,
                                      source=source, media_id=media_id, public_view=public_view,
                                      can_write=can_write))
            ] if detail_type == "season" and isinstance(provider_metadata, dict) else [],
            "artwork": {
                "src": metadata.get("image") or "",
                "aspect": "poster",
                "backdrop": (gallery or {}).get("hero", {}).get("url") or (gallery or {}).get("overview", {}).get("url") or "",
                "logo": (gallery or {}).get("overview", {}).get("logo_url") or (gallery or {}).get("hero", {}).get("logo_url") or "",
                "gallery": _primitive(gallery or {"photos": [], "video": None}),
                "gallery_supported": carousel.carousel_supported(media_type, source),
            },
            "synopsis": metadata.get("synopsis") or "",
            "facts": facts,
            "layout": {
                "family": layout_family,
                "zones": detail_layouts.get(layout_family, {}),
            },
            "secondary": _secondary_detail(
                metadata, item, source, detail_type, media_type,
                None if public_view else viewer, book_formats,
            ),
            "sections": sections,
            "score_chips": _primitive(score_chips),
            "tag_sections": _primitive(tag_sections),
            "related_sections": related_sections,
            "people": people,
            "children": children,
            "providers": providers,
            "link_sections": _primitive(links),
            "game_lengths": _primitive(game_lengths),
            "youtube_watch": _primitive(youtube_watch),
            "book_formats": _primitive(book_formats),
            "detail_links": _primitive(person_rows),
            "media_details": _primitive(details),
            "activity_subtitle": _primitive(activity_subtitle),
            "collections": _primitive(collections),
            "viewer_rating": {
                "enabled": bool(viewer and not public_view and viewer.ratings_enabled),
                "scale_max": viewer.rating_scale_max if viewer and not public_view else 10,
                "value": _primitive(viewer.scale_score_for_display(tracked.score))
                if tracked is not None and viewer and not public_view else None,
            },
            "tracking": tracking,
            "tracking_entries": tracking_entries,
            "tracking_entries_count": tracking_entries_count,
            "tracking_entries_has_more": tracking_entries_count > len(tracking_entries),
            "history": history,
            "read_only": public_view or not is_authenticated,
            "public_view": public_view or not is_authenticated,
            "capabilities": {
                "can_request_seerr": bool(
                    is_authenticated and not public_view
                    and media_type in (app_models.MediaTypes.MOVIE.value, app_models.MediaTypes.TV.value)
                    and source == "tmdb" and _seerr_configured(request.user)
                ),
                "can_track": (
                    is_authenticated
                    and can_write
                    and not public_view
                    and tracked is None
                    and detail_type not in {
                        app_models.MediaTypes.VIDEO.value,
                        app_models.MediaTypes.EPISODE.value,
                    }
                ),
                "can_update": (
                    is_authenticated
                    and can_write
                    and not public_view
                    and tracked is not None
                    and (detail_type != app_models.MediaTypes.EPISODE.value or media_type == app_models.MediaTypes.TV.value)
                ),
            },
        }, status=HTTP.OK)


class DetailGalleryView(drf_views.APIView):
    """Resolve deferred detail-gallery media without returning HTML."""

    permission_classes = [PublicDetailRead]

    @extend_schema(
        parameters=[OpenApiParameter("season_number", int, required=False)],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id):
        """Return clearlogo, backdrop, photos and trailer data for the hero."""
        season_number = request.query_params.get("season_number")
        try:
            season_number = int(season_number) if season_number is not None else None
            if not check_valid_type(media_type) or not check_source_type(media_type, source):
                return Response({"detail": "Unsupported media identity."}, status=HTTP.BAD_REQUEST)
            if not carousel.carousel_supported(media_type, source):
                return Response({"gallery": None}, status=HTTP.OK)
            gallery = carousel.resolve_carousel_media(
                media_type,
                source,
                media_id,
                season_number=season_number,
                cached_only=False,
            )
        except (TypeError, ValueError):
            return Response({"detail": "Invalid season number."}, status=HTTP.BAD_REQUEST)
        except Exception:
            logger.exception("Deferred detail gallery failed for %s/%s", source, media_id)
            return Response({"detail": "The artwork gallery could not be loaded."}, status=HTTP.BAD_GATEWAY)
        if gallery is None:
            return Response({"gallery": None}, status=HTTP.OK)
        return Response({"gallery": _primitive(gallery)}, status=HTTP.OK)


def _seerr_configured(user):
    return bool(user.seerr_url and user.seerr_api_key and user.seerr_user_id)


# /api/v1/media/[type]/[source]/[id]/request/
class MediaSeerrRequestView(drf_views.APIView):
    """Show a title's Seerr state and request it (mirrors seerr_request).

    The web view takes a TMDB id; this one does too, so it applies to
    ``tmdb`` movies and shows only. Anything else, or a user without Seerr
    configured, is a 404 like the web panel.
    """

    def _client(self, request, media_type, source):
        if (
            media_type not in (MediaTypes.MOVIE.value, MediaTypes.TV.value)
            or source != "tmdb"
            or not _seerr_configured(request.user)
        ):
            return None
        return seerr_api.SeerrClient.for_user(request.user)

    def _summary(self, request, client, media_type, media_id):
        return {
            **seerr_api.summarize(media_type, client.media(media_type, media_id)),
            "seerr_url": f"{request.user.seerr_url}/{media_type}/{media_id}",
        }

    @extend_schema(
        operation_id="retrieveMediaSeerrRequest",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id):
        """Return the Seerr request state for the title."""
        try:
            client = self._client(request, media_type, source)
            if client is None:
                return Response({"detail": "Seerr is not available."}, status=HTTP.NOT_FOUND)
            return Response(self._summary(request, client, media_type, media_id))
        except (seerr_api.SeerrError, import_helpers.MediaImportError) as error:
            return Response({"detail": error.message}, status=HTTP.BAD_GATEWAY)

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        operation_id="requestMediaInSeerr",
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request, media_type, source, media_id):
        """Request the title (all seasons, or the listed `seasons`)."""
        try:
            client = self._client(request, media_type, source)
            if client is None:
                return Response({"detail": "Seerr is not available."}, status=HTTP.NOT_FOUND)
            raw = request.data.get("seasons") or []
            seasons = [int(n) for n in raw if str(n).isdigit()] if isinstance(raw, list) else []
            client.request(
                media_type,
                media_id,
                request.user.seerr_user_id,
                seasons=seasons or None,
            )
            return Response(self._summary(request, client, media_type, media_id))
        except (seerr_api.SeerrError, import_helpers.MediaImportError) as error:
            return Response({"detail": error.message}, status=HTTP.BAD_GATEWAY)


def _collection_stats(request, tracked_type, lookup):
    """Return the web Collection card's collected seasons and episodes counts, or None.

    Only shows and seasons have them; they are computed by the same helpers the
    detail page uses.
    """
    from app.helpers import get_season_collection_stats, get_tv_show_collection_stats
    from app.media_details_views import _metadata_episode_count
    from app.providers import services

    if tracked_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        item = Item.objects.filter(media_type=tracked_type, **lookup).first()
        if not item:
            return None
        # The web card counts episodes from the provider metadata, like the Details card.
        try:
            metadata = services.get_media_metadata(tracked_type, item.media_id, item.source)
            episode_count = _metadata_episode_count(metadata)
        except Exception:
            episode_count = None
        return get_tv_show_collection_stats(
            request.user, item, metadata_episode_count=episode_count,
        )
    if tracked_type == MediaTypes.SEASON.value:
        item = Item.objects.filter(media_type=tracked_type, **lookup).first()
        return get_season_collection_stats(request.user, item) if item else None
    return None


def _collection_episode_resolution_counts(user, item, stats):
    """Classify the same covered episodes used by the collection totals.

    A recorded episode uses its latest copy; broader entries cover episodes
    according to the existing show/season collection fallback rules.
    """
    identity = {"media_id": item.media_id, "source": item.source}
    episodes = Item.objects.filter(media_type=MediaTypes.EPISODE.value, **identity)
    if item.media_type == MediaTypes.SEASON.value:
        episodes = episodes.filter(season_number=item.season_number)
    else:
        episodes = episodes.exclude(season_number=0)
    episode_rows = list(episodes.values("id", "season_number"))
    ids = [row["id"] for row in episode_rows]
    episode_copies = CollectionEntry.objects.filter(
        user=user, item_id__in=ids,
    ).order_by("item_id", "-collected_at", "-id").values("item_id", "resolution")
    latest = {}
    for entry in episode_copies:
        latest.setdefault(entry["item_id"], entry["resolution"] or "")

    seasons = CollectionEntry.objects.filter(
        user=user, item__media_type=MediaTypes.SEASON.value, **{
            f"item__{key}": value for key, value in identity.items()
        },
    )
    if item.media_type == MediaTypes.SEASON.value:
        seasons = seasons.filter(item=item)
    seasons = seasons.order_by("item__season_number", "-collected_at", "-id").values(
        "item__season_number", "resolution",
    )
    season_resolutions = {}
    for entry in seasons:
        season_resolutions.setdefault(entry["item__season_number"], entry["resolution"] or "")

    granular_entries = CollectionEntry.objects.filter(
        user=user, item__media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value],
        **{f"item__{key}": value for key, value in identity.items()},
    )
    if item.media_type != MediaTypes.SEASON.value:
        granular_entries = granular_entries.exclude(item__season_number=0)
    granular = granular_entries.exists()
    parent = item if item.media_type != MediaTypes.SEASON.value else None
    if parent is None:
        from app.helpers import _resolve_show_item_for_season

        parent = _resolve_show_item_for_season(item)
    show_entry = None if granular or parent is None else CollectionEntry.objects.filter(
        user=user, item=parent,
    ).order_by("-collected_at", "-id").values("resolution").first()

    counts = {}
    for episode in episode_rows:
        episode_id = episode["id"]
        season = episode["season_number"]
        if episode_id in latest:
            resolution = latest[episode_id]
        elif season in season_resolutions and (
            item.media_type != MediaTypes.SEASON.value or not latest
        ):
            resolution = season_resolutions[season]
        elif show_entry is not None:
            resolution = show_entry["resolution"] or ""
        else:
            continue
        counts[resolution] = counts.get(resolution, 0) + 1

    # Metadata can report more episodes than are represented by local Items.
    # Broader coverage can include those episodes without per-episode details.
    unclassified = max(0, stats["collected_episodes"] - sum(counts.values()))
    if unclassified:
        resolution = (show_entry or {}).get("resolution") or ""
        counts[resolution] = counts.get(resolution, 0) + unclassified
    return [{"resolution": resolution, "count": count} for resolution, count in counts.items()]


def _collection_entries(request, tracked_type, **lookup):
    item_ids = Item.objects.filter(media_type=tracked_type, **lookup).values("id")
    entries = (
        CollectionEntry.objects.filter(user=request.user, item_id__in=item_ids)
        .select_related("item")
        .order_by("-collected_at", "-id")
    )
    payload = {
        "results": serialize_data(
            list(entries),
            many=True,
            serializer_class=CollectionEntrySerializer,
        ),
    }
    # Include descendant copies in the context without changing the editable
    # entries above, which are scoped to the exact item.
    context_items = Item.objects.filter(media_type=tracked_type, **lookup)
    if tracked_type in (MediaTypes.TV.value, MediaTypes.ANIME.value, MediaTypes.SEASON.value):
        bucket = tracked_type if tracked_type != MediaTypes.SEASON.value else MediaTypes.TV.value
        child_lookup = {key: value for key, value in lookup.items() if key != "episode_number"}
        context_items = Item.objects.filter(
            **child_lookup,
            # Older child rows use their own type as the library bucket.
            library_media_type__in=[bucket, MediaTypes.SEASON.value, MediaTypes.EPISODE.value],
            media_type__in=([MediaTypes.SEASON.value, MediaTypes.EPISODE.value]
                            if tracked_type == MediaTypes.SEASON.value else
                            [tracked_type, MediaTypes.SEASON.value, MediaTypes.EPISODE.value]),
        )
    payload["context_copies"] = list(
        CollectionEntry.objects.filter(user=request.user, item__in=context_items)
        .order_by("item_id", "id")
        .values("id", "item_id", "item__title", "item__media_type", "item__season_number",
                "item__episode_number", "resolution", "media_type", "hdr", "is_3d",
                "audio_codec", "audio_channels", "bitrate", "purchase_price",
                "purchase_location", "collected_at")
    )
    from app.collection_views import (
        _build_collection_episode_audit_entries,
        _build_collection_season_audit_entries,
    )

    item = Item.objects.filter(media_type=tracked_type, **lookup).first()
    if item and tracked_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        rows = _build_collection_season_audit_entries(request.user, item)
        season_numbers = dict(Item.objects.filter(
            id__in=[row["season_item_id"] for row in rows],
        ).values_list("id", "season_number"))
        episodes_by_season = {}
        resolutions_by_item = {}
        for copy in payload["context_copies"]:
            if copy["item__media_type"] == MediaTypes.EPISODE.value:
                resolutions_by_item.setdefault(copy["item_id"], []).append(copy["resolution"] or "")
        episodes = Item.objects.filter(
            media_type=MediaTypes.EPISODE.value, media_id=item.media_id, source=item.source,
            season_number__in=season_numbers.values(),
        ).order_by("season_number", "episode_number", "id").values(
            "id", "title", "season_number", "episode_number",
        )
        for episode in episodes:
            episode["resolutions"] = resolutions_by_item.get(episode["id"], [])
            episodes_by_season.setdefault(episode["season_number"], []).append(episode)
        payload["audit_kind"] = "seasons"
        payload["audit_entries"] = [
            {
                "id": row["season_item_id"],
                "title": row["display_title"],
                "source_labels": row["source_labels"],
                "quality_label": row["quality_label"],
                "metadata": row["collection_entry"],
                "season_number": season_numbers.get(row["season_item_id"]),
                "episodes": episodes_by_season.get(season_numbers.get(row["season_item_id"]), []),
            }
            for row in rows
        ]
    elif item and tracked_type == MediaTypes.SEASON.value:
        rows = _build_collection_episode_audit_entries(request.user, item)
        payload["audit_kind"] = "episodes"
        payload["audit_entries"] = [
            {"id": row["entry"].id, "title": row["title"],
             "source_labels": row["source_labels"], "quality_label": row["quality_label"]}
            for row in rows
        ]
    stats = _collection_stats(request, tracked_type, lookup)
    if stats is not None:
        payload["stats"] = stats
        if item is not None:
            payload["resolution_counts"] = _collection_episode_resolution_counts(request.user, item, stats)
    return Response(
        payload,
        status=HTTP.OK,
    )


class CollectionSeasonAuditView(drf_views.APIView):
    """Apply a season audit action to the caller's collected episodes."""

    def _entries(self, request, season_id):
        season = Item.objects.filter(id=season_id, media_type=MediaTypes.SEASON.value).first()
        if not season:
            return CollectionEntry.objects.none()
        return CollectionEntry.objects.filter(
            user=request.user, item__media_id=season.media_id, item__source=season.source,
            item__media_type=MediaTypes.EPISODE.value, item__season_number=season.season_number,
        )

    @extend_schema(operation_id="updateCollectionSeasonAudit", request=OpenApiTypes.OBJECT,
                   responses={200: OpenApiTypes.OBJECT})
    def patch(self, request, season_id):
        """Apply validated, changed copy fields to every episode in this season."""
        from app.collection_views import _COLLECTION_ENTRY_COPY_FIELDS
        from app.forms import CollectionEntryForm

        entries = self._entries(request, season_id)
        first = entries.select_related("item").first()
        if not first:
            return Response({"detail": "No collected episodes found."}, status=HTTP.NOT_FOUND)
        changed = {key: value for key, value in request.data.items() if key in _COLLECTION_ENTRY_COPY_FIELDS}
        if not changed:
            return Response({"detail": "No supported fields to update."}, status=HTTP.BAD_REQUEST)
        data = {name: first.serializable_value(name) for name in CollectionEntryForm.Meta.fields}
        data.update(changed)
        form = CollectionEntryForm(data, instance=first, user=request.user,
                                   collection_media_type=first.item.media_type)
        if not form.is_valid():
            return Response({"detail": "Invalid collection data.", "errors": form.errors}, status=HTTP.BAD_REQUEST)
        entries.update(**{name: form.cleaned_data[name] for name in changed}, updated_at=timezone.now())
        return Response({"updated": True})

    @extend_schema(operation_id="deleteCollectionSeasonAudit", responses={204: None})
    def delete(self, request, season_id):
        """Remove the caller's episode copies represented by this season row."""
        deleted, _ = self._entries(request, season_id).delete()
        if not deleted:
            return Response({"detail": "No collected episodes found."}, status=HTTP.NOT_FOUND)
        return Response(status=HTTP.NO_CONTENT)


# /api/v1/media/[type]/[source]/[id]/collection/entries/
class MediaCollectionEntriesView(drf_views.APIView):
    """List the caller's owned copies of one item, addressed by identity.

    ``GET /collection/`` pages the whole collection, so a client looking for
    one title past the first page never found it; this answers for the title
    itself, and the season and episode variants for a show's parts.
    """

    @extend_schema(
        operation_id="listMediaCollectionEntries",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id):
        """Return the caller's collection entries for the item."""
        return _collection_entries(
            request,
            media_type,
            media_id=media_id,
            source=source,
        )


# /api/v1/media/tv/[source]/[id]/[season]/collection/entries/
class MediaSeasonCollectionEntriesView(drf_views.APIView):
    """Season variant of MediaCollectionEntriesView."""

    @extend_schema(
        parameters=[MEDIA_TYPE_TV_ONLY_PARAM],
        operation_id="listMediaSeasonCollectionEntries",
        responses={200: OpenApiTypes.OBJECT, 400: DetailErrorSerializer},
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Return the caller's collection entries for one season."""
        if media_type != MediaTypes.TV.value:
            return Response(
                {"detail": "Seasons and episodes are supported only for 'tv'."},
                status=HTTP.BAD_REQUEST,
            )
        return _collection_entries(
            request,
            MediaTypes.SEASON.value,
            media_id=media_id,
            source=source,
            season_number=int(season_number),
        )


# /api/v1/media/tv/[source]/[id]/[season]/[episode]/collection/entries/
class MediaEpisodeCollectionEntriesView(drf_views.APIView):
    """Episode variant of MediaCollectionEntriesView."""

    @extend_schema(
        parameters=[MEDIA_TYPE_TV_ONLY_PARAM],
        operation_id="listMediaEpisodeCollectionEntries",
        responses={200: OpenApiTypes.OBJECT, 400: DetailErrorSerializer},
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Return the caller's collection entries for one episode."""
        if media_type != MediaTypes.TV.value:
            return Response(
                {"detail": "Seasons and episodes are supported only for 'tv'."},
                status=HTTP.BAD_REQUEST,
            )
        return _collection_entries(
            request,
            MediaTypes.EPISODE.value,
            media_id=media_id,
            source=source,
            season_number=int(season_number),
            episode_number=int(episode_number),
        )


def _progress_payload(breakdown_model):
    """Return the card plus the full breakdown the web dialog opens, in one payload."""
    model = breakdown_model["model"]
    summary = model["summary"]
    return {
        "card": {
            "percent": summary["percent"],
            "caughtUp": bool(summary["caughtUp"]),
            "runKey": model["runKey"],
            "rewatch": summary["rewatch"],
        },
        "model": model,
    }


def _watched_text(instance):
    """Return the show hero's watched duration ("2h 48min"), as the web prints it."""
    from app.activity_builders import (
        _build_detail_activity_state,
        _format_detail_activity_duration,
    )
    from app.models import TV

    if not isinstance(instance, TV):
        return ""
    play_stats, _subtitle = _build_detail_activity_state(
        instance.item.media_type, {}, current_instance=instance, user_medias=[instance],
    )
    text = _format_detail_activity_duration((play_stats or {}).get("total_minutes"), "watched")
    return (text or "").removesuffix(" watched")


# /api/v1/media/tv/[source]/[id]/progress-card/
class MediaProgressCardView(drf_views.APIView):
    """The "Your Progress" card and breakdown of a tracked show or season.

    The same projection the web card and its dialog render
    (``app.progress_views``); 404 when the caller does not track the title.
    """

    def _instance(self, request, media_type, source, media_id, season_number):
        from app.models import TV, Season

        if media_type not in (MediaTypes.TV.value, MediaTypes.ANIME.value):
            return None
        lookup = {
            "user": request.user,
            "item__media_id": media_id,
            "item__source": source,
        }
        if season_number is None:
            return TV.objects.select_related("item").filter(
                item__media_type=media_type, **lookup,
            ).first()
        return Season.objects.select_related("item", "related_tv__item").filter(
            item__season_number=int(season_number), **lookup,
        ).first()

    @extend_schema(
        operation_id="retrieveMediaProgressCard",
        responses={200: OpenApiTypes.OBJECT, 404: DetailErrorSerializer},
    )
    def get(self, request, media_type, source, media_id, season_number=None):
        """Return the progress card and breakdown model."""
        from app.models import Season
        from app.progress_views import _presentation
        from app.services.progress_breakdown import build_progress_breakdown

        instance = self._instance(request, media_type, source, media_id, season_number)
        if instance is None:
            return Response({"detail": "Progress is not tracked."}, status=HTTP.NOT_FOUND)
        breakdown = build_progress_breakdown(
            **({"season": instance} if isinstance(instance, Season) else {"tv": instance}),
        )
        if breakdown.percent is None:
            return Response({"detail": "Progress is not tracked."}, status=HTTP.NOT_FOUND)
        payload = _progress_payload(_presentation(breakdown))
        payload["watched_text"] = _watched_text(instance)
        return Response(payload)


# /api/v1/media/tv/[source]/[id]/[season]/progress-card/
class MediaSeasonProgressCardView(MediaProgressCardView):
    """Season variant of MediaProgressCardView."""

    @extend_schema(
        operation_id="retrieveMediaSeasonProgressCard",
        parameters=[MEDIA_TYPE_TV_ONLY_PARAM],
        responses={200: OpenApiTypes.OBJECT, 404: DetailErrorSerializer},
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Return the season's progress card and breakdown model."""
        return super().get(request, media_type, source, media_id, season_number)


# /api/v1/media/[type]/[source]/[id]/checkin/
class MediaCheckInView(drf_views.APIView):
    """The detail page's Check-in split button (mirrors ``checkin_views``).

    GET lists the actions the web button offers for the caller's entry; POST
    runs one of them. Anything that cannot be checked in, or is not tracked, is
    a 404 like the web, which shows no Check-in button there.
    """

    def _phase(self, request, media_type, source, media_id):
        from app import checkin
        from app.models import BasicMedia
        from app.services.completion import select_preferred_activity_entry

        current = select_preferred_activity_entry(
            BasicMedia.objects.filter_media_prefetch(
                request.user, media_id, media_type, source,
            ),
        )
        if current is None:
            return None, None
        return current, checkin.phase(request.user, current)

    @extend_schema(
        operation_id="retrieveMediaCheckIn",
        responses={200: OpenApiTypes.OBJECT, 404: DetailErrorSerializer},
    )
    def get(self, request, media_type, source, media_id):
        """Return the Check-in actions for the tracked entry."""
        from app import checkin_session
        from app.track_actions import track_actions

        checkin_session.settle(request.user.id)
        current, phase = self._phase(request, media_type, source, media_id)
        if current is None or phase is None:
            return Response({"detail": "Check-in is not available."}, status=HTTP.NOT_FOUND)
        actions = track_actions(current.item.media_type, current.status, phase)
        return Response({
            "phase": phase["phase"],
            "actions": [
                {key: str(value) for key, value in action.items()} for action in actions
            ],
        })

    @extend_schema(
        operation_id="createMediaCheckInAction",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 400: DetailErrorSerializer, 404: DetailErrorSerializer},
    )
    def post(self, request, media_type, source, media_id):
        """Run one Check-in action (start, resume, pause, finish, stop, cancel, log_play)."""
        from app import checkin, checkin_session
        from app.checkin_views import ACTIONS, SESSION_ACTIONS

        name = request.data.get("action")
        if checkin_session.supports(media_type):
            action = SESSION_ACTIONS.get(name)
            arguments = (request.user, source, media_type, media_id)
        else:
            action = ACTIONS.get(name) if checkin.supports(media_type, source) else None
            arguments = (request.user, media_type, media_id)
        if action is None:
            return Response({"detail": "Unsupported check-in."}, status=HTTP.BAD_REQUEST)
        try:
            action(*arguments)
        except checkin.CheckInError as error:
            return Response({"detail": error.message}, status=HTTP.BAD_REQUEST)
        return self.get(request, media_type, source, media_id)


# /api/v1/media/tv/[source]/[id]/collect/
# /api/v1/media/tv/[source]/[id]/[season]/collect/
class MediaShowCollectView(drf_views.APIView):
    """Add a copy of every episode of a show or season to the collection.

    The detail page's Collection "add" for a show or season (web
    ``collection_add`` for a season or show item): each episode without a copy
    gets one carrying the sent format fields; episodes already collected are
    left alone. Fields are validated like ``MediaCollectionView``.
    """

    @extend_schema(
        operation_id="updateMediaShowCollection",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 400: DetailErrorSerializer, 404: DetailErrorSerializer},
    )
    def put(self, request, media_type, source, media_id, season_number=None):
        """Collect every episode under the show or season."""
        from app.collection_views import _expand_collection_entry_to_episodes

        from .fork_views import MediaCollectionView

        if media_type not in (MediaTypes.TV.value, MediaTypes.ANIME.value):
            return Response({"detail": "Only shows are collected this way."}, status=HTTP.BAD_REQUEST)
        fields, error = MediaCollectionView()._entry_fields(request.data)
        if error is not None:
            return error
        lookup = {"media_id": media_id, "source": source}
        if season_number is None:
            lookup["media_type"] = media_type
        else:
            lookup.update(media_type=MediaTypes.SEASON.value, season_number=int(season_number))
        item = Item.objects.filter(**lookup).first()
        if item is None:
            return Response({"detail": "Title not found."}, status=HTTP.NOT_FOUND)
        created, skipped = _expand_collection_entry_to_episodes(
            request.user, item, cleaned_data=fields,
        )
        return Response({"created": len(created), "already_collected": skipped})


class MediaSeasonCollectView(MediaShowCollectView):
    """Season variant of MediaShowCollectView."""

    @extend_schema(
        operation_id="updateMediaSeasonCollection",
        parameters=[MEDIA_TYPE_TV_ONLY_PARAM],
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 400: DetailErrorSerializer, 404: DetailErrorSerializer},
    )
    def put(self, request, media_type, source, media_id, season_number):
        """Collect every episode under the season."""
        return super().put(request, media_type, source, media_id, season_number)


def _stored_item(media_type, source, media_id, season_number=None):
    """Return the catalog Item behind a detail route, or None."""
    lookup = {"source": source, "media_id": str(media_id)}
    if season_number is not None:
        lookup.update(media_type=MediaTypes.SEASON.value, season_number=int(season_number))
    else:
        lookup["media_type"] = media_type
    return Item.objects.filter(**lookup).first()


# Static logo of each score chip, as the web chip shows it (others show the source's initials).
_SCORE_LOGOS = {
    "tmdb": "img/tmdb-logo.png",
    "mal": "img/myanimelist-logo.svg",
    "igdb": "img/igdb-logo.png",
    "trakt": "img/trakt-logo.svg",
    "imdb": "img/imdb-logo.png",
    "opencritic": "img/opencritic-logo.svg",
}


# /api/v1/media/comic/[source]/[id]/issues/
class MediaIssuesView(drf_views.APIView):
    """A comic volume's issues, as the web "Issues" list reads them (cover, number, date, read)."""

    @extend_schema(
        operation_id="listMediaIssues",
        responses={200: OpenApiTypes.OBJECT, 400: DetailErrorSerializer},
    )
    def get(self, request, media_type, source, media_id):
        """Return one page of the volume's issues with the viewer's read state."""
        if media_type != MediaTypes.COMIC.value:
            return Response(
                {"detail": "Issues are supported only for 'comic'."}, status=HTTP.BAD_REQUEST,
            )
        from app.media_details_views import _enrich_comic_issues
        from app.providers import services

        try:
            limit = max(1, min(int(request.query_params.get("limit", 20)), 100))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            return Response({"detail": "limit and offset must be integers."}, status=HTTP.BAD_REQUEST)
        metadata = services.get_media_metadata(media_type, media_id, source)
        issues = _enrich_comic_issues(metadata.get("issues") or [], request.user, source)
        return Response({
            "pagination": {"total": len(issues), "limit": limit, "offset": offset},
            "results": [
                {
                    "media_id": str(issue["media_id"]),
                    "source": issue["source"],
                    "title": issue.get("title") or "",
                    "issue_number": str(issue.get("issue_number") or ""),
                    "cover_date": issue.get("cover_date") or "",
                    "image": issue.get("image") or "",
                    "read": bool(issue.get("history")),
                }
                for issue in issues[offset:offset + limit]
            ],
        })


class MediaAnimeEpisodesView(drf_views.APIView):
    """The read-only grouped episode preview of a flat anime detail page."""

    @extend_schema(operation_id="listMediaAnimeEpisodes", parameters=[OpenApiParameter("episode_page", int, OpenApiParameter.QUERY)], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, media_type, source, media_id):
        """Reuse the web preview and retain each mapped provider identity."""
        from django.db.models import OuterRef, Q, Subquery

        from app.activity_builders import _paginate_detail_episodes
        from app.metadata_sync_views import _build_flat_anime_episode_preview
        from app.models import BasicMedia, Episode
        from app.providers import services
        from app.services import metadata_resolution
        from users.appearance import resolved_detail_layouts

        if media_type != MediaTypes.ANIME.value:
            return Response({"detail": "Episode previews are supported only for 'anime'."}, status=HTTP.BAD_REQUEST)
        metadata = services.get_media_metadata(media_type, media_id, source)
        detail_item = _stored_item(media_type, source, media_id)
        resolved = metadata_resolution.resolve_detail_metadata(
            request.user, item=detail_item, route_media_type=media_type,
            media_id=media_id, source=source, base_metadata=metadata,
            persistence_mode="best_effort", persist_links=False,
        ) if detail_item else None
        display_metadata = resolved.header_metadata if resolved else metadata
        rows = metadata.get("episodes")
        if not isinstance(rows, list):
            rows = _build_flat_anime_episode_preview(
                request, detail_item=detail_item,
                media_id=media_id, base_metadata=metadata, metadata_resolution_result=resolved,
            ) or []
        total = len(rows)
        rows, load_more = _paginate_detail_episodes(request, rows)
        # A rating without a watch is deliberately absent from Episode.objects
        # and the web preview's history. Resolve only this page's identities,
        # selecting at most one viewer-owned rating per catalog episode.
        coordinates = Q(pk__in=[])
        for row in rows:
            if row.get("season_number") is not None and row.get("episode_number") is not None:
                coordinates |= Q(source=row.get("source") or source, media_id=str(row.get("media_id") or media_id),
                                 season_number=row["season_number"], episode_number=row["episode_number"])
        rating_rows = Episode.ratings.filter(
            related_season__user=request.user, item_id=OuterRef("pk"), rating_only=True,
        ).order_by("-scored_at", "-pk")
        page_ratings = {
            (episode.source, str(episode.media_id), episode.season_number, episode.episode_number): episode
            for episode in Item.objects.filter(coordinates, media_type=MediaTypes.EPISODE.value).annotate(
                preview_score=Subquery(rating_rows.values("score")[:1]),
                preview_rating_season=Subquery(rating_rows.values("related_season_id")[:1]),
            ).only("source", "media_id", "season_number", "episode_number")
        }
        for index, row in enumerate(rows):
            rating = page_ratings.get((row.get("source") or source, str(row.get("media_id") or media_id),
                                       row.get("season_number"), row.get("episode_number")))
            if rating is not None and rating.preview_rating_season:
                rows[index] = {**row, "unwatched_score": rating.preview_score,
                               "rating_season_id": row.get("rating_season_id") or rating.preview_rating_season}
        visible_content = resolved_detail_layouts(request.user.detail_page_layouts).get("series", {}).get("content", [])
        related = display_metadata.get("related") or {} if "related" in visible_content else {}
        tracked = BasicMedia.objects.filter_media(request.user, media_id, media_type, source).exists()
        token = request.auth
        can_write = not hasattr(token, "has_scope") or token.has_scope("watchlist:write")
        return Response({"pagination": {"total": total}, "load_more": load_more,
                         "related": related, "results": [
            {
                "item": {
                    "media_id": str(row.get("media_id") or media_id),
                    "source": row.get("source") or source,
                    "title": row.get("title") or "",
                    "season_number": row.get("season_number"),
                    "episode_number": row.get("episode_number"),
                    "image": row.get("image") or "",
                },
                "display_episode_number": row.get("display_episode_number") or row.get("episode_number"),
                "overview": row.get("overview") or "",
                "air_date": row.get("air_date"),
                "runtime": row.get("runtime"),
                "watched": bool(row.get("history") or row.get("watched_by_progress")),
                "collected": bool(row.get("collection_entry")),
                "can_list": bool(row.get("item")),
                "can_rate": bool(can_write and request.user.ratings_enabled and (tracked or row.get("rating_season_id"))),
                "score": next((entry.score for entry in row.get("history") or [] if getattr(entry, "score", None) is not None), row.get("unwatched_score")),
                "display_score": _primitive(request.user.scale_score_for_display(next((entry.score for entry in row.get("history") or [] if getattr(entry, "score", None) is not None), row.get("unwatched_score")))) if request.user.ratings_enabled else None,
                "scale_max": request.user.rating_scale_max,
            }
            for row in rows
        ]})


# /api/v1/media/[type]/[source]/[id]/scores/
class MediaScoresView(drf_views.APIView):
    """Stored provider score chips and series graphs, as the web chip row reads them.

    Chips: Trakt, IMDb, MyAnimeList and OpenCritic from the stored Item fields
    (``app.detail_builders._build_*_context``). Graphs (TV and anime only) are
    the stored episode-rating grids behind the web "View series graph" popover.
    """

    @extend_schema(
        operation_id="retrieveMediaScores",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id, season_number=None):
        """Return the stored score chips, series graphs and game lengths of one title."""
        from app.detail_builders import (
            _build_game_lengths_context,
            _build_imdb_rating_context,
            _build_mal_rating_context,
            _build_opencritic_context,
            _build_series_graph_data,
            _build_stored_season_scores_graph,
            _build_trakt_popularity_context,
        )

        item = _stored_item(media_type, source, media_id, season_number)
        route_type = MediaTypes.SEASON.value if season_number is not None else media_type
        chips = []
        if item is not None:
            if item.provider_rating is not None and item.provider_rating_count is not None:
                chips.append({
                    "key": "provider",
                    "label": source,
                    "rating": round(float(item.provider_rating), 1),
                    "count": item.provider_rating_count,
                })
            trakt = _build_trakt_popularity_context(item, route_type)
            if trakt:
                chips.append({"key": "trakt", "label": "Trakt",
                              "rating": trakt["rating"], "count": trakt["rating_count"]})
            imdb = _build_imdb_rating_context(item, route_type)
            if imdb:
                chips.append({"key": "imdb", "label": "IMDb",
                              "rating": imdb["rating"], "count": imdb["rating_count"]})
            mal = _build_mal_rating_context(item, route_type)
            if mal:
                chips.append({"key": "mal", "label": "MyAnimeList",
                              "rating": mal["rating"], "count": mal["rating_count"]})
            critic = _build_opencritic_context(item, route_type)
            if critic:
                chips.append({
                    "key": "opencritic", "label": "OpenCritic", "rating": critic["score"],
                    "count": None, "percentRecommended": critic["percent_recommended"],
                    "tier": critic["tier"], "url": critic["url"],
                })
        graphs = {}
        if route_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
            for key, flags in (("provider", {}), ("trakt", {"use_trakt": True}),
                               ("imdb", {"use_imdb": True})):
                graph = _build_series_graph_data(source, media_id, **flags) \
                    or _build_stored_season_scores_graph(source, media_id, **flags)
                if graph:
                    graphs[key] = graph
        lengths = (
            _build_game_lengths_context(item)
            if item is not None and route_type == MediaTypes.GAME.value else None
        )
        game_lengths = None
        if lengths:
            game_lengths = {
                "available": bool(lengths.get("available")),
                "sourceLabel": lengths.get("source_label") or "",
                "sourceUrl": lengths.get("source_url") or "",
                "cards": [
                    {"label": card["label"], "value": card["value"], "count": card["count"]}
                    for card in lengths.get("cards") or []
                ],
            }
        logos = {
            key: request.build_absolute_uri(static(path))
            for key, path in {
                "provider": _SCORE_LOGOS.get(source),
                **{key: _SCORE_LOGOS[key] for key in ("trakt", "imdb", "mal", "opencritic")},
            }.items()
            if path
        }
        return Response({
            "chips": chips, "graphs": graphs, "gameLengths": game_lengths, "logos": logos,
        })


# /api/v1/media/tv/[source]/[id]/[season]/scores/
class MediaSeasonScoresView(MediaScoresView):
    """Season variant of MediaScoresView."""

    @extend_schema(
        operation_id="retrieveMediaSeasonScores",
        parameters=[MEDIA_TYPE_TV_ONLY_PARAM],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Return the season's stored score chips."""
        return super().get(request, media_type, source, media_id, season_number)


# /api/v1/media/[type]/[source]/[id]/activity-timeline/
class MediaActivityTimelineView(drf_views.APIView):
    """The "Activity History" dialog entries, as the web ``history_modal`` builds them.

    Same ``history_processor`` output (formatted change descriptions per
    record), newest first, grouped by the day of the record in the viewer's
    zone and date format. ``id`` deletes through the ``history`` endpoint.
    """

    @extend_schema(
        operation_id="retrieveMediaActivityTimeline",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id, season_number=None, episode_number=None):
        """Return the viewer's activity entries for one title."""
        from django.utils import timezone

        from app import history_processor
        from app.models import BasicMedia
        from users.templatetags.user_tags import user_date_format, user_datetime_format

        lookup_type = media_type
        if season_number is not None:
            lookup_type = (
                MediaTypes.EPISODE.value if episode_number is not None else MediaTypes.SEASON.value
            )
        medias = list(
            BasicMedia.objects.filter_media(
                request.user, media_id, lookup_type, source,
                season_number=season_number, episode_number=episode_number,
            ).order_by("created_at", "pk")
        )
        entries = []
        for number, media in enumerate(medias, start=1):
            history = media.history.all()
            if lookup_type == MediaTypes.PODCAST.value:
                history = history.filter(end_date__isnull=False)
            entries.extend(
                {
                    "id": entry["id"],
                    "instanceId": entry["instance_id"],
                    "instance": number,
                    "when": entry["date"],
                    "date": user_datetime_format(entry["date"], request.user),
                    "changes": [str(change["description"]) for change in entry["changes"]],
                }
                for entry in history_processor.process_history_entries(
                    history, lookup_type, number, request.user,
                )
            )
        entries.sort(key=lambda row: row["when"], reverse=True)
        days = []
        for row in entries:
            day = timezone.localtime(row["when"]).strftime("%Y-%m-%d")
            if not days or days[-1]["day"] != day:
                days.append({"day": day, "label": user_date_format(row["when"], request.user), "entries": []})
            days[-1]["entries"].append({**row, "when": row["when"].isoformat()})
        return Response({"multipleInstances": len(medias) > 1, "days": days})


# /api/v1/media/tv/[source]/[id]/[season]/activity-timeline/
class MediaSeasonActivityTimelineView(MediaActivityTimelineView):
    """Season variant with a distinct schema operation."""

    @extend_schema(
        operation_id="retrieveMediaSeasonActivityTimeline",
        parameters=[MEDIA_TYPE_TV_ONLY_PARAM],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id, season_number):
        """Return the activity entries of a season."""
        return super().get(request, media_type, source, media_id, season_number)


# /api/v1/media/tv/[source]/[id]/[season]/[episode]/activity-timeline/
class MediaEpisodeActivityTimelineView(MediaActivityTimelineView):
    """Episode variant of MediaActivityTimelineView."""

    @extend_schema(
        operation_id="retrieveMediaEpisodeActivityTimeline",
        parameters=[MEDIA_TYPE_TV_ONLY_PARAM],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, media_type, source, media_id, season_number, episode_number=None):
        """Return the activity entries of a season or episode."""
        return super().get(request, media_type, source, media_id, season_number, episode_number)
