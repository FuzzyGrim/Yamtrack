import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime

from celery import states
from celery.signals import before_task_publish, task_failure, task_success
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)
from django.db.utils import OperationalError
from django.dispatch import receiver
from django.utils import timezone
from django_celery_results.models import TaskResult

from app import credits as credit_helpers
from app import history_cache, statistics_cache
from app.discover import tab_cache as discover_tab_cache
from app.history_cache_utils import HISTORY_LOGGING_STYLES
from app.history_entry_builders import flat_anime_episode_watches
from app.models import (
    TV,
    AlbumTracker,
    Anime,
    ArtistTracker,
    BoardGame,
    Book,
    BookListeningSession,
    CollectionEntry,
    Comic,
    ComicIssue,
    DeletedEntry,
    DeletedMedia,
    DiscoverFeedback,
    DiscoverFeedbackType,
    Episode,
    Game,
    Item,
    ItemPersonCredit,
    ItemStudioCredit,
    ItemTag,
    Manga,
    MediaTypes,
    MetadataBackfillField,
    MetadataBackfillState,
    Movie,
    Music,
    Podcast,
    PodcastPlay,
    PodcastShowTracker,
    Season,
    Sources,
    Status,
    Video,
    VideoPlay,
)
from app.services import podcast_plays
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)

RUNTIME_UNKNOWN_FAILED = 999999  # runtime completely unknown / failed lookup


@receiver([pre_save, pre_delete])
def fence_overwrite_tracking_writes(sender, instance, **kwargs):
    """Cooperate with a durable replacement before mutating its original scope."""
    from integrations.import_scope import TRACKING_MODELS, guard_instances

    if (
        not kwargs.get("raw")
        and instance._meta.apps is apps
        and instance._meta.app_label == "app"
        and instance._meta.model_name in TRACKING_MODELS
    ):
        guard_instances([instance])


@receiver(post_save)
def refresh_entry_source_suggestions(sender, instance, **kwargs):
    """Offer a newly typed source in the tracking modal straight away."""
    if kwargs.get("raw") or not getattr(instance, "entry_source", ""):
        return
    from app import entry_sources

    entry_sources.note_saved_source(instance)


RUNTIME_BACKFILL_SOURCES = ("tmdb", "tvdb", "mal", "simkl")
GENRE_BACKFILL_SOURCES = ("tmdb", "tvdb", "mal", "simkl", "igdb", "bgg")
# Non-import tasks with user-facing results. Import tasks are tracked by their
# Celery name so a new importer does not need a second registration here.
TRACKED_TASK_NAMES = frozenset(
    {
        "integrations.tasks.import_goodreads",
        "Sync Plex Watchlist",
        "Sync Plex Watched Marks",
        "Scheduled backup export",
        "Bulk Episode Plays",
        "Bulk Music Plays",
        "Bulk Consumption Batch",
    },
)
DISCOVER_PRIORITY_HISTORY_DEBOUNCE_SECONDS = 15
DISCOVER_PRIORITY_HISTORY_COUNTDOWN = 15
_SUPPRESS_MEDIA_CACHE_CHANGE_SIGNALS: ContextVar[bool] = ContextVar(
    "suppress_media_cache_change_signals",
    default=False,
)
_SUPPRESS_MEDIA_CHANGE_SIDE_EFFECTS: ContextVar[bool] = ContextVar(
    "suppress_media_change_side_effects",
    default=False,
)
_IMPORTER_REPLACING_ROWS: ContextVar[bool] = ContextVar(
    "importer_replacing_rows",
    default=False,
)
_ACCOUNT_BEING_DELETED: ContextVar[bool] = ContextVar(
    "account_being_deleted",
    default=False,
)


@contextmanager
def suppress_media_cache_change_signals():
    """Temporarily skip tracked-media cache invalidation signal work."""
    token = _SUPPRESS_MEDIA_CACHE_CHANGE_SIGNALS.set(True)
    try:
        yield
    finally:
        _SUPPRESS_MEDIA_CACHE_CHANGE_SIGNALS.reset(token)


def media_cache_change_signals_suppressed() -> bool:
    """Return whether tracked-media cache invalidation signals are suppressed."""
    return bool(_SUPPRESS_MEDIA_CACHE_CHANGE_SIGNALS.get())


@contextmanager
def suppress_media_change_side_effects():
    """Temporarily skip media-row side effects during bulk mutations."""
    token = _SUPPRESS_MEDIA_CHANGE_SIDE_EFFECTS.set(True)
    try:
        yield
    finally:
        _SUPPRESS_MEDIA_CHANGE_SIDE_EFFECTS.reset(token)


@contextmanager
def importer_replacing_rows():
    """Mark deletes inside the block as an import replacing rows.

    An overwrite import deletes a tracked show and writes it back. That is not
    the user deleting it, so it must not leave a deletion tombstone: the
    tombstone would make every later sync skip a show the user still tracks.
    """
    token = _IMPORTER_REPLACING_ROWS.set(True)
    try:
        yield
    finally:
        _IMPORTER_REPLACING_ROWS.reset(token)


@contextmanager
def deleting_account():
    """Mark deletes inside the block as a whole account being removed.

    The cascade removes every tracked row with the user. A deletion tombstone
    or statistics marker written for each one would point at the user
    mid-delete and break the delete on its foreign key.
    """
    token = _ACCOUNT_BEING_DELETED.set(True)
    try:
        # Statistics, history and cache refresh signals would also write rows
        # for the user that is being removed.
        with suppress_media_cache_change_signals(), suppress_media_change_side_effects():
            yield
    finally:
        _ACCOUNT_BEING_DELETED.reset(token)


def media_change_side_effects_suppressed() -> bool:
    """Return whether media-row side effects are suppressed for the current context."""
    return bool(_SUPPRESS_MEDIA_CHANGE_SIDE_EFFECTS.get())


def _is_tracked_task(task_name):
    """Return whether a task needs durable status for a user-facing consumer."""
    return bool(
        task_name
        and (task_name.startswith("Import ") or task_name in TRACKED_TASK_NAMES)
    )


def _sender_task_name(sender):
    """Return the Celery task name carried by a completion signal sender."""
    return getattr(sender, "name", None) or getattr(
        getattr(sender, "request", None),
        "task",
        None,
    )


@before_task_publish.connect
def create_task_result_on_publish(
    sender=None,
    headers=None,
    body=None,
    **kwargs,
):
    """Create a TaskResult object with PENDING status on task publish.

    https://github.com/celery/django-celery-results/issues/286#issuecomment-1279161047
    """
    task_name = headers.get("task") if headers else None
    if not _is_tracked_task(task_name):
        return

    if not apps.ready:
        # Publishing from AppConfig.ready() (startup scheduling) must not
        # write to the database mid-initialization; a missing PENDING row is
        # already treated as PENDING by its consumers (issue #341).
        return

    try:
        TaskResult.objects.store_result(
            content_type="application/json",
            content_encoding="utf-8",
            task_id=headers["id"],
            result=None,
            status=states.PENDING,
            task_name=task_name,
            task_args=headers.get("argsrepr", ""),
            task_kwargs=headers.get("kwargsrepr", ""),
        )
    except OperationalError as e:
        # Handle disk I/O errors gracefully - log and continue
        # This can happen if the database file is locked or there's a disk issue
        logger.warning("Failed to store task result due to database error: %s", e)
    except Exception as e:  # pragma: no cover
        # Catch any other unexpected errors
        logger.warning("Unexpected error storing task result: %s", e)


