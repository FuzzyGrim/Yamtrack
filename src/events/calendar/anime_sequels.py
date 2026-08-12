import logging

from app.models import Anime, Item, MediaTypes, Sources, Status
from app.providers import services

logger = logging.getLogger(__name__)

# Raw MAL v2 relation type for sequels, e.g. "sequel", "prequel", "spin_off"
SEQUEL_RELATION_TYPE = "sequel"
# Node formats that count as trackable anime sequels. OVAs, specials and
# music videos are excluded even when MAL lists them as sequels.
TRACKABLE_MEDIA_FORMATS = frozenset({"tv", "movie", "ona"})


def is_trackable_anime_sequel(related):
    """Return whether a related anime entry should be auto-tracked.

    Only MAL "sequel" relations in a trackable format are considered.
    Prequels, side stories, alternative versions, spin-offs, summaries and
    OVAs are explicitly ignored. Kept isolated so it is easy to review and
    extend (e.g. user-configurable relation types) without touching the
    polling or creation logic.
    """
    if not related:
        return False
    if related.get("relation_type") != SEQUEL_RELATION_TYPE:
        return False
    return related.get("media_format") in TRACKABLE_MEDIA_FORMATS


def get_related_anime(item):
    """Return the related_anime entries for an item, tolerating failures."""
    try:
        metadata = services.get_media_metadata(
            media_type=MediaTypes.ANIME.value,
            media_id=item.media_id,
            source=Sources.MAL.value,
        )
    except services.ProviderAPIError:
        logger.warning("Failed to fetch metadata for %s", item)
        return []
    except Exception:
        logger.exception("Error fetching metadata for %s", item)
        return []

    related = metadata.get("related", {}).get("related_anime", [])
    if not isinstance(related, list):
        logger.warning("%s - Malformed related_anime data", item)
        return []
    return related


def create_sequel_entries(item, user_ids):
    """Create planning anime entries for sequels of ``item`` for each user.

    Sequels are created idempotently: an item already tracked by a user is
    left untouched.
    """
    sequels = [
        related
        for related in get_related_anime(item)
        if is_trackable_anime_sequel(related)
    ]
    if not sequels:
        return 0

    created_count = 0
    for sequel in sequels:
        sequel_item, _ = Item.objects.get_or_create(
            media_id=str(sequel["media_id"]),
            source=sequel["source"],
            media_type=MediaTypes.ANIME.value,
            defaults={
                "title": sequel["title"],
                "image": sequel.get("image"),
            },
        )
        for user_id in user_ids:
            _, was_created = Anime.objects.get_or_create(
                item=sequel_item,
                user_id=user_id,
                defaults={"status": Status.PLANNING.value},
            )
            if was_created:
                created_count += 1
                logger.info(
                    "%s - Added sequel %s for user %s",
                    item,
                    sequel_item,
                    user_id,
                )
    return created_count


def check_anime_sequels():
    """Create planning entries for sequels announced for completed anime."""
    completed_trackers = Anime.objects.filter(
        status=Status.COMPLETED.value,
        item__source=Sources.MAL.value,
    ).values_list("item_id", "user_id")

    user_ids_by_item = {}
    for item_id, user_id in completed_trackers:
        user_ids_by_item.setdefault(item_id, set()).add(user_id)

    if not user_ids_by_item:
        return "No completed anime to check for sequels"

    items = Item.objects.filter(id__in=user_ids_by_item.keys())

    created_count = 0
    for item in items:
        try:
            created_count += create_sequel_entries(item, user_ids_by_item[item.id])
        except Exception:
            logger.exception("Error processing sequels for %s", item)

    logger.info(
        "Anime sequel check finished - created %d planning entries",
        created_count,
    )
    return f"Created {created_count} anime sequel planning entries"
