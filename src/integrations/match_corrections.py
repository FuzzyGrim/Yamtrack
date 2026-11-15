"""Preview and apply user-scoped Plex/Trakt match corrections."""

# These errors carry user-facing messages rendered by the correction form.
# ruff: noqa: TRY003, EM101, EM102

from __future__ import annotations

import hashlib
import json
from collections import Counter

from django.db.models import Q

from app.models import (
    TV,
    CollectionEntry,
    DiscoverFeedback,
    Episode,
    Item,
    ItemTag,
    MediaTypes,
    Movie,
    PlaybackProgress,
    ProgressChange,
    Season,
    Sources,
    WatchState,
    WatchStateChange,
)
from app.providers import services
from app.public_errors import PublicMessageError
from integrations.external_references import save_correction
from lists.models import CustomListItem, ListRecommendation


class InvalidMatchCorrectionError(PublicMessageError, ValueError):
    """The source and destination cannot be corrected together."""


class StaleCorrectionPreviewError(PublicMessageError, ValueError):
    """The affected user-owned state changed after preview."""


class MissingEpisodeMappingError(PublicMessageError, ValueError):
    """At least one source episode has not been mapped."""


TRACKABLE_TYPES = {MediaTypes.MOVIE.value, MediaTypes.TV.value}
PAIR_SIZE = 2
SCALAR_FIELDS = (
    "status",
    "score",
    "progress",
    "start_date",
    "end_date",
    "notes",
)


def _json_value(value):
    """Return a JSON-safe model value."""
    return str(value) if value is not None else None


def _user_list_filter(user):
    """Return the lists whose contents this user is allowed to change."""
    return Q(custom_list__owner=user) | Q(custom_list__collaborators=user)


def _episode_queryset(user, item):
    """Return the user's episode watches below a TV Item."""
    return Episode.objects.filter(
        related_season__user=user,
        related_season__related_tv__item=item,
    ).select_related("item", "related_season", "related_season__item")


def _state_payload(user, item):
    """Serialize the mutable user-owned state used for stale-preview checks."""
    def media_rows(queryset):
        """Serialize only concrete fields available on this media model."""
        rows = []
        for media in queryset:
            field_names = {field.name for field in media._meta.concrete_fields}
            rows.append(
                {
                    "id": media.pk,
                    **{
                        field: _json_value(getattr(media, field))
                        for field in SCALAR_FIELDS
                        if field in field_names
                    },
                },
            )
        return rows

    tv_rows = media_rows(TV.objects.filter(user=user, item=item))
    movie_rows = media_rows(Movie.objects.filter(user=user, item=item))
    episodes = list(
        _episode_queryset(user, item).values(
            "id",
            "item_id",
            "related_season_id",
            "end_date",
            "start_date",
            "status",
            "notes",
            "score",
            "dropped",
        ),
    )
    return {
        "item": item.pk,
        "tv": tv_rows,
        "movie": movie_rows,
        "episodes": episodes,
        "plays": list(
            Movie.objects.filter(user=user, item=item)
            .values_list("plays__id", "plays__external_id", "plays__end_date"),
        ),
        "collection": list(
            CollectionEntry.objects.filter(user=user, item=item).values_list("id"),
        ),
        "playback": list(
            PlaybackProgress.objects.filter(user=user, item=item).values_list(
                "id", "position_seconds", "duration_seconds", "completed", "updated_at"
            ),
        ),
        "watch_state": list(
            WatchState.objects.filter(user=user, item=item).values_list(
                "id", "watched", "play_count", "last_watched_at", "updated_at"
            ),
        ),
        "progress_changes": list(
            ProgressChange.objects.filter(user=user, item=item).values_list(
                "id", "sequence", "kind", "position_seconds", "completed"
            ),
        ),
        "feedback": list(
            DiscoverFeedback.objects.filter(user=user, item=item).values_list("id"),
        ),
        "tags": list(
            ItemTag.objects.filter(tag__user=user, item=item).values_list("id"),
        ),
        "list_items": list(
            CustomListItem.objects.filter(_user_list_filter(user), item=item).values_list(
                "id", "custom_list_id", "list_item_id"
            ),
        ),
        "recommendations": list(
            ListRecommendation.objects.filter(
                _user_list_filter(user), item=item
            ).values_list("id", "custom_list_id"),
        ),
    }