@task_success.connect
def update_task_result_on_success(sender=None, result=None, **kwargs):
    """Mark the TaskResult row created on publish as SUCCESS.

    CELERY_RESULT_BACKEND is Redis, so django_celery_results never runs its
    own completion-tracking signal handlers against the DB - without this,
    the PENDING row from create_task_result_on_publish is never updated.
    """
    if not apps.ready or sender is None:
        return

    if not _is_tracked_task(_sender_task_name(sender)):
        return

    task_id = getattr(sender.request, "id", None)
    if not task_id:
        return

    try:
        TaskResult.objects.filter(task_id=task_id).update(
            status=states.SUCCESS,
            result=json.dumps(result),
            date_done=timezone.now(),
        )
    except OperationalError as e:
        logger.warning("Failed to update task result due to database error: %s", e)
    except Exception as e:  # pragma: no cover
        logger.warning("Unexpected error updating task result: %s", e)


@task_failure.connect
def update_task_result_on_failure(
    sender=None,
    task_id=None,
    exception=None,
    einfo=None,
    **kwargs,
):
    """Mark the TaskResult row created on publish as FAILURE."""
    if not apps.ready or not task_id:
        return

    if not _is_tracked_task(_sender_task_name(sender)):
        return

    try:
        TaskResult.objects.filter(task_id=task_id).update(
            status=states.FAILURE,
            result=json.dumps(
                {"exc_type": type(exception).__name__, "exc_message": [str(exception)]},
            ),
            traceback=str(einfo) if einfo else None,
            date_done=timezone.now(),
        )
    except OperationalError as e:
        logger.warning("Failed to update task result due to database error: %s", e)
    except Exception as e:  # pragma: no cover
        logger.warning("Unexpected error updating task result: %s", e)


def _sync_owner_smart_lists_for_items(owner, items):
    """Sync smart-list membership for a deduped set of owner items.

    Evaluating a smart list's rules scans the owner's library (seconds on a
    large one), so a request only checks whether the owner has smart lists and
    queues the evaluation on the default queue once the write commits.
    """
    if not owner:
        return

    if not CustomList.objects.filter(owner=owner, is_smart=True).exists():
        return
    item_ids = list({item.id for item in items if item and item.id})
    if not item_ids:
        return
    from lists.tasks import sync_smart_lists_for_items_task

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        sync_smart_lists_for_items_task.delay(owner.id, item_ids)
    else:
        transaction.on_commit(
            lambda: sync_smart_lists_for_items_task.delay(owner.id, item_ids),
        )


@receiver([post_save, post_delete], sender=TV)
@receiver([post_save, post_delete], sender=Season)
@receiver([post_save, post_delete], sender=Anime)
@receiver([post_save, post_delete], sender=Movie)
@receiver([post_save, post_delete], sender=Manga)
@receiver([post_save, post_delete], sender=Book)
@receiver([post_save, post_delete], sender=Comic)
@receiver([post_save, post_delete], sender=Game)
@receiver([post_save, post_delete], sender=BoardGame)
@receiver([post_save, post_delete], sender=Music)
@receiver([post_save, post_delete], sender=Podcast)
def sync_smart_lists_on_media_change(sender, instance, **kwargs):
    """Incrementally update smart-list memberships when owner media rows change."""
    if kwargs.get("raw"):
        return
    if media_change_side_effects_suppressed():
        return
    _sync_owner_smart_lists_for_items(
        getattr(instance, "user", None),
        [getattr(instance, "item", None)],
    )


@receiver([post_save, post_delete], sender=CollectionEntry)
def sync_smart_lists_on_collection_change(sender, instance, **kwargs):
    """Incrementally update smart lists when collection ownership changes."""
    if kwargs.get("raw"):
        return
    owner = getattr(instance, "user", None)
    item = getattr(instance, "item", None)
    if not owner or not item:
        return

    items_to_sync = [item]
    if item.media_type == MediaTypes.EPISODE.value:
        related_show_items = Item.objects.filter(
            media_id=item.media_id,
            source=item.source,
            media_type__in=[
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
                MediaTypes.SEASON.value,
            ],
        ).only("id", "media_type", "media_id", "source")
        items_to_sync.extend(related_show_items)

    _sync_owner_smart_lists_for_items(owner, items_to_sync)


@receiver([post_save, post_delete], sender=CollectionEntry)
def clear_media_list_cache_on_collection_change(sender, instance, **kwargs):
    """Invalidate media-list caches when collection metadata changes."""
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    if not user_id:
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return

    from app.cache_utils import clear_library_view_caches

    clear_library_view_caches(user_id)


@receiver([post_save, post_delete], sender=ArtistTracker)
@receiver([post_save, post_delete], sender=AlbumTracker)
@receiver([post_save, post_delete], sender=PodcastShowTracker)
def clear_home_row_cache_on_music_podcast_tracker_change(sender, instance, **kwargs):
    """Invalidate Home/media-list caches when a music/podcast tracker's status changes."""
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    if not user_id:
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return

    from app.cache_utils import clear_library_view_caches

    clear_library_view_caches(user_id)


@receiver([post_save, post_delete], sender=AlbumTracker)
def refresh_statistics_on_album_tracker_change(sender, instance, **kwargs):
    """Rebuild range aggregates when an album's tracking changes."""
    if kwargs.get("raw") or media_change_side_effects_suppressed():
        return
    if instance.user_id:
        statistics_cache.invalidate_statistics_cache(instance.user_id)


@receiver([post_save, post_delete], sender=ItemTag)
def sync_smart_lists_on_item_tag_change(sender, instance, **kwargs):
    """Incrementally update smart lists when a tag is applied to or removed from an item."""
    if kwargs.get("raw"):
        return
    owner = getattr(getattr(instance, "tag", None), "user", None)
    item = getattr(instance, "item", None)
    if not owner or not item:
        return
    _sync_owner_smart_lists_for_items(owner, [item])


@receiver([post_save, post_delete], sender=CustomListItem)
def sync_smart_lists_on_list_membership_change(sender, instance, **kwargs):
    """Re-evaluate referencing smart lists when a manual list's membership changes.

    A smart list's "List" filter includes the full contents of any linked
    (non-smart) list. Membership changes on that manual list happen outside
    the normal media-tracking signals below, so they need their own hook.
    """
    if kwargs.get("raw"):
        return
    custom_list = getattr(instance, "custom_list", None)
    item = getattr(instance, "item", None)
    if not custom_list or not item or custom_list.is_smart:
        return

    owners = {custom_list.owner}
    owners.update(custom_list.collaborators.all())
    for owner in owners:
        _sync_owner_smart_lists_for_items(owner, [item])


