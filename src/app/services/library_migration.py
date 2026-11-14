"""Safe, user-scoped migration between the TV and Anime libraries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

import requests
from django.db import IntegrityError, transaction

from app import cache_utils, history_cache, signals
from app.models import (
    TV,
    Anime,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
)
from app.providers import services, tmdb, tvdb
from app.services import anime_migration, metadata_resolution
from app.services.tracking_hydration import ensure_item_metadata
from app.signals import suppress_media_change_side_effects
from integrations import anime_mapping


class LibraryMigrationError(Exception):
    """Raised when a TV/Anime library move cannot be completed safely."""


def _migration_error(message: str) -> NoReturn:
    raise LibraryMigrationError(message)


@dataclass(frozen=True, slots=True)
class LibraryMigrationPlan:
    """Read-only provider and coordinate data used by the write phase."""

    source_item_id: int
    target_media_type: str
    target_source: str
    target_media_id: str
    target_bucket: str
    target_metadata: dict
    target_seasons: dict[int, dict]
    source_shape: str


def library_bucket(item: Item) -> str:
    """Return the user's library bucket for an item."""
    return item.library_media_type or item.media_type


def destination_media_type(item: Item) -> str:
    """Return the route media type for the opposite library bucket."""
    bucket = library_bucket(item)
    if bucket == MediaTypes.TV.value:
        return MediaTypes.ANIME.value
    if bucket == MediaTypes.ANIME.value:
        return MediaTypes.TV.value
    _migration_error("Only TV Shows and Anime entries can be moved.")


def _source_shape(item: Item) -> str:
    if item.media_type == MediaTypes.TV.value:
        return "grouped"
    if (
        item.media_type == MediaTypes.ANIME.value
        and item.source == Sources.MAL.value
    ):
        return "flat"
    _migration_error("This tracking entry is not a movable show.")


def _owned_source_tracker(user, item: Item):
    if item.media_type == MediaTypes.TV.value:
        return TV.objects.filter(user=user, item=item).first()
    if item.media_type == MediaTypes.ANIME.value:
        return Anime.all_objects.filter(
            user=user,
            item=item,
            migrated_to_item__isnull=True,
        ).first()
    return None


def _destination_provider_for_item(user, item: Item, target_media_type: str) -> str:
    """Choose a usable destination provider for the move search."""
    default_source = metadata_resolution.metadata_default_source(
        user,
        target_media_type,
    )
    if not (
        item.media_type == MediaTypes.ANIME.value
        and item.source == Sources.MAL.value
        and target_media_type == MediaTypes.TV.value
    ):
        return default_source

    available_sources = [
        choice.value
        for choice in metadata_resolution.available_metadata_sources(
            target_media_type,
        )
    ]
    provider_order = [
        default_source,
        *(provider for provider in available_sources if provider != default_source),
    ]
    for provider in provider_order:
        if provider not in metadata_resolution.GROUPED_ANIME_PROVIDERS:
            continue
        if anime_mapping.resolve_provider_series_id(item.media_id, provider):
            return provider
    return default_source


def get_move_context(user, item: Item) -> dict | None:
    """Return the modal/search context when a user can move this item."""
    if not getattr(user, "tv_enabled", False) or not getattr(
        user,
        "anime_enabled",
        False,
    ):
        return None
    try:
        target_media_type = destination_media_type(item)
        _source_shape(item)
    except LibraryMigrationError:
        return None
    if _owned_source_tracker(user, item) is None:
        return None
    target_source = _destination_provider_for_item(
        user,
        item,
        target_media_type,
    )
    allowed_sources = {
        choice.value
        for choice in metadata_resolution.available_metadata_sources(target_media_type)
    }
    if target_source not in allowed_sources:
        return None
    return {
        "target_media_type": target_media_type,
        "target_source": target_source,
        "target_label": (
            "Anime" if target_media_type == MediaTypes.ANIME.value else "TV Shows"
        ),
        "target_source_label": metadata_resolution.metadata_provider_label(
            target_source,
        ),
    }