def _digest(payload):
    """Hash a preview payload deterministically."""
    encoded = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _source_episodes(user, item):
    """Return required source episode keys and their display data."""
    rows = _episode_queryset(user, item)
    episodes = []
    keys = set()
    for episode in rows:
        if not episode.item:
            continue
        season = episode.related_season.item.season_number
        number = episode.item.episode_number
        if season is None or number is None:
            continue
        key = f"{season}:{number}"
        keys.add(key)
        episodes.append(
            {
                "key": key,
                "season": season,
                "episode": number,
                "title": episode.item.title,
            },
        )
    return keys, episodes


def _destination_sources(source_item):
    """Return the providers a corrected match may point at."""
    if source_item.media_type == MediaTypes.TV.value:
        return {Sources.TMDB.value, Sources.TVDB.value}
    return {Sources.TMDB.value}


def destination_episodes(destination_item):
    """Return the destination show's episodes for the numbering picker."""
    metadata = services.get_media_metadata(
        destination_item.media_type,
        destination_item.media_id,
        destination_item.source,
    ) or {}
    seasons = sorted(
        {
            int(season["season_number"])
            for season in (metadata.get("related") or {}).get("seasons") or []
            if isinstance(season, dict) and season.get("season_number") is not None
        },
    )
    if not seasons:
        return []
    payload = services.get_media_metadata(
        "tv_with_seasons",
        destination_item.media_id,
        destination_item.source,
        seasons,
    ) or {}
    rows = []
    for number in seasons:
        for episode in (payload.get(f"season/{number}") or {}).get("episodes") or []:
            if not isinstance(episode, dict) or episode.get("episode_number") is None:
                continue
            rows.append(
                {
                    "id": f"{number}:{int(episode['episode_number'])}",
                    "code": f"S{int(number):02d}E{int(episode['episode_number']):02d}",
                    "title": episode.get("title") or episode.get("name") or "",
                    "air_date": str(episode.get("air_date") or ""),
                },
            )
    return rows


def suggest_mapping(source_episodes, catalogue):
    """Propose a destination episode for each source episode.

    Returns ``{key: (destination_id, kind)}``; keys with no safe proposal are
    left out. ``matched`` means the same number and the same title,
    ``suggested`` means only a unique title or only the number agrees.
    """
    by_id = {row["id"]: row for row in catalogue}
    titles = {}
    for row in catalogue:
        titles.setdefault(row["title"].casefold(), []).append(row["id"])
    proposals = {}
    for row in source_episodes:
        title = (row["title"] or "").casefold()
        title_matches = titles.get(title, []) if title else []
        if len(title_matches) == 1:
            # An exact, unique title outranks the number: renumbering is the
            # usual reason to be here.
            kind = "matched" if title_matches[0] == row["key"] else "suggested"
            proposals[row["key"]] = (title_matches[0], kind)
        elif row["key"] in by_id:
            proposals[row["key"]] = (row["key"], "suggested")
    return proposals


def _default_mapping(episodes):
    """Propose equal-number episode mappings."""
    return {
        row["key"]: {"season": row["season"], "episode": row["episode"]}
        for row in episodes
    }


def _scalar_conflicts(source, destination):
    """Return scalar fields that have two different meaningful values."""
    field_names = {field.name for field in source._meta.concrete_fields}
    return {
        field: {
            "source": _json_value(getattr(source, field)),
            "destination": _json_value(getattr(destination, field)),
        }
        for field in SCALAR_FIELDS
        if field in field_names
        and getattr(source, field) not in (None, "")
        and getattr(destination, field) not in (None, "")
        and getattr(source, field) != getattr(destination, field)
    }