@receiver(m2m_changed, sender=CustomList.items.through)
def sync_smart_lists_on_list_items_m2m_change(
    sender, instance, action, reverse, pk_set, **kwargs
):
    """Catch list-membership writes that bypass CustomListItem's save/delete.

    `CustomList.items.add()` creates `CustomListItem` rows via the m2m
    manager's `bulk_create()`, which doesn't call `save()`, so
    `sync_smart_lists_on_list_membership_change` above never fires for it.
    `m2m_changed` fires regardless of how the through rows were written.
    """
    if action != "post_add" or reverse or instance.is_smart or not pk_set:
        return

    items = list(Item.objects.filter(id__in=pk_set))
    owners = {instance.owner}
    owners.update(instance.collaborators.all())
    for owner in owners:
        _sync_owner_smart_lists_for_items(owner, items)


@receiver(post_save, sender=Item)
def sync_smart_lists_on_watch_providers_change(
    sender, instance, update_fields=None, **kwargs
):
    """Re-evaluate smart lists after streaming providers are persisted on an item.

    Provider data is often written after the tracking row exists (TMDB backfill
    or detail-page refresh). Incremental sync on Movie/TV save then ran against
    empty providers and skipped the item; a later full rebuild corrected it.
    """
    if kwargs.get("raw") or media_change_side_effects_suppressed():
        return
    if not update_fields or "watch_providers" not in update_fields:
        return
    if not getattr(instance, "id", None):
        return

    try:
        model = apps.get_model("app", instance.media_type)
    except LookupError:
        return

    owner_ids = list(
        model.objects.filter(item_id=instance.id)
        .values_list("user_id", flat=True)
        .distinct()
    )
    if not owner_ids:
        return

    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    for owner in user_model.objects.filter(pk__in=owner_ids):
        _sync_owner_smart_lists_for_items(owner, [instance])


def _invalidate_history_for_media_change(
    user_id: int,
    *,
    day_keys,
    logging_styles,
    reason: str,
    prioritized: bool,
    force: bool = False,
) -> None:
    normalized_day_keys = [day_key for day_key in (day_keys or []) if day_key]
    if not normalized_day_keys:
        return

    invalidate_kwargs = {"force": True} if force else {}
    rebuild_styles = history_cache.invalidate_history_days(
        user_id,
        day_keys=normalized_day_keys,
        logging_styles=logging_styles,
        reason=reason,
        refresh_index=not prioritized,
        **invalidate_kwargs,
    )
    if not prioritized:
        return

    for logging_style in rebuild_styles:
        history_cache.schedule_history_refresh(
            user_id,
            logging_style,
            debounce_seconds=DISCOVER_PRIORITY_HISTORY_DEBOUNCE_SECONDS,
            countdown=DISCOVER_PRIORITY_HISTORY_COUNTDOWN,
            warm_days=0,
            day_keys=normalized_day_keys,
            allow_inline=False,
        )