def _target_provider_media_type(target_media_type: str, target_source: str) -> str:
    if target_media_type == MediaTypes.ANIME.value and target_source in {
        Sources.TMDB.value,
        Sources.TVDB.value,
    }:
        return MediaTypes.TV.value
    return target_media_type


def _episode_map(season_metadata: dict) -> dict[int, dict]:
    result = {}
    for episode in season_metadata.get("episodes") or []:
        if not isinstance(episode, Mapping):
            continue
        try:
            result[int(episode["episode_number"])] = episode
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _source_coordinates(user, item: Item) -> tuple[list[Season], list[Episode]]:
    tv = TV.objects.filter(user=user, item=item).first()
    if tv is None:
        _migration_error("The source show is no longer tracked.")
    seasons = list(
        Season.objects.filter(related_tv=tv)
        .select_related("item")
        .order_by("item__season_number", "id")
    )
    episodes = list(
        Episode.objects.filter(related_season__in=seasons)
        .select_related("item", "related_season__item")
        .order_by("id")
    )
    return seasons, episodes


def _load_target_seasons(
    target_media_id: str,
    target_source: str,
    source_seasons: list[Season],
) -> dict[int, dict]:
    season_numbers = []
    for season in source_seasons:
        number = season.item.season_number
        if number is None:
            _migration_error(
                "A source season has no season coordinate; nothing was changed."
            )
        season_numbers.append(int(number))

    if not season_numbers:
        return {}

    try:
        payload = services.get_media_metadata(
            "tv_with_seasons",
            target_media_id,
            target_source,
            sorted(set(season_numbers)),
        ) or {}
    except services.ProviderAPIError:
        _migration_error(
            "The destination seasons could not be loaded; nothing was changed."
        )
    result = {}
    for number in set(season_numbers):
        metadata = dict(payload.get(f"season/{number}") or {})
        if not metadata:
            _migration_error(
                f"The destination has no matching season {number}; nothing was changed."
            )
        result[number] = metadata
    return result


def preflight_library_move(
    user,
    source_item: Item,
    target_media_type: str,
    target_source: str,
    target_media_id: str,
) -> LibraryMigrationPlan:
    """Validate ownership, provider identity, and coordinates without writes."""
    if not getattr(user, "tv_enabled", False) or not getattr(
        user,
        "anime_enabled",
        False,
    ):
        _migration_error(
            "Enable both TV Shows and Anime before moving a title."
        )

    target_media_type = (target_media_type or "").strip()
    target_source = (target_source or "").strip()
    target_media_id = (target_media_id or "").strip()
    if target_media_type not in {MediaTypes.TV.value, MediaTypes.ANIME.value}:
        _migration_error("Choose TV Shows or Anime as the destination.")
    if not target_media_id:
        _migration_error("Choose a destination title.")

    expected_media_type = destination_media_type(source_item)
    if target_media_type != expected_media_type:
        _migration_error("The destination library does not match the source.")

    allowed_sources = {
        choice.value
        for choice in metadata_resolution.available_metadata_sources(target_media_type)
    }
    if target_source not in allowed_sources:
        _migration_error("That destination provider is not available.")

    source_shape = _source_shape(source_item)
    if _owned_source_tracker(user, source_item) is None:
        _migration_error("You do not own the source tracking entry.")

    try:
        target_metadata = services.get_media_metadata(
            target_media_type,
            target_media_id,
            target_source,
            language=metadata_resolution.metadata_language_default(user),
        )
    except services.ProviderAPIError:
        _migration_error("The destination title could not be loaded.")
    if not target_metadata:
        _migration_error("The destination title could not be loaded.")
    if str(target_metadata.get("media_id") or target_media_id) != target_media_id:
        _migration_error("The destination ID did not match the selected title.")

    target_bucket = target_media_type
    target_seasons = {}
    if source_shape == "grouped":
        source_seasons, source_episodes = _source_coordinates(user, source_item)
        if target_source == Sources.MAL.value:
            source_tv = TV.objects.get(user=user, item=source_item)
            if source_seasons or source_episodes or source_tv.progress:
                _migration_error(
                    "This title has season, episode, or progress state that cannot be represented safely as flat Anime."
                )
        else:
            target_seasons = _load_target_seasons(
                target_media_id,
                target_source,
                source_seasons,
            )
            episodes_by_season = {
                int(season_number): _episode_map(metadata)
                for season_number, metadata in target_seasons.items()
            }
            for episode in source_episodes:
                season_number = episode.related_season.item.season_number
                episode_number = getattr(episode.item, "episode_number", None)
                if season_number is None or episode_number is None:
                    _migration_error(
                        "An episode is missing season or episode coordinates; nothing was changed."
                    )
                if int(episode_number) not in episodes_by_season[int(season_number)]:
                    _migration_error(
                        f"The destination has no matching episode S{int(season_number):02d}E{int(episode_number):02d}; nothing was changed."
                    )
    else:
        if target_source not in {Sources.TMDB.value, Sources.TVDB.value}:
            _migration_error("Flat Anime can only move to grouped TV metadata.")
        provider_series_id = anime_mapping.resolve_provider_series_id(
            source_item.media_id,
            target_source,
        )
        if str(provider_series_id or "") != target_media_id:
            _migration_error(
                "The selected destination is not the verified provider match for this Anime."
            )

    return LibraryMigrationPlan(
        source_item_id=source_item.id,
        target_media_type=target_media_type,
        target_source=target_source,
        target_media_id=target_media_id,
        target_bucket=target_bucket,
        target_metadata=dict(target_metadata),
        target_seasons=target_seasons,
        source_shape=source_shape,
    )


