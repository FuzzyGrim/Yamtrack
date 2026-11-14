"""Which tracker rows put an ``Item`` in a user's library for a media type.

The engine queries ``Item`` rows and reaches tracker state through correlated
subqueries, so one item is one candidate however many tracker rows (repeat
viewings) it has. This module is the single place that knows how each media
type's tracker model is reached: the owner path, the status path, and the
anime-library routing that lets TV-tracked anime appear in the Anime library.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps
from django.db import models
from django.db.models import Case, Exists, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Cast, Concat

from app.library_query.spec import ROUTING_MODEL
from app.models.choices import MediaTypes, Sources, Status
from app.services import metadata_resolution


@dataclass(frozen=True)
class TrackerSource:
    """One tracker model feeding a library, and the items it may contribute."""

    model: type[models.Model]
    user_lookup: str
    status_field: str
    item_q: Q

    @property
    def is_episode(self) -> bool:
        """Return whether rows hang off a season instead of a user."""
        return self.user_lookup != "user"

    def rows(self, user):
        """Return every tracker row the user owns in this model."""
        return self.model.objects.filter(**{self.user_lookup: user})

    def item_rows(self, user, outer_ref: str = "pk"):
        """Return the user's rows for the outer item (a correlated subquery).

        The owner is compared as ``owner + 0`` on purpose. Without table
        statistics SQLite otherwise picks the (user, created_at) index to
        satisfy a subquery's ORDER BY and walks every row the user owns, once
        per item; an expression cannot use an index, so the lookup goes
        through ``item_id`` and touches only the item's own rows.
        """
        user_id = getattr(user, "pk", user)
        return (
            self.model.objects.filter(item_id=OuterRef(outer_ref))
            .alias(_owner=F(self.user_lookup) + 0)
            .filter(_owner=user_id)
        )

    def item_ids(self, user, row_q: Q | None = None):
        """Return the ids of items with a user's row matching ``row_q``.

        Uncorrelated on purpose: ``pk IN (this)`` is evaluated once from the
        tracker table's user index, where ``EXISTS`` correlated to each item
        would make the database walk every user's items.
        """
        rows = self.rows(user)
        if row_q is not None:
            rows = rows.filter(row_q)
        return rows.values("item_id")

    def has_field(self, name: str) -> bool:
        """Return whether rows store ``name`` (TV derives dates from seasons)."""
        return any(field.attname == name for field in self.model._meta.concrete_fields)

    def activity(self):
        """Return the expression that orders rows by most recent activity."""
        whens = [
            When(**{f"{name}__isnull": False}, then=F(name))
            for name in (("end_date", "start_date") if self.is_episode else ("end_date", "progressed_at"))
            if self.has_field(name)
        ]
        if not whens:
            return F("created_at")
        return Case(*whens, default=F("created_at"), output_field=models.DateTimeField())


def _source(media_type: str, item_q: Q | None = None) -> TrackerSource:
    model = apps.get_model("app", media_type)
    if media_type == MediaTypes.EPISODE.value:
        return TrackerSource(
            model=model,
            user_lookup="related_season__user",
            status_field="status",
            item_q=item_q or Q(),
        )
    return TrackerSource(
        model=model,
        user_lookup="user",
        status_field="status",
        item_q=item_q or Q(),
    )


@dataclass(frozen=True)
class PodcastShowSource(TrackerSource):
    """Relate show trackers to list items through their provider identity."""

    def item_rows(self, user, outer_ref: str = "pk"):
        """Find the show tracker for the outer item identity."""
        return self.rows(user).filter(
            show__podcast_uuid=OuterRef("media_id"), show__source=OuterRef("source"),
        )

    def item_ids(self, user, row_q: Q | None = None):
        """Resolve matching show trackers to their existing item ids."""
        from app.models import Item

        rows = self.rows(user)
        if row_q is not None:
            rows = rows.filter(row_q)
        item = Item.objects.filter(
            media_type=MediaTypes.PODCAST.value,
            media_id=OuterRef("show__podcast_uuid"), source=OuterRef("show__source"),
        ).values("pk")[:1]
        return rows.annotate(_item_id=Subquery(item)).values("_item_id")


@dataclass(frozen=True)
class AlbumSource(TrackerSource):
    """Relate album trackers to list Items using MusicBrainz identity."""

    def item_rows(self, user, outer_ref="pk"):
        """Find the viewer's album trackers for the outer list item."""
        from app.models import Album

        matching_groups = Album.objects.annotate(
            _local_id=Concat(Value("album-"), Cast("pk", models.CharField()))
        ).filter(
            Q(musicbrainz_release_id=OuterRef(OuterRef("media_id")))
            | Q(musicbrainz_release_group_id=OuterRef(OuterRef("media_id")))
            | Q(_local_id=OuterRef(OuterRef("media_id")))
        ).exclude(musicbrainz_release_group_id__isnull=True).exclude(
            musicbrainz_release_group_id=""
        ).values("musicbrainz_release_group_id")
        return self.rows(user).annotate(
            _album_local_id=Concat(Value("album-"), Cast("album_id", models.CharField()))
        ).filter(
            Q(album__musicbrainz_release_id=OuterRef("media_id"))
            | Q(album__musicbrainz_release_group_id=OuterRef("media_id"))
            | Q(album__musicbrainz_release_group_id__in=Subquery(matching_groups))
            | Q(_album_local_id=OuterRef("media_id"))
        )

    def item_ids(self, user, row_q=None):
        """Return existing list item ids matching album trackers."""
        from app.models import Item

        rows = self.item_rows(user)
        if row_q is not None:
            rows = rows.filter(row_q)
        return Item.objects.filter(self.item_q).annotate(
            _album_tracked=Exists(rows)
        ).filter(_album_tracked=True).values("pk")