def invalidate_bulk_episode_history(user_id, episodes, *, reason):
    """Invalidate History for episodes written in bulk, after the commit.

    ``bulk_create`` sends no save signals, so completing a show or season left
    its days showing the old entries until the cache expired.
    """
    if (
        not user_id
        or media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return
    day_keys = sorted(
        {
            day_key
            for episode in episodes
            if (day_key := _episode_history_day_key(episode.end_date, episode.start_date))
        },
    )
    if not day_keys:
        return
    transaction.on_commit(
        lambda: _invalidate_history_for_media_change(
            user_id,
            day_keys=day_keys,
            logging_styles=HISTORY_LOGGING_STYLES,
            reason=reason,
            prioritized=False,
            force=True,
        ),
    )


def _history_specs(day_keys):
    """Invalidate ``day_keys`` in both logging styles (only the user's is rebuilt)."""
    return [(day_keys, HISTORY_LOGGING_STYLES)]


def _handle_media_cache_change(
    user_id: int | None,
    changed_media_type: str,
    *,
    reason: str,
    history_specs=None,
    statistics_day_values=None,
    schedule_statistics: bool = True,
    force_history_days: bool = False,
    clear_runtime_caches: bool = True,
    history_dates_may_have_moved: bool = True,
) -> None:
    if not user_id:
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return

    if clear_runtime_caches:
        from app.cache_utils import clear_library_view_caches

        # Clears now and, inside a transaction, again once it commits.
        clear_library_view_caches(user_id, media_types=(changed_media_type,))

    active_context = discover_tab_cache.get_active_context(user_id)
    targets = discover_tab_cache.invalidate_for_media_change(
        user_id, changed_media_type
    )
    prioritized = discover_tab_cache.should_prioritize(
        active_context,
        changed_media_type,
        target_media_types=targets,
    )

    for day_keys, logging_styles in history_specs or []:
        _invalidate_history_for_media_change(
            user_id,
            day_keys=day_keys,
            logging_styles=logging_styles,
            reason=reason,
            prioritized=prioritized,
            force=force_history_days,
        )

    has_history_days = any(
        day_key
        for day_keys, _logging_styles in history_specs or []
        for day_key in day_keys or []
    )
    statistics_marked = False
    if history_specs and not has_history_days:
        # Planning activity is commonly undated. There is no day key to
        # invalidate in that case, but it can still appear in a title's
        # history. Statistics read undated rows straight from the database
        # when aggregating, so no day payload is affected: re-aggregating the
        # ranges is enough. History day payloads are dropped only when the save
        # may have taken a dated row's date away (its old day is unknown here):
        # otherwise adding or updating an undated entry, Planning above all,
        # threw away every cached day and left the repair task to rebuild them.
        if history_dates_may_have_moved:
            history_cache.invalidate_history_cache(
                user_id, reason="undated_media_change"
            )
        statistics_cache.invalidate_statistics_cache(user_id)
        statistics_marked = True

    normalized_stat_days = [
        day_value for day_value in (statistics_day_values or []) if day_value
    ]
    if normalized_stat_days:
        statistics_cache.invalidate_statistics_days(
            user_id,
            day_values=normalized_stat_days,
            reason=reason,
        )
        statistics_marked = True

    if schedule_statistics and not statistics_marked:
        # Status, score and count changes reach every range without touching a
        # day payload; recording the change is what makes the sync rebuild.
        statistics_cache.invalidate_statistics_cache(user_id)


def _invalidate_discover_from_item_tag(instance) -> None:
    owner = getattr(getattr(instance, "tag", None), "user", None)
    item = getattr(instance, "item", None)
    if not owner or not item:
        return
    discover_tab_cache.invalidate_for_media_change(owner.id, item.media_type)


@receiver(post_save, sender=TV)
@receiver(post_save, sender=Season)
@receiver(post_save, sender=Anime)
@receiver(post_save, sender=Movie)
@receiver(post_save, sender=Manga)
@receiver(post_save, sender=Book)
@receiver(post_save, sender=Comic)
@receiver(post_save, sender=Game)
@receiver(post_save, sender=BoardGame)
@receiver(post_save, sender=Music)
@receiver(post_save, sender=Podcast)
def clear_discover_feedback_on_media_save(sender, instance, **kwargs):
    """Clear hidden Discover feedback when a user explicitly tracks an item."""
    if kwargs.get("raw"):
        return
    if media_change_side_effects_suppressed():
        return
    user_id = getattr(instance, "user_id", None)
    item_id = getattr(instance, "item_id", None)
    if not user_id or not item_id:
        return
    DiscoverFeedback.objects.filter(
        user_id=user_id,
        item_id=item_id,
        feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
    ).delete()


@receiver(post_save, sender=Movie)
@receiver(post_save, sender=Episode)
def close_resume_point_on_watched(sender, instance, **kwargs):
    """Close the open resume point of a watched movie or episode (#1809)."""
    if kwargs.get("raw") or instance.status != Status.COMPLETED.value:
        return
    item_id = instance.item_id
    user_id = (
        instance.related_season.user_id if sender is Episode else instance.user_id
    )
    if not user_id or not item_id:
        return
    from api.fork_views_playback import complete_playback_progress
    from app import live_playback

    complete_playback_progress(
        user_id,
        item_id,
        event_at=live_playback.current_webhook_event_at(),
    )


# Same top-level, importer-tracked media types as helpers.get_existing_media()
# (Season/Episode excluded there and here).
@receiver(post_delete, sender=TV)
@receiver(post_delete, sender=Anime)
@receiver(post_delete, sender=Movie)
@receiver(post_delete, sender=Manga)
@receiver(post_delete, sender=Book)
@receiver(post_delete, sender=Comic)
@receiver(post_delete, sender=Game)
@receiver(post_delete, sender=BoardGame)
@receiver(post_delete, sender=Music)
@receiver(post_delete, sender=Podcast)
def record_deleted_media_tombstone(sender, instance, **kwargs):
    """Remember that the user deleted this item so imports don't recreate it."""
    if (
        kwargs.get("raw")
        or _IMPORTER_REPLACING_ROWS.get()
        or _ACCOUNT_BEING_DELETED.get()
    ):
        return
    user = getattr(instance, "user", None)
    item = getattr(instance, "item", None)
    if not user or not item:
        return
    DeletedMedia.objects.get_or_create(
        user=user,
        media_type=item.media_type,
        source=item.source,
        media_id=item.media_id,
    )


@receiver(post_delete, sender=TV)
@receiver(post_delete, sender=Season)
@receiver(post_delete, sender=Anime)
@receiver(post_delete, sender=Movie)
@receiver(post_delete, sender=Manga)
@receiver(post_delete, sender=Book)
@receiver(post_delete, sender=Comic)
@receiver(post_delete, sender=ComicIssue)
@receiver(post_delete, sender=Game)
@receiver(post_delete, sender=BoardGame)
@receiver(post_delete, sender=Music)
@receiver(post_delete, sender=Podcast)
@receiver(post_delete, sender=Video)
def record_deleted_entry(sender, instance, **kwargs):
    """Log the deletion for the library change feed (unlike the importer tombstone)."""
    if kwargs.get("raw") or _ACCOUNT_BEING_DELETED.get():
        return
    try:
        user_id = instance.user_id
        item = instance.item
    except ObjectDoesNotExist:  # the owner or item is going away with it
        return
    DeletedEntry.objects.update_or_create(
        user_id=user_id,
        media_type=item.media_type,
        source=item.source,
        media_id=item.media_id,
        season_number=item.season_number,
        defaults={"deleted_at": timezone.now()},
    )


@receiver(post_save, sender=TV)
@receiver(post_save, sender=Anime)
@receiver(post_save, sender=Movie)
@receiver(post_save, sender=Manga)
@receiver(post_save, sender=Book)
@receiver(post_save, sender=Comic)
@receiver(post_save, sender=Game)
@receiver(post_save, sender=BoardGame)
@receiver(post_save, sender=Music)
@receiver(post_save, sender=Podcast)
def clear_deleted_media_tombstone_on_track(sender, instance, created, **kwargs):
    """Clear a deletion tombstone when the user manually tracks the item again."""
    if kwargs.get("raw") or not created:
        return
    user = getattr(instance, "user", None)
    item = getattr(instance, "item", None)
    if not user or not item:
        return
    DeletedMedia.objects.filter(
        user=user,
        media_type=item.media_type,
        source=item.source,
        media_id=item.media_id,
    ).delete()


def flush_media_change_side_effects(
    *,
    owner,
    items,
    changed_media_type: str,
    reason: str,
    history_day_keys=None,
    statistics_day_values=None,
) -> None:
    """Run one consolidated side-effect pass after bulk media mutations."""
    if not owner:
        return

    normalized_items = []
    seen_item_ids = set()
    for item in items or []:
        if not item or not getattr(item, "id", None):
            continue
        if item.id in seen_item_ids:
            continue
        seen_item_ids.add(item.id)
        normalized_items.append(item)

    if normalized_items:
        from integrations.mal_sync import queue_grouped_sync
        from lists.tasks import sync_smart_lists_for_items_task

        # One trigger per show: its seasons and episodes share the show's
        # provider id, and the show item itself resolves most directly.
        per_show = {}
        for item in sorted(normalized_items, key=lambda item: item.media_type != "tv"):
            per_show.setdefault((item.source, item.media_id), item)
        for item in per_show.values():
            queue_grouped_sync(owner.id, item)

        sync_smart_lists_for_items_task.delay(owner.id, list(seen_item_ids))
        DiscoverFeedback.objects.filter(
            user_id=owner.id,
            item_id__in=seen_item_ids,
            feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
        ).delete()

    normalized_history_day_keys = [
        day_key for day_key in (history_day_keys or []) if day_key
    ]
    history_specs = []
    if normalized_history_day_keys:
        history_specs.extend(_history_specs(normalized_history_day_keys))

    _handle_media_cache_change(
        owner.id,
        changed_media_type,
        reason=reason,
        history_specs=history_specs,
        statistics_day_values=statistics_day_values or normalized_history_day_keys,
    )


def _discover_user_ids_for_credit_item(item) -> tuple[set[int], str | None]:
    if not item:
        return set(), None

    if item.media_type == MediaTypes.MOVIE.value:
        user_ids = (
            Movie.objects.filter(item_id=item.id)
            .values_list("user_id", flat=True)
            .distinct()
        )
        return set(user_ids), MediaTypes.MOVIE.value

    if item.media_type == MediaTypes.TV.value:
        user_ids = (
            TV.objects.filter(item_id=item.id)
            .values_list("user_id", flat=True)
            .distinct()
        )
        return set(user_ids), MediaTypes.TV.value

    if item.media_type == MediaTypes.EPISODE.value:
        user_ids = (
            Episode.objects.filter(item_id=item.id)
            .values_list("related_season__user_id", flat=True)
            .distinct()
        )
        return set(user_ids), MediaTypes.TV.value

    return set(), None


@receiver([post_save, post_delete], sender=ItemTag)
def refresh_discover_cache_on_item_tag_change(sender, instance, **kwargs):
    """Refresh Discover when item tags change, since they affect taste profiles."""
    if kwargs.get("raw"):
        return
    _invalidate_discover_from_item_tag(instance)


@receiver([post_save, post_delete], sender=ItemPersonCredit)
def refresh_discover_cache_on_item_person_credit_change(sender, instance, **kwargs):
    """Refresh Discover when credited people change on tracked movie/TV items."""
    if kwargs.get("raw"):
        return
    if media_change_side_effects_suppressed():
        return
    item = getattr(instance, "item", None)
    if item is None and getattr(instance, "item_id", None):
        item = Item.objects.filter(id=instance.item_id).only("id", "media_type").first()
    invalidate_discover_for_credit_item(item)


def invalidate_discover_for_credit_item(item):
    """Refresh Discover for the users tracking a movie/TV item whose credits changed."""
    user_ids, media_type = _discover_user_ids_for_credit_item(item)
    if not media_type:
        return
    for user_id in user_ids:
        discover_tab_cache.invalidate_for_media_change(user_id, media_type)


def _invalidate_episode_history_changes(changes, runtime_user_ids=()):
    """Invalidate committed Episode history and runtime caches by owner."""
    from app.cache_utils import clear_library_view_caches

    for user_id in runtime_user_ids:
        clear_library_view_caches(user_id, media_types=(MediaTypes.EPISODE.value,))

    for user_id, day_keys in changes.items():
        _handle_media_cache_change(
            user_id,
            MediaTypes.EPISODE.value,
            reason="episode_change",
            history_specs=_history_specs(day_keys),
            statistics_day_values=day_keys,
            force_history_days=True,
            clear_runtime_caches=False,
        )


@receiver(pre_save, sender=Episode)
def capture_episode_history_identity(sender, instance, **kwargs):
    """Remember the persisted Episode owner/day before an update."""
    if kwargs.get("raw"):
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return
    previous = None
    if instance.pk:
        previous = (
            Episode.objects.filter(pk=instance.pk)
            .values_list("related_season__user_id", "end_date", "start_date")
            .first()
        )
    instance._previous_history_identity = previous


def _episode_history_day_key(end_date, start_date):
    """Return the day an Episode is listed on: its finish, else its start."""
    return history_cache.history_day_key(end_date or start_date)


@receiver(post_save, sender=Episode)
def refresh_history_cache_on_episode_save(sender, instance, **kwargs):
    """Invalidate old and new Episode days after the write commits."""
    if kwargs.get("raw"):
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return
    previous = getattr(instance, "_previous_history_identity", None)
    if hasattr(instance, "_previous_history_identity"):
        delattr(instance, "_previous_history_identity")
    user_id = getattr(getattr(instance, "related_season", None), "user_id", None)
    day_key = _episode_history_day_key(instance.end_date, instance.start_date)
    changes = {}
    for changed_user_id, changed_day_key in (
        (
            previous[0] if previous else None,
            _episode_history_day_key(previous[1], previous[2]) if previous else None,
        ),
        (user_id, day_key),
    ):
        if changed_user_id and changed_day_key:
            changes.setdefault(changed_user_id, set()).add(changed_day_key)
    committed_changes = {user: sorted(days) for user, days in changes.items()}
    runtime_user_ids = sorted(
        {
            changed_user_id
            for changed_user_id in (previous[0] if previous else None, user_id)
            if changed_user_id
        },
    )
    transaction.on_commit(
        lambda: _invalidate_episode_history_changes(
            committed_changes,
            runtime_user_ids,
        ),
        using=kwargs.get("using"),
    )


@receiver(post_delete, sender=Episode)
def refresh_history_cache_on_episode_delete(sender, instance, **kwargs):
    """Invalidate a deleted Episode day after the deletion commits."""
    if kwargs.get("raw"):
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return
    user_id = getattr(getattr(instance, "related_season", None), "user_id", None)
    day_key = _episode_history_day_key(instance.end_date, instance.start_date)
    origin = kwargs.get("origin")
    if isinstance(origin, (Season, TV)):
        # Deleting a season or show cascades here once per episode. Gather the
        # days on the row being deleted and invalidate them together after the
        # commit, instead of one full cache round per episode for the same day.
        pending = getattr(origin, "_episode_history_days", None)
        if pending is None:
            pending = origin._episode_history_days = {}
            transaction.on_commit(
                lambda: _invalidate_episode_history_changes(
                    {
                        owner: sorted(days)
                        for owner, days in pending.items()
                        if days
                    },
                    list(pending),
                ),
                using=kwargs.get("using"),
            )
        if user_id:
            days = pending.setdefault(user_id, set())
            if day_key:
                days.add(day_key)
        return
    changes = {user_id: [day_key]} if user_id and day_key else {}
    runtime_user_ids = [user_id] if user_id else []
    transaction.on_commit(
        lambda changes=changes: _invalidate_episode_history_changes(
            changes,
            runtime_user_ids,
        ),
        using=kwargs.get("using"),
    )


def _statistics_days_for_dates(start_dt, end_dt):
    # Unsaved assignments can still hold a plain date; treat it as that day.
    start_dt, end_dt = (
        timezone.make_aware(datetime.combine(value, datetime.min.time()))
        if isinstance(value, date) and not isinstance(value, datetime)
        else value
        for value in (start_dt, end_dt)
    )
    days = set(history_cache.history_day_keys_for_range(start_dt, end_dt) or [])
    for value in (start_dt, end_dt):
        if day_key := history_cache.history_day_key(value):
            days.add(day_key)
    return days


@receiver(pre_save, sender=Movie)
@receiver(pre_save, sender=Music)
@receiver(pre_save, sender=Podcast)
@receiver(pre_save, sender=Game)
@receiver(pre_save, sender=BoardGame)
@receiver(pre_save, sender=Anime)
@receiver(pre_save, sender=Manga)
@receiver(pre_save, sender=Book)
@receiver(pre_save, sender=Comic)
def capture_statistics_previous_dates(sender, instance, **kwargs):
    """Remember a row's persisted dates, so moving them re-marks the old days.

    The change handlers only see the new dates; without this the day an entry
    moved away from kept counting it until something else rebuilt it.
    """
    if kwargs.get("raw"):
        return
    if not instance.pk:
        instance._history_had_dates = False
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return
    previous = (
        sender.objects.filter(pk=instance.pk)
        .values_list("start_date", "end_date")
        .first()
    )
    instance._statistics_previous_dates = previous
    # History places podcasts and music by end date alone, so a start date
    # does not make a row "dated": a progress save on an unfinished podcast
    # left no day behind, yet dropped every cached day.
    placed_by = previous[1:] if previous and sender in (Podcast, Music) else previous
    instance._history_had_dates = previous is None or any(placed_by)


@receiver(post_save, sender=Movie)
@receiver(post_save, sender=Music)
@receiver(post_save, sender=Podcast)
@receiver(post_save, sender=Game)
@receiver(post_save, sender=BoardGame)
@receiver(post_save, sender=Anime)
@receiver(post_save, sender=Manga)
@receiver(post_save, sender=Book)
@receiver(post_save, sender=Comic)
def mark_statistics_previous_dates(sender, instance, **kwargs):
    """Mark the days a row's dates moved away from."""
    previous = instance.__dict__.pop("_statistics_previous_dates", None)
    if kwargs.get("raw") or not previous:
        return
    if previous == (instance.start_date, instance.end_date):
        return
    old_days = _statistics_days_for_dates(*previous) - _statistics_days_for_dates(
        instance.start_date, instance.end_date
    )
    if old_days:
        # History day keys are the same days, and the receivers that follow
        # only see the new dates: the day a row moved away from kept showing it.
        history_cache.invalidate_history_days(
            instance.user_id,
            day_keys=sorted(old_days),
            reason="media_date_moved",
            force=True,
        )
        statistics_cache.invalidate_statistics_days(
            instance.user_id, old_days, reason="media_date_moved"
        )


def _history_dates_may_have_moved(instance, signal_kwargs) -> bool:
    """Whether an undated save or delete may have left a history day stale.

    History places a row by its dates, and the receivers only see the new
    ones. An undated row that is new, deleted, or saved without touching its
    dates cannot have left a day it used to be on; only a save that may have
    cleared existing dates needs the whole-cache fallback.
    """
    had_dates = instance.__dict__.pop("_history_had_dates", None)
    if signal_kwargs.get("signal") is post_delete or signal_kwargs.get("created"):
        return False
    update_fields = signal_kwargs.get("update_fields")
    if update_fields is not None and not {"start_date", "end_date"} & set(
        update_fields
    ):
        return False
    return had_dates is not False


@receiver([post_save, post_delete], sender=Movie)
def refresh_history_cache_on_movie_change(sender, instance, **kwargs):
    """Schedule history cache refresh when movie activity changes."""
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    activity_dt = getattr(instance, "end_date", None) or getattr(
        instance, "start_date", None
    )
    day_key = history_cache.history_day_key(activity_dt)
    _handle_media_cache_change(
        user_id,
        MediaTypes.MOVIE.value,
        reason="movie_change",
        history_specs=_history_specs([day_key] if day_key else []),
        statistics_day_values=[day_key] if day_key else [],
        history_dates_may_have_moved=_history_dates_may_have_moved(instance, kwargs),
    )


def _schedule_credits_backfill_if_needed(item_id):
    if not item_id:
        return
    item_row = (
        Item.objects.filter(
            id=item_id,
            source=Sources.TMDB.value,
            media_type__in=[
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.EPISODE.value,
            ],
        )
        .values("media_type")
        .first()
    )
    if not item_row:
        return
    if not credit_helpers.missing_credits_backfill_item_ids([item_id]):
        return
    from app.tasks import enqueue_credits_backfill_items

    enqueue_credits_backfill_items([item_id], countdown=3)


def _defer_credits_backfill_check(item_id):
    """Run the credits check after the save's response, not inside it."""
    if not item_id:
        return
    from app.tasks import enqueue_item_backfills

    transaction.on_commit(
        lambda: enqueue_item_backfills.delay(item_id, ["credits_if_needed"]),
        robust=True,
    )


@receiver(post_save, sender=Episode)
def schedule_credits_backfill_on_episode_play(sender, instance, **kwargs):
    """Queue credits backfill for episode and related show when an episode play is saved."""
    if kwargs.get("raw"):
        return
    if media_change_side_effects_suppressed():
        return
    if not getattr(instance, "end_date", None):
        return
    episode_item_id = getattr(instance, "item_id", None)
    _defer_credits_backfill_check(episode_item_id)
    related_season = getattr(instance, "related_season", None)
    season_item_id = getattr(getattr(related_season, "item", None), "id", None)
    _defer_credits_backfill_check(season_item_id)
    related_tv = getattr(related_season, "related_tv", None)
    tv_item_id = getattr(related_tv, "item_id", None)
    _defer_credits_backfill_check(tv_item_id)


@receiver(post_save, sender=Movie)
def schedule_credits_backfill_on_movie_play(sender, instance, **kwargs):
    """Queue credits backfill for TMDB movies when a play is saved."""
    if kwargs.get("raw"):
        return
    if media_change_side_effects_suppressed():
        return
    if not (
        getattr(instance, "end_date", None) or getattr(instance, "start_date", None)
    ):
        return
    _defer_credits_backfill_check(getattr(instance, "item_id", None))


@receiver([post_save, post_delete], sender=Music)
def refresh_history_cache_on_music_change(sender, instance, **kwargs):
    """Schedule history cache refresh after the music write commits."""
    if kwargs.get("raw"):
        return
    if (
        media_cache_change_signals_suppressed()
        or media_change_side_effects_suppressed()
    ):
        return

    user_id = getattr(instance, "user_id", None)
    day_key = history_cache.history_day_key(getattr(instance, "end_date", None))
    history_dates_may_have_moved = _history_dates_may_have_moved(instance, kwargs)

    def _refresh_after_commit():
        _handle_media_cache_change(
            user_id,
            MediaTypes.MUSIC.value,
            reason="music_change",
            history_specs=_history_specs([day_key] if day_key else []),
            statistics_day_values=[day_key] if day_key else [],
            history_dates_may_have_moved=history_dates_may_have_moved,
        )

    transaction.on_commit(
        _refresh_after_commit,
        using=kwargs.get("using"),
    )


@receiver([post_save, post_delete], sender=Podcast)
def refresh_history_cache_on_podcast_change(sender, instance, **kwargs):
    """Schedule history cache refresh when podcast activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    day_key = history_cache.history_day_key(getattr(instance, "end_date", None))
    history_specs = _history_specs([day_key] if day_key else [])
    update_fields = kwargs.get("update_fields")
    if not day_key and (
        kwargs.get("created")
        or (update_fields is not None and "end_date" not in update_fields)
    ):
        # History places podcasts by end date only. An undated save that
        # cannot have cleared one touches no history day, so skip the
        # whole-cache clear an undated change otherwise falls back to; GPodder
        # progress polls made exactly these saves (#1158).
        history_specs = None
    _handle_media_cache_change(
        user_id,
        MediaTypes.PODCAST.value,
        reason="podcast_change",
        history_specs=history_specs,
        statistics_day_values=[day_key] if day_key else [],
        history_dates_may_have_moved=_history_dates_may_have_moved(instance, kwargs),
    )


@receiver(post_save, sender=Podcast)
def keep_podcast_plays_in_step(sender, instance, created, **kwargs):
    """Make a direct save of a podcast tracker move or create its play.

    The web form, importers and webhooks write the tracker. A play is a
    listen, so the tracker's dates and status are carried onto the play that
    describes it. Writes made by the play service itself are skipped.
    """
    if kwargs.get("raw") or podcast_plays.in_service_write():
        return
    previous_end_date = None if created else instance.tracker.previous("end_date")
    podcast_plays.apply_tracker_edit(instance, previous_end_date)


@receiver(pre_save, sender=PodcastPlay)
def remember_podcast_play_end_date(sender, instance, **kwargs):
    """Keep the persisted end date so a moved play also refreshes its old day."""
    if kwargs.get("raw") or not instance.pk:
        return
    instance._previous_end_date = (
        sender.objects.filter(pk=instance.pk).values_list("end_date", flat=True).first()
    )


@receiver([post_save, post_delete], sender=PodcastPlay)
def refresh_history_cache_on_podcast_play_change(sender, instance, **kwargs):
    """Refresh the History and Statistics days a podcast listen lands on.

    The tracker's own save only covers its newest play, so a change to an
    older play needs its days refreshed here.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance.podcast, "user_id", None)
    day_keys = {
        history_cache.history_day_key(value)
        for value in (
            instance.end_date,
            instance.__dict__.pop("_previous_end_date", None),
        )
        if value
    }
    day_keys.discard(None)
    if not day_keys:
        return
    ordered = sorted(day_keys)
    _handle_media_cache_change(
        user_id,
        MediaTypes.PODCAST.value,
        reason="podcast_play_change",
        history_specs=_history_specs(ordered),
        statistics_day_values=ordered,
    )