def _target_item(
    user,
    plan: LibraryMigrationPlan,
    *,
    media_type: str | None = None,
    library_media_type: str | None = None,
    season_number: int | None = None,
    fallback_title: str = "",
    metadata: dict | None = None,
) -> Item:
    resolved_media_type = media_type or plan.target_media_type
    resolved_bucket = library_media_type or plan.target_bucket
    tracking_media_type = metadata_resolution.get_tracking_media_type(
        resolved_media_type,
        source=plan.target_source,
    )
    Item.objects.select_for_update().filter(
        media_id=plan.target_media_id,
        source=plan.target_source,
        media_type=tracking_media_type,
        library_media_type=resolved_bucket,
        season_number=season_number,
        episode_number=None,
    ).first()
    return ensure_item_metadata(
        user,
        resolved_media_type,
        plan.target_media_id,
        plan.target_source,
        season_number=season_number,
        identity_media_type=(
            MediaTypes.TV.value
            if plan.target_source in {Sources.TMDB.value, Sources.TVDB.value}
            else None
        ),
        library_media_type=resolved_bucket,
        fallback_title=fallback_title,
        prefetched_metadata=metadata or plan.target_metadata,
        provider_link_retry_max_retries=0,
    ).item


def _season_item(plan: LibraryMigrationPlan, season_number: int, metadata: dict) -> Item:
    defaults = Item.title_fields_from_metadata(metadata, fallback_title="")
    item, _created = Item.objects.get_or_create(
        media_id=plan.target_media_id,
        source=plan.target_source,
        media_type=MediaTypes.SEASON.value,
        library_media_type=plan.target_bucket,
        season_number=season_number,
        defaults=defaults,
    )
    return Item.objects.select_for_update().get(pk=item.pk)


def _episode_item(
    plan: LibraryMigrationPlan,
    season_number: int,
    episode_number: int,
    metadata: dict,
    fallback_title: str,
) -> Item:
    item, _created = Item.objects.get_or_create(
        media_id=plan.target_media_id,
        source=plan.target_source,
        media_type=MediaTypes.EPISODE.value,
        library_media_type=plan.target_bucket,
        season_number=season_number,
        episode_number=episode_number,
        defaults=Item.title_fields_from_episode_metadata(
            metadata,
            fallback_title=fallback_title,
        ),
    )
    return Item.objects.select_for_update().get(pk=item.pk)