def preview_match_correction(user, source_item, destination_item, episode_mapping=None):
    """Build a preview and token for moving one user's tracked entry."""
    if source_item.pk == destination_item.pk:
        raise InvalidMatchCorrectionError("Choose a different destination.")
    if source_item.media_type not in TRACKABLE_TYPES:
        raise InvalidMatchCorrectionError("Only movies and TV shows can be corrected.")
    if destination_item.media_type != source_item.media_type:
        raise InvalidMatchCorrectionError("The destination must have the same media type.")
    if destination_item.source not in _destination_sources(source_item):
        raise InvalidMatchCorrectionError(
            "The destination must use verified provider metadata.",
        )

    source_media = (
        Movie.objects.filter(user=user, item=source_item).first()
        if source_item.media_type == MediaTypes.MOVIE.value
        else TV.objects.filter(user=user, item=source_item).first()
    )
    if source_media is None:
        raise InvalidMatchCorrectionError("The source item is not tracked by this user.")
    destination_media = (
        Movie.objects.filter(user=user, item=destination_item).first()
        if source_item.media_type == MediaTypes.MOVIE.value
        else TV.objects.filter(user=user, item=destination_item).first()
    )

    source_payload = _state_payload(user, source_item)
    destination_payload = _state_payload(user, destination_item)
    episodes = []
    required_keys = set()
    mapping = episode_mapping or {}
    if source_item.media_type == MediaTypes.TV.value:
        required_keys, episodes = _source_episodes(user, source_item)
        if not episode_mapping:
            mapping = _default_mapping(episodes)

    # The mapping is chosen after the preview, so only tracked state is digested.
    payload = {
        "source": source_payload,
        "destination": destination_payload,
    }
    return {
        "token": _digest(payload),
        "source": source_item,
        "destination": destination_item,
        "source_media": source_media,
        "destination_media": destination_media,
        "episodes": episodes,
        "required_episode_keys": sorted(required_keys),
        "episode_mapping": mapping,
        "scalar_conflicts": (
            _scalar_conflicts(source_media, destination_media)
            if destination_media
            else {}
        ),
        "counts": {
            "episodes": len(source_payload["episodes"]),
            "movie_plays": len(source_payload["plays"]),
            "collections": len(source_payload["collection"]),
            "playback": len(source_payload["playback"]),
            "watch_state": len(source_payload["watch_state"]),
            "lists": len(source_payload["list_items"]),
            "recommendations": len(source_payload["recommendations"]),
        },
    }


def _mapping_value(mapping, key):
    """Read and validate one episode mapping value."""
    value = mapping.get(key)
    if isinstance(value, dict):
        value = (value.get("season"), value.get("episode"))
    if not isinstance(value, (list, tuple)) or len(value) != PAIR_SIZE:
        raise MissingEpisodeMappingError(f"Episode {key} is not mapped.")
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError) as error:
        raise MissingEpisodeMappingError(f"Episode {key} is not mapped.") from error


def _merge_scalars(source, destination, decisions):
    """Merge user-owned scalar tracking fields, requiring conflict choices."""
    decisions = decisions or {}
    conflicts = _scalar_conflicts(source, destination)
    for field in conflicts:
        choice = decisions.get(field)
        if choice not in {"source", "destination"}:
            raise InvalidMatchCorrectionError(
                f"Choose the {field.replace('_', ' ')} value before applying."
            )
        setattr(destination, field, getattr(source if choice == "source" else destination, field))
    field_names = {field.name for field in destination._meta.concrete_fields}
    for field in SCALAR_FIELDS:
        if field not in field_names:
            continue
        if getattr(destination, field) in (None, "") and getattr(source, field) not in (
            None,
            "",
        ):
            setattr(destination, field, getattr(source, field))
    destination.__class__.objects.filter(pk=destination.pk).update(
        **{
            field: getattr(destination, field)
            for field in field_names & set(SCALAR_FIELDS)
        },
    )


def _move_unique_state(
    model, user, source_item, destination_item, fields, *, clock=None
):
    """Move one user/item singleton, keeping the newer destination row.

    ``clock`` names an event-ordering watermark. Both rows describe the same
    title, so the kept row takes the newer of the two whichever row's values
    win: an event older than one already applied to either row is stale.
    """
    source = model.objects.filter(user=user, item=source_item).first()
    if not source:
        return
    destination = model.objects.filter(user=user, item=destination_item).first()
    if destination is None:
        model.objects.filter(pk=source.pk).update(item=destination_item)
        return
    values = {}
    if (
        getattr(source, "updated_at", None)
        and getattr(destination, "updated_at", None)
        and source.updated_at > destination.updated_at
    ):
        values = {field: getattr(source, field) for field in fields}
    if clock:
        clocks = [
            value
            for value in (getattr(source, clock), getattr(destination, clock))
            if value is not None
        ]
        if clocks:
            values[clock] = max(clocks)
    if values:
        model.objects.filter(pk=destination.pk).update(**values)
    source.delete()