@receiver([post_save, post_delete], sender=BookListeningSession)
def refresh_history_cache_on_book_listening_session_change(
    sender,
    instance,
    **kwargs,
):
    """Rebuild the history day a listening session lands on."""
    if kwargs.get("raw"):
        return
    try:
        user_id = instance.book.user_id
    except Book.DoesNotExist:
        return
    day_key = history_cache.history_day_key(instance.started_at)
    _handle_media_cache_change(
        user_id,
        MediaTypes.BOOK.value,
        reason="book_listening_session_change",
        history_specs=_history_specs([day_key] if day_key else []),
        statistics_day_values=[day_key] if day_key else [],
    )


@receiver([post_save, post_delete], sender=VideoPlay)
def refresh_history_cache_on_video_play_change(sender, instance, **kwargs):
    """Schedule history cache refresh when a video play is saved or removed."""
    if kwargs.get("raw"):
        return
    day_key = history_cache.history_day_key(instance.end_date)
    _handle_media_cache_change(
        instance.video.user_id,
        MediaTypes.VIDEO.value,
        reason="video_play_change",
        history_specs=_history_specs([day_key] if day_key else []),
        statistics_day_values=[day_key] if day_key else [],
    )


@receiver([post_save, post_delete], sender=TV)
def refresh_statistics_cache_on_tv_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when TV activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    _handle_media_cache_change(
        getattr(instance, "user_id", None),
        MediaTypes.TV.value,
        reason="tv_change",
    )