def _user_relation_filter(relation, user_id: int) -> dict[str, Any] | None:
    model = relation.related_model
    names = {field.name for field in model._meta.get_fields()}
    if "user" in names:
        return {"user_id": user_id}
    if "tag" in names:
        return {"tag__user_id": user_id}
    if "custom_list" in names:
        return {"custom_list__owner_id": user_id}
    if "recommended_by" in names:
        return {"recommended_by_id": user_id}
    return None


def _move_owned_relations(source_item: Item, target_item: Item, user_id: int) -> None:
    """Move current-user item relations, keeping shared metadata untouched."""
    if source_item.pk == target_item.pk:
        return
    for relation in Item._meta.related_objects:
        field = relation.field
        if relation.many_to_many or field.name != "item":
            continue
        model = relation.related_model
        if model in {TV, Season, Episode, Anime}:
            continue
        filter_kwargs = _user_relation_filter(relation, user_id)
        if filter_kwargs is None:
            continue
        queryset = model._base_manager.filter(
            **{field.attname: source_item.id, **filter_kwargs},
        )
        for related in list(queryset):
            setattr(related, field.name, target_item)
            try:
                with transaction.atomic():
                    related.save(update_fields=[field.attname])
            except IntegrityError:
                related.delete()


def _move_grouped(user, source_item: Item, plan: LibraryMigrationPlan) -> Item:
    source_tv = TV.objects.select_for_update().get(user=user, item=source_item)
    source_seasons = list(
        Season.objects.filter(related_tv=source_tv)
        .select_related("item")
        .order_by("id")
    )
    source_episodes = list(
        Episode.objects.filter(related_season__in=source_seasons)
        .select_related("item", "related_season__item")
        .order_by("id")
    )
    target_item = _target_item(
        user,
        plan,
        media_type=_target_provider_media_type(
            plan.target_media_type,
            plan.target_source,
        ),
        library_media_type=plan.target_bucket,
    )
    target_item = Item.objects.select_for_update().get(pk=target_item.pk)
    target_tv = TV.objects.select_for_update().filter(user=user, item=target_item).first()
    destination_fields = None
    if target_tv is not None:
        destination_fields = {
            field.name: getattr(target_tv, field.name)
            for field in TV._meta.concrete_fields
            if field.name not in {"id", "item", "user", "created_at"}
        }
    if target_tv is None:
        source_tv.item = target_item
        source_tv.save(update_fields=["item_id"])
        target_tv = source_tv

    _move_owned_relations(source_item, target_item, user.id)
    for source_season in source_seasons:
        season_number = int(source_season.item.season_number)
        source_season_item = source_season.item
        metadata = plan.target_seasons[season_number]
        if not metadata:
            # The preview only lets an empty season through when the
            # destination lacks it; there is nowhere to move it to.
            if Episode.objects.filter(related_season=source_season).exists():
                _migration_error(
                    f"The destination has no matching season {season_number}; nothing was changed."
                )
            source_season.delete()
            continue
        destination_season_item = _season_item(plan, season_number, metadata)
        destination_season = Season.objects.filter(
            user=user,
            related_tv=target_tv,
            item=destination_season_item,
        ).first()
        if destination_season is None:
            source_season.item = destination_season_item
            source_season.related_tv = target_tv
            source_season.save(update_fields=["item_id", "related_tv_id"])
            destination_season = source_season
        else:
            Episode.objects.filter(related_season=source_season).update(
                related_season=destination_season,
            )
            source_season.delete()
        _move_owned_relations(
            source_season_item,
            destination_season_item,
            user.id,
        )

    if target_tv.pk != source_tv.pk:
        source_tv.delete()
        for field_name, value in (destination_fields or {}).items():
            setattr(target_tv, field_name, value)
        if destination_fields:
            target_tv.save_base(update_fields=list(destination_fields))

    for episode in source_episodes:
        season_number = int(episode.related_season.item.season_number)
        episode_number = int(episode.item.episode_number)
        season_metadata = plan.target_seasons.get(season_number)
        if season_metadata is None:
            _migration_error(
                f"The destination has no matching season {season_number}; nothing was changed."
            )
        episode_metadata = _episode_map(season_metadata).get(episode_number)
        if episode_metadata is None:
            _migration_error(
                f"The destination has no matching episode S{int(season_number):02d}E{int(episode_number):02d}; nothing was changed."
            )
        destination_episode_item = _episode_item(
            plan,
            season_number,
            episode_number,
            episode_metadata,
            target_item.title,
        )
        source_episode_item = episode.item
        Episode.objects.filter(pk=episode.pk).update(item=destination_episode_item)
        _move_owned_relations(source_episode_item, destination_episode_item, user.id)

    return target_item