def _move_relations(user, source_item, destination_item):
    """Move only this user's collection, playback, tags, and list state."""
    CollectionEntry.objects.filter(user=user, item=source_item).update(
        item=destination_item
    )
    _move_unique_state(
        PlaybackProgress,
        user,
        source_item,
        destination_item,
        ("position_seconds", "duration_seconds", "completed"),
        clock="last_event_at",
    )
    _move_unique_state(
        WatchState,
        user,
        source_item,
        destination_item,
        ("watched", "play_count", "last_watched_at"),
    )
    ProgressChange.objects.filter(user=user, item=source_item).update(
        item=destination_item
    )
    WatchStateChange.objects.filter(user=user, item=source_item).update(
        item=destination_item
    )

    for feedback in list(
        DiscoverFeedback.objects.filter(user=user, item=source_item),
    ):
        if DiscoverFeedback.objects.filter(
            user=user,
            item=destination_item,
            feedback_type=feedback.feedback_type,
        ).exists():
            feedback.delete()
        else:
            DiscoverFeedback.objects.filter(pk=feedback.pk).update(item=destination_item)

    for tag in list(ItemTag.objects.filter(tag__user=user, item=source_item)):
        if ItemTag.objects.filter(tag_id=tag.tag_id, item=destination_item).exists():
            tag.delete()
        else:
            ItemTag.objects.filter(pk=tag.pk).update(item=destination_item)

    for list_item in list(
        CustomListItem.objects.filter(_user_list_filter(user), item=source_item),
    ):
        if CustomListItem.objects.filter(
            custom_list_id=list_item.custom_list_id,
            item=destination_item,
        ).exists():
            list_item.delete()
        else:
            CustomListItem.objects.filter(pk=list_item.pk).update(item=destination_item)

    for recommendation in list(
        ListRecommendation.objects.filter(
            _user_list_filter(user), item=source_item
        ),
    ):
        if ListRecommendation.objects.filter(
            custom_list_id=recommendation.custom_list_id,
            item=destination_item,
        ).exists():
            recommendation.delete()
        else:
            ListRecommendation.objects.filter(pk=recommendation.pk).update(
                item=destination_item
            )


def _move_movie(user, source_item, destination_item, decisions):
    """Move movie tracking and deduplicate only identified plays."""
    source = Movie.objects.filter(user=user, item=source_item).first()
    if not source:
        raise InvalidMatchCorrectionError("The source movie is not tracked by this user.")
    destination = Movie.objects.filter(user=user, item=destination_item).first()
    if destination:
        _merge_scalars(source, destination, decisions)
        destination_external_ids = set(
            destination.plays.exclude(external_id__isnull=True)
            .exclude(external_id="")
            .values_list("external_id", flat=True),
        )
        for play in list(source.plays.all()):
            if play.external_id and play.external_id in destination_external_ids:
                play.delete()
            else:
                play.__class__.objects.filter(pk=play.pk).update(movie=destination)
        source.delete()
    else:
        Movie.objects.filter(pk=source.pk).update(item=destination_item)


def _destination_season_item(destination_item, season_number, template):
    """Return the destination show's season Item, creating it from the source's."""
    season_item = Item.objects.filter(
        media_id=destination_item.media_id,
        source=destination_item.source,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
    ).first()
    if season_item is None:
        season_item = Item.objects.create(
            media_id=destination_item.media_id,
            source=destination_item.source,
            media_type=MediaTypes.SEASON.value,
            library_media_type=template.library_media_type,
            title=template.title,
            original_title=template.original_title,
            localized_title=template.localized_title,
            image=template.image,
            season_number=season_number,
        )
    return season_item


