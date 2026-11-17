"""Build calendar items for a persisted episode-order catalogue."""

from datetime import datetime

from django.utils import timezone

from app.models import TV, EpisodeOrder, Item, MediaTypes, Season, Status
from events import release_marker
from events.models import Event


def _release_datetime(value):
    """Parse a provider date or return the unknown-date sentinel."""
    if not value:
        return datetime.max.replace(tzinfo=timezone.get_current_timezone())
    if hasattr(value, "tzinfo"):
        return value if value.tzinfo else timezone.make_aware(value)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.get_current_timezone())
    return parsed if parsed.tzinfo else timezone.make_aware(parsed)


def refresh_order_events(order: EpisodeOrder):
    """Materialize an order's seasons, episodes, and calendar events idempotently."""
    rows = order.catalogue.get("episodes") or []
    tvs = TV.objects.filter(item_id=order.show_id).only("pk", "user_id")
    season_items = {}
    for row in rows:
        season_number = int(row["season_number"])
        season_item, _ = Item.objects.get_or_create(
            episode_order=order,
            media_id=order.media_id,
            source=order.provider,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            episode_number=None,
            defaults={"title": order.show.title, "image": order.show.image},
        )
        season_items[season_number] = season_item
        episode_item, _ = Item.objects.update_or_create(
            episode_order=order,
            media_id=order.media_id,
            source=order.provider,
            media_type=MediaTypes.EPISODE.value,
            season_number=season_number,
            episode_number=int(row["episode_number"]),
            defaults={
                "provider_episode_id": str(row["provider_episode_id"]),
                "title": row.get("title") or order.show.title,
                "image": row.get("image") or "",
                "runtime_minutes": row.get("runtime"),
                "release_datetime": _release_datetime(row.get("air_date")),
            },
        )
        for tv in tvs:
            _season, _ = Season.all_objects.get_or_create(
                related_tv=tv,
                item=season_item,
                user_id=tv.user_id,
                defaults={"status": Status.IN_PROGRESS.value},
            )
            Event.objects.update_or_create(
                item=episode_item,
                content_number=episode_item.episode_number,
                defaults={"datetime": episode_item.release_datetime},
            )
    if rows:
        release_marker.bump()
    return len(rows)