def _move_grouped_to_flat(user, source_item: Item, plan: LibraryMigrationPlan) -> Item:
    source_tv = TV.objects.select_for_update().get(user=user, item=source_item)
    target_item = _target_item(
        user,
        plan,
        media_type=MediaTypes.ANIME.value,
        library_media_type=MediaTypes.ANIME.value,
    )
    target_item = Item.objects.select_for_update().get(pk=target_item.pk)
    target_anime = Anime.all_objects.select_for_update().filter(
        user=user,
        item=target_item,
        migrated_to_item__isnull=True,
    ).first()
    if target_anime is None:
        target_anime = Anime(
            user=user,
            item=target_item,
            score=source_tv.score,
            status=source_tv.status,
            notes=source_tv.notes,
            start_date=source_tv.start_date,
            end_date=source_tv.end_date,
            progress=0,
        )
        target_anime.save_base()
    _move_owned_relations(source_item, target_item, user.id)
    source_tv.delete()
    return target_item


def migrate_library_item(
    user,
    source_item: Item,
    target_media_type: str,
    target_source: str,
    target_media_id: str,
) -> Item:
    """Preflight and atomically move one user's tracked show."""
    plan = preflight_library_move(
        user,
        source_item,
        target_media_type,
        target_source,
        target_media_id,
    )

    # The flat-anime preflight reads provider metadata. Run it before the
    # transaction: SQLite has one writer, and a slow provider response must not
    # hold the write lock for the whole move.
    flat_preflight = None
    if plan.source_shape == "flat":
        try:
            flat_preflight = anime_migration.preflight_flat_anime_to_grouped(
                user,
                source_item,
                plan.target_source,
            )
        except services.ProviderAPIError:
            _migration_error(
                "The verified Anime mapping could not be loaded; nothing was changed."
            )
        except anime_migration.AnimeMigrationError as error:
            _migration_error(str(error))
        if flat_preflight.provider_series_id != plan.target_media_id:
            _migration_error("The verified Anime mapping changed; retry the move.")

    with transaction.atomic(), suppress_media_change_side_effects():
        source_item = Item.objects.select_for_update().get(pk=plan.source_item_id)
        if _owned_source_tracker(user, source_item) is None:
            _migration_error("The source tracking entry changed; retry the move.")

        if plan.source_shape == "flat":
            preflight = flat_preflight
            try:
                result = anime_migration.persist_flat_anime_migration(preflight)
            except anime_migration.AnimeMigrationError as error:
                _migration_error(str(error))
            target_item = result.grouped_tv.item
            if plan.target_bucket == MediaTypes.TV.value:
                grouped_target_seasons = {
                    int(entry.season_number): dict(entry.season_metadata)
                    for entry in preflight.entries
                }
                grouped_plan = LibraryMigrationPlan(
                    source_item_id=target_item.id,
                    target_media_type=MediaTypes.TV.value,
                    target_source=plan.target_source,
                    target_media_id=plan.target_media_id,
                    target_bucket=MediaTypes.TV.value,
                    target_metadata=dict(preflight.series_metadata),
                    target_seasons=grouped_target_seasons,
                    source_shape="grouped",
                )
                target_item = _move_grouped(user, target_item, grouped_plan)
        elif plan.target_source == Sources.MAL.value:
            target_item = _move_grouped_to_flat(user, source_item, plan)
        else:
            target_item = _move_grouped(user, source_item, plan)

        _schedule_cache_reconcile(user, target_item)
        return target_item