def _move_tv(user, source_item, destination_item, mapping, decisions):
    """Move TV tracking while preserving every episode watch row."""
    source = TV.objects.filter(user=user, item=source_item).first()
    if not source:
        raise InvalidMatchCorrectionError("The source TV show is not tracked by this user.")
    # Read the source's seasons before the show can be repointed below: with no
    # destination row the source row becomes the destination, and these seasons
    # would then look like they already belong to it.
    source_seasons = list(
        Season.objects.filter(user=user, related_tv=source).select_related("item"),
    )
    destination = TV.objects.filter(user=user, item=destination_item).first()
    if destination:
        _merge_scalars(source, destination, decisions)
    else:
        TV.objects.filter(pk=source.pk).update(item=destination_item)
        destination = TV.objects.get(pk=source.pk)

    destination_seasons = {
        season.item.season_number: season
        for season in Season.objects.filter(user=user, related_tv=destination)
        .exclude(pk__in=[season.pk for season in source_seasons])
        .select_related("item")
    }
    for season in source_seasons:
        source_season = season.item.season_number
        destination_season_number = source_season
        if source_season is not None:
            # The season row follows where most of its episodes go; an episode
            # mapped elsewhere (a special, say) gets that season's row below.
            targets = Counter(
                _mapping_value(mapping, key)[0]
                for key in mapping
                if key.startswith(f"{source_season}:")
            )
            if targets:
                destination_season_number = targets.most_common(1)[0][0]
        destination_season = destination_seasons.get(destination_season_number)
        if destination_season is None:
            Season.objects.filter(pk=season.pk).update(
                item=_destination_season_item(
                    destination_item, destination_season_number, season.item
                ),
                related_tv=destination,
            )
            destination_season = Season.objects.get(pk=season.pk)
            destination_seasons[destination_season_number] = destination_season

        for episode in list(Episode.objects.filter(related_season=season)):
            if not episode.item:
                Episode.objects.filter(pk=episode.pk).update(related_season=destination_season)
                continue
            key = f"{source_season}:{episode.item.episode_number}"
            target_season_number, target_episode_number = _mapping_value(mapping, key)
            target_season = destination_seasons.get(target_season_number)
            if target_season is None:
                target_season = Season(
                    user=user,
                    related_tv=destination,
                    item=_destination_season_item(
                        destination_item, target_season_number, season.item
                    ),
                    status=season.status,
                )
                # bulk_create skips Season.save(), whose status cascade would
                # rewrite the show while it is half moved.
                Season.objects.bulk_create([target_season])
                destination_seasons[target_season_number] = target_season
            target_item = Item.objects.filter(
                media_id=destination_item.media_id,
                source=destination_item.source,
                media_type=MediaTypes.EPISODE.value,
                season_number=target_season_number,
                episode_number=target_episode_number,
            ).first()
            if target_item is None:
                target_item = Item.objects.create(
                    media_id=destination_item.media_id,
                    source=destination_item.source,
                    media_type=MediaTypes.EPISODE.value,
                    library_media_type=episode.item.library_media_type,
                    title=episode.item.title,
                    original_title=episode.item.original_title,
                    localized_title=episode.item.localized_title,
                    image=episode.item.image,
                    season_number=target_season_number,
                    episode_number=target_episode_number,
                )
            Episode.objects.filter(pk=episode.pk).update(
                related_season=target_season,
                item=target_item,
            )
        if season.pk != destination_season.pk:
            season.delete()

    if source.pk != destination.pk:
        source.delete()


def apply_match_correction(
    user,
    source_item_id,
    destination_item_id,
    preview_token,
    *,
    episode_mapping=None,
    decisions=None,
    reference_ids=(),
    note="",
):
    """Atomically rehome one user's data and save its future-import decision."""
    from app.services.sync_lock import invalidate_contexts, state_transaction

    with state_transaction(user.pk) as sequence:
        invalidate_contexts(sequence)
        source_item, destination_item = (
            Item.objects.select_for_update().get(pk=source_item_id),
            Item.objects.select_for_update().get(pk=destination_item_id),
        )
        preview = preview_match_correction(
            user,
            source_item,
            destination_item,
            episode_mapping=episode_mapping,
        )
        if preview["token"] != preview_token:
            raise StaleCorrectionPreviewError("The preview is out of date; refresh it.")
        mapping = preview["episode_mapping"]
        missing = set(preview["required_episode_keys"]) - set(mapping)
        if missing:
            raise MissingEpisodeMappingError(
                "Map every affected episode before applying the correction."
            )

        if source_item.media_type == MediaTypes.MOVIE.value:
            _move_movie(user, source_item, destination_item, decisions)
        else:
            from app.services import library_migration

            _move_tv(user, source_item, destination_item, mapping, decisions)
            # The same follow-ups as a provider move: the rehomed show needs its
            # calendar (next episode, progress) and caches rebuilt.
            library_migration._schedule_cache_reconcile(user, destination_item)
        _move_relations(user, source_item, destination_item)

        if reference_ids:
            save_correction(
                reference_ids,
                user=user,
                destination=destination_item,
                episode_mapping=mapping,
                note=note,
            )
        return destination_item