@receiver(post_delete, sender=TV)
def clear_time_left_cache_on_tv_delete(sender, instance, **kwargs):
    """Clear time_left cache when TV show is deleted."""
    user_id = getattr(instance, "user_id", None)
    if user_id:
        from app.cache_utils import clear_library_view_caches

        clear_library_view_caches(user_id, media_types=(MediaTypes.TV.value,))
        logger.debug(
            "Cleared time_left cache for user %s after deleting TV show: %s",
            user_id,
            instance,
        )


@receiver([post_save, post_delete], sender=Season)
def refresh_statistics_cache_on_season_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when season activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    _handle_media_cache_change(
        getattr(instance, "user_id", None),
        MediaTypes.SEASON.value,
        reason="season_change",
    )


@receiver(post_delete, sender=Season)
def clear_time_left_cache_on_season_delete(sender, instance, **kwargs):
    """Clear time_left cache when Season is deleted."""
    user_id = getattr(instance, "user_id", None)
    if user_id:
        from app.cache_utils import clear_library_view_caches

        clear_library_view_caches(user_id, media_types=(MediaTypes.SEASON.value,))
        logger.debug(
            "Cleared time_left cache for user %s after deleting Season: %s",
            user_id,
            instance,
        )


@receiver([post_save, post_delete], sender=Anime)
def refresh_statistics_cache_on_anime_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when anime activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    day_keys = _collect_reading_statistics_day_keys(instance)
    history_dates_may_have_moved = _history_dates_may_have_moved(instance, kwargs)
    if kwargs.get("signal") is post_delete:
        # History splits the entry into episodes dated by its change log, which
        # the delete removes, so the days they were on are no longer known.
        history_day_keys = []
        history_dates_may_have_moved = True
    else:
        history_day_keys = set(_collect_reading_history_day_keys(instance))
        # The change log row for this save is the newest episode date.
        history_day_keys.add(history_cache.history_day_key(timezone.now()))
        for _number, watched_at in flat_anime_episode_watches([instance]).get(
            instance.id,
            [],
        ):
            history_day_keys.add(history_cache.history_day_key(watched_at))
    _handle_media_cache_change(
        user_id,
        MediaTypes.ANIME.value,
        reason="anime_change",
        history_specs=_history_specs(history_day_keys),
        statistics_day_values=day_keys,
        history_dates_may_have_moved=history_dates_may_have_moved,
    )