def tracker_sources(user, media_type: str, routing: str, *, include_podcast_shows=False, include_albums=False) -> list[TrackerSource]:
    """Return the tracker sources that make up ``media_type``'s library.

    Anime a user tracks as TV lives on TV rows with an anime library bucket.
    With ``library`` routing, ``anime_library_visibility`` decides whether
    those rows appear in the Anime library, the TV library, or both, exactly
    as the media list does. With ``model`` routing they stay on TV.
    """
    if media_type == MediaTypes.MUSIC.value and include_albums:
        from app.models import AlbumTracker

        return [AlbumSource(AlbumTracker, "user", "status", Q(source=Sources.MUSICBRAINZ.value, media_type=media_type)), _source(media_type)]
    if media_type == MediaTypes.PODCAST.value and include_podcast_shows:
        from app.models import PodcastShowTracker

        return [PodcastShowSource(PodcastShowTracker, "user", "status", Q()), _source(media_type)]
    if routing == ROUTING_MODEL:
        return [_source(media_type)]
    anime_bucket = Q(library_media_type=MediaTypes.ANIME.value)
    if media_type == MediaTypes.ANIME.value:
        include_in_anime, _include_in_tv = metadata_resolution.anime_library_visibility(
            user,
        )
        sources = [_source(MediaTypes.ANIME.value)]
        if include_in_anime:
            sources.append(_source(MediaTypes.TV.value, anime_bucket))
        return sources
    if media_type == MediaTypes.TV.value:
        _include_in_anime, include_in_tv = metadata_resolution.anime_library_visibility(
            user,
        )
        return [_source(MediaTypes.TV.value, None if include_in_tv else ~anime_bucket)]
    return [_source(media_type)]


def episode_media_for_items(user, item_ids):
    """Load unique active episode cards with their own latest tracking state."""
    source = _source(MediaTypes.EPISODE.value)
    rows = (
        source.rows(user).filter(item_id__in=item_ids)
        .select_related("item", "related_season__item", "related_season__related_tv__item")
        .defer("item__watch_providers", "related_season__item__watch_providers",
               "related_season__related_tv__item__watch_providers")
        .annotate(_activity=source.activity())
        .order_by(F("_activity").desc(nulls_last=True), "-pk")
    )
    latest = {}
    for media in rows:
        primary = latest.setdefault(media.item_id, media)
        activity = media.end_date or media.start_date
        watched = media.end_date or (
            media.start_date if media.status == Status.IN_PROGRESS.value else None
        )
        if not hasattr(primary, "last_played_at"):
            primary.last_played_at = None
            primary.aggregated_last_watched = None
            primary.aggregated_score = media.score
            primary.aggregated_start_date = None
            primary.aggregated_end_date = None
            primary.aggregated_created_at = media.created_at
        if activity and (primary.last_played_at is None or activity > primary.last_played_at):
            primary.last_played_at = activity
        if watched and (primary.aggregated_last_watched is None or watched > primary.aggregated_last_watched):
            primary.aggregated_last_watched = watched
        if primary.aggregated_score is None and media.score is not None:
            primary.aggregated_score = media.score
        if media.start_date and (primary.aggregated_start_date is None or media.start_date < primary.aggregated_start_date):
            primary.aggregated_start_date = media.start_date
        if media.end_date and (primary.aggregated_end_date is None or media.end_date > primary.aggregated_end_date):
            primary.aggregated_end_date = media.end_date
        primary.aggregated_created_at = max(primary.aggregated_created_at, media.created_at)
    return latest