def _schedule_cache_reconcile(user, target_item: Item) -> None:
    history_days = {
        day
        for episode in Episode.objects.filter(
            related_season__related_tv__user=user,
            related_season__related_tv__item=target_item,
        ).only("end_date")
        if (day := history_cache.history_day_key(episode.end_date))
    }
    transaction.on_commit(
        lambda: _reconcile_caches(user.id, history_days, target_item.id),
    )


def _reconcile_caches(user_id: int, history_days: set[str], target_item_id: int) -> None:
    cache_utils.clear_library_view_caches(
        user_id,
        media_types=(MediaTypes.EPISODE.value,),
    )
    if history_days:
        signals._invalidate_episode_history_changes({user_id: list(history_days)})
    # The move suppresses media side effects, so the target's seasons have no
    # release events yet. Next-episode, Check-in and upcoming all read those.
    import events.tasks

    events.tasks.reload_calendar.apply_async(
        kwargs={"item_ids": [target_item_id]},
        countdown=3,
    )


@dataclass(frozen=True, slots=True)
class TvProviderSwitch:
    """What moving one TV show between TMDB and TVDB would do (read-only)."""

    target_source: str
    target_label: str
    target_media_id: str
    target_title: str
    seasons: int
    episodes: int
    missing: list[str]
    plan: LibraryMigrationPlan | None


def tv_provider_switch_target(user, item: Item) -> str | None:
    """Return the other TV provider when this user can move the show there."""
    if (
        item.media_type != MediaTypes.TV.value
        or item.source not in {Sources.TMDB.value, Sources.TVDB.value}
        or library_bucket(item) != MediaTypes.TV.value
        or _owned_source_tracker(user, item) is None
    ):
        return None
    target_source = (
        Sources.TVDB.value if item.source == Sources.TMDB.value else Sources.TMDB.value
    )
    if not metadata_resolution.provider_is_enabled(target_source, user):
        return None
    return target_source


def _switch_target_media_id(item: Item, target_source: str) -> str | None:
    """Return the verified id of a show on its other provider, if one is known."""
    media_id = metadata_resolution.resolve_provider_media_id(
        item,
        target_source,
        route_media_type=MediaTypes.TV.value,
        persist_links=False,
    )
    if media_id:
        return media_id
    # Nothing stored yet: ask the providers, the same lookup the nightly
    # TMDB -> TVDB migration uses.
    try:
        if target_source == Sources.TVDB.value:
            found = tmdb.resolve_tvdb_id_for_tmdb_show(item.media_id)
        else:
            found = tvdb.series_tmdb_id(item.media_id)
    except (services.ProviderAPIError, requests.RequestException):
        return None
    return str(found) if found else None