def _collect_reading_statistics_day_keys(instance):
    """Return statistics day keys touched by a reading entry."""
    start_dt = getattr(instance, "start_date", None)
    end_dt = getattr(instance, "end_date", None)
    range_keys = history_cache.history_day_keys_for_range(start_dt, end_dt)
    activity_key = history_cache.history_day_key(
        end_dt or start_dt or getattr(instance, "created_at", None),
    )
    day_keys = set(range_keys or [])
    if activity_key:
        day_keys.add(activity_key)
    return day_keys


def _collect_reading_history_day_keys(instance):
    """Return the history day key(s) where a reading/anime card appears.

    The day builder anchors these single-record types on their end_date (falling
    back to start_date), so only that one day's cached card needs rebuilding.
    """
    activity_key = history_cache.history_day_key(
        getattr(instance, "end_date", None) or getattr(instance, "start_date", None),
    )
    return [activity_key] if activity_key else []


@receiver([post_save, post_delete], sender=Manga)
def refresh_statistics_cache_on_manga_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when manga activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    day_keys = _collect_reading_statistics_day_keys(instance)
    history_day_keys = _collect_reading_history_day_keys(instance)
    _handle_media_cache_change(
        user_id,
        MediaTypes.MANGA.value,
        reason="manga_change",
        history_specs=_history_specs(history_day_keys),
        statistics_day_values=day_keys,
        history_dates_may_have_moved=_history_dates_may_have_moved(instance, kwargs),
    )


@receiver([post_save, post_delete], sender=Book)
def refresh_statistics_cache_on_book_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when book activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    day_keys = _collect_reading_statistics_day_keys(instance)
    history_day_keys = _collect_reading_history_day_keys(instance)
    # A progress save is dated now in the change log, which History reads for
    # page-based books, so today's day is rebuilt too.
    today_key = history_cache.history_day_key(timezone.now())
    if today_key and today_key not in history_day_keys:
        history_day_keys = [*history_day_keys, today_key]
    _handle_media_cache_change(
        user_id,
        MediaTypes.BOOK.value,
        reason="book_change",
        history_specs=_history_specs(history_day_keys),
        statistics_day_values=day_keys,
        history_dates_may_have_moved=_history_dates_may_have_moved(instance, kwargs),
    )


@receiver([post_save, post_delete], sender=Comic)
def refresh_statistics_cache_on_comic_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when comic activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    day_keys = _collect_reading_statistics_day_keys(instance)
    history_day_keys = _collect_reading_history_day_keys(instance)
    _handle_media_cache_change(
        user_id,
        MediaTypes.COMIC.value,
        reason="comic_change",
        history_specs=_history_specs(history_day_keys),
        statistics_day_values=day_keys,
        history_dates_may_have_moved=_history_dates_may_have_moved(instance, kwargs),
    )


@receiver([post_save, post_delete], sender=ComicIssue)
def refresh_statistics_cache_on_comic_issue_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when comic issue activity changes."""
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    day_keys = _collect_reading_statistics_day_keys(instance)
    _handle_media_cache_change(
        user_id,
        MediaTypes.COMIC_ISSUE.value,
        reason="comic_issue_change",
        statistics_day_values=day_keys,
    )


@receiver([post_save, post_delete], sender=Game)
def refresh_statistics_cache_on_game_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when game activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    start_dt = getattr(instance, "start_date", None) or getattr(
        instance, "end_date", None
    )
    end_dt = getattr(instance, "end_date", None) or getattr(
        instance, "start_date", None
    )
    range_keys = history_cache.history_day_keys_for_range(start_dt, end_dt)
    session_key = history_cache.history_day_key(end_dt or start_dt)
    stats_day_keys = set(range_keys or [])
    if session_key:
        stats_day_keys.add(session_key)
    _handle_media_cache_change(
        user_id,
        MediaTypes.GAME.value,
        reason="game_change",
        history_specs=[
            (range_keys, ("repeats",)),
            ([session_key] if session_key else [], ("sessions",)),
        ],
        statistics_day_values=stats_day_keys,
        history_dates_may_have_moved=_history_dates_may_have_moved(instance, kwargs),
    )


@receiver([post_save, post_delete], sender=BoardGame)
def refresh_statistics_cache_on_boardgame_change(sender, instance, **kwargs):
    """Schedule statistics cache refresh when board game activity changes.

    We schedule a refresh but don't delete the cache immediately,
    so users can see the old data with a notification while refresh happens.
    """
    if kwargs.get("raw"):
        return
    user_id = getattr(instance, "user_id", None)
    start_dt = getattr(instance, "start_date", None) or getattr(
        instance, "end_date", None
    )
    end_dt = getattr(instance, "end_date", None) or getattr(
        instance, "start_date", None
    )
    range_keys = history_cache.history_day_keys_for_range(start_dt, end_dt)
    session_key = history_cache.history_day_key(end_dt or start_dt)
    stats_day_keys = set(range_keys or [])
    if session_key:
        stats_day_keys.add(session_key)
    _handle_media_cache_change(
        user_id,
        MediaTypes.BOARDGAME.value,
        reason="boardgame_change",
        history_specs=[
            (range_keys, ("repeats",)),
            ([session_key] if session_key else [], ("sessions",)),
        ],
        statistics_day_values=stats_day_keys,
        history_dates_may_have_moved=_history_dates_may_have_moved(instance, kwargs),
    )


@receiver(post_save, sender=Item)
def schedule_runtime_backfill_on_item_save(
    sender,
    instance,
    created,
    update_fields=None,
    **kwargs,
):
    """Queue runtime/genre/credits backfills for newly created or missing metadata items.

    Also invalidates time_left cache when episode runtime changes.
    """
    if kwargs.get("raw") or media_change_side_effects_suppressed():
        return

    # Check if runtime_minutes was actually updated (not just saving the same value)
    runtime_updated = (
        not created
        and (update_fields is None or "runtime_minutes" in update_fields)
        and instance.media_type == MediaTypes.EPISODE.value
    )

    # Every user tracking this show or one of its seasons sees the new runtime.
    if runtime_updated:
        from app.cache_utils import EPISODE_DETAILS, clear_show_view_caches

        clear_show_view_caches(instance.media_id, instance.source, EPISODE_DETAILS)

    # A row created just now has no backfill state yet, so there is nothing to
    # clear. Skipping the lookups matters when a show is completed for the
    # first time and hundreds of episode items are created in one request.
    if (
        not created
        and instance.runtime_minutes is not None
        and instance.runtime_minutes != RUNTIME_UNKNOWN_FAILED
    ):
        MetadataBackfillState.objects.filter(
            item=instance,
            field=MetadataBackfillField.RUNTIME,
        ).delete()
    genre_identity_fields = {"media_id", "source", "media_type"}
    genre_identity_changed = created or bool(
        genre_identity_fields.intersection(update_fields or set()),
    )
    if genre_identity_changed and not created:
        MetadataBackfillState.objects.filter(
            item=instance,
            field=MetadataBackfillField.GENRES,
        ).delete()
    has_people = False
    has_studios = False
    if instance.source == Sources.TMDB.value and instance.media_type in (
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
    ):
        has_people = ItemPersonCredit.objects.filter(item=instance).exists()
        has_studios = ItemStudioCredit.objects.filter(item=instance).exists()
        needs_studios = instance.media_type in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
        )
        if has_people and (has_studios or not needs_studios):
            MetadataBackfillState.objects.filter(
                item=instance,
                field=MetadataBackfillField.CREDITS,
            ).delete()

    relevant_fields = {"runtime_minutes", "genres", *genre_identity_fields}
    if (
        not created
        and update_fields is not None
        and not relevant_fields.intersection(update_fields)
    ):
        return

    # Avoid eager backfill task execution during tests; tests call backfill helpers directly.
    if settings.TESTING:
        return

    runtime_missing = (
        instance.runtime_minutes in (None, 0)
        and instance.runtime_minutes != RUNTIME_UNKNOWN_FAILED
    )
    genres_missing = not instance.genres

    if runtime_missing and instance.source in RUNTIME_BACKFILL_SOURCES:
        if instance.media_type in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.ANIME.value,
        ):
            from app.tasks import enqueue_item_backfills

            transaction.on_commit(
                lambda item_id=instance.id: enqueue_item_backfills.delay(
                    item_id, ["runtime"]
                ),
                robust=True,
            )
        elif (
            instance.media_type == MediaTypes.EPISODE.value
            and instance.season_number is not None
        ):
            from app.tasks import enqueue_episode_runtime_backfill

            transaction.on_commit(
                lambda coordinate=(instance.media_id, instance.source, instance.season_number): (
                    enqueue_episode_runtime_backfill([coordinate])
                ),
                robust=True,
            )

    genre_backfill_applicable = (
        instance.source in GENRE_BACKFILL_SOURCES
        and instance.media_type
        in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.ANIME.value,
            MediaTypes.GAME.value,
            MediaTypes.BOARDGAME.value,
        )
    )
    requires_tmdb_tv_genre_verification = (
        instance.source == Sources.TMDB.value
        and instance.media_type == MediaTypes.TV.value
    )
    tmdb_tv_genre_verification_triggered = requires_tmdb_tv_genre_verification and (
        created or genre_identity_changed or update_fields is None
    )

    if genre_backfill_applicable and (
        genres_missing or genre_identity_changed or tmdb_tv_genre_verification_triggered
    ):
        from app.tasks import enqueue_item_backfills

        transaction.on_commit(
            lambda item_id=instance.id: enqueue_item_backfills.delay(
                item_id, ["genres"]
            ),
            robust=True,
        )

    if (
        instance.source == Sources.TMDB.value
        and instance.media_type
        in (MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value)
        and (
            not has_people
            or (
                instance.media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value)
                and not has_studios
            )
        )
    ):
        from app.tasks import enqueue_item_backfills

        transaction.on_commit(
            lambda item_id=instance.id: enqueue_item_backfills.delay(
                item_id, ["credits"]
            ),
            robust=True,
        )