def preview_tv_provider_switch(user, item: Item) -> TvProviderSwitch:
    """Check, without writing, whether a show can move to its other TV provider.

    Every tracked season and episode must exist on the destination; anything
    that does not is listed in ``missing`` and ``plan`` stays ``None`` so the
    move cannot be applied.
    """
    target_source = tv_provider_switch_target(user, item)
    if target_source is None:
        _migration_error("This show cannot be moved to another provider.")
    target_label = metadata_resolution.metadata_provider_label(target_source)

    target_media_id = _switch_target_media_id(item, target_source)
    if not target_media_id:
        _migration_error(f"No matching {target_label} title was found for this show.")

    source_seasons, source_episodes = _source_coordinates(user, item)
    season_numbers = []
    for season in source_seasons:
        if season.item.season_number is None:
            _migration_error(
                "A source season has no season coordinate; nothing was changed."
            )
        season_numbers.append(int(season.item.season_number))
    season_numbers = sorted(set(season_numbers))

    try:
        target_metadata = services.get_media_metadata(
            MediaTypes.TV.value,
            target_media_id,
            target_source,
            language=metadata_resolution.metadata_language_default(user),
        )
        payload = (
            services.get_media_metadata(
                "tv_with_seasons",
                target_media_id,
                target_source,
                season_numbers,
            )
            if season_numbers
            else {}
        ) or {}
    except services.ProviderAPIError:
        _migration_error(f"{target_label} could not be reached; nothing was changed.")
    if not target_metadata:
        _migration_error(f"The {target_label} title could not be loaded.")

    target_seasons = {
        number: dict(payload.get(f"season/{number}") or {})
        for number in season_numbers
    }
    # A tracked season with no plays has nothing to lose: one the destination
    # lacks (an announced season, say) is dropped by the move, not a blocker.
    played_seasons = {
        int(episode.related_season.item.season_number)
        for episode in source_episodes
        if episode.related_season.item.season_number is not None
    }
    missing = [
        f"Season {number}"
        for number, meta in target_seasons.items()
        if not meta and number in played_seasons
    ]
    episodes_by_season = {
        number: _episode_map(meta) for number, meta in target_seasons.items() if meta
    }
    for episode in source_episodes:
        season_number = episode.related_season.item.season_number
        episode_number = episode.item.episode_number
        if season_number is None or episode_number is None:
            _migration_error(
                "An episode is missing season or episode coordinates; nothing was changed."
            )
        known = episodes_by_season.get(int(season_number))
        if known is not None and int(episode_number) not in known:
            missing.append(f"S{int(season_number):02d}E{int(episode_number):02d}")

    plan = None
    if not missing:
        plan = LibraryMigrationPlan(
            source_item_id=item.id,
            target_media_type=MediaTypes.TV.value,
            target_source=target_source,
            target_media_id=target_media_id,
            target_bucket=MediaTypes.TV.value,
            target_metadata=dict(target_metadata),
            target_seasons=target_seasons,
            source_shape="grouped",
        )
    return TvProviderSwitch(
        target_source=target_source,
        target_label=target_label,
        target_media_id=target_media_id,
        target_title=target_metadata.get("title") or item.title,
        seasons=len(source_seasons),
        episodes=len(source_episodes),
        missing=missing,
        plan=plan,
    )


def switch_tv_provider(user, source_item: Item) -> Item:
    """Move one user's tracked show to its other TV provider, all or nothing.

    Only this user's tracking moves; the shared provider rows (and other users'
    tracking of them) are left alone.
    """
    switch = preview_tv_provider_switch(user, source_item)
    if switch.plan is None:
        _migration_error(
            "Some tracked episodes do not exist on "
            f"{switch.target_label}; nothing was changed."
        )
    with transaction.atomic(), suppress_media_change_side_effects():
        source_item = Item.objects.select_for_update().get(pk=switch.plan.source_item_id)
        if _owned_source_tracker(user, source_item) is None:
            _migration_error("The source tracking entry changed; retry the move.")
        target_item = _move_grouped(user, source_item, switch.plan)
        _schedule_cache_reconcile(user, target_item)
        return target_item


def tv_items_to_move(user, target_source: str):
    """Return this user's tracked TV shows that sit on the other TMDB/TVDB provider."""
    other_source = (
        Sources.TMDB.value if target_source == Sources.TVDB.value else Sources.TVDB.value
    )
    return (
        Item.objects.filter(
            media_type=MediaTypes.TV.value,
            source=other_source,
            tv__user=user,
        )
        .exclude(library_media_type=MediaTypes.ANIME.value)
        .distinct()
        .order_by("id")
    )
