import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from lists.imports import mdblist as mdblist_lists
from lists.imports import trakt as trakt_lists

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(name="Sync Smart Lists For Items")
def sync_smart_lists_for_items_task(owner_id: int, item_ids: list) -> None:
    """Sync smart-list membership for a set of items — runs in background after bulk media mutations.

    Each list is asked once which of the items match; a list that fails is
    logged and the others still sync.
    """
    from app.models import Item
    from lists.smart_rules import sync_smart_lists_for_items

    try:
        owner = User.objects.get(pk=owner_id)
    except User.DoesNotExist:
        return
    # Ids of deleted items match nothing and have no rows, so they are no-ops.
    sync_smart_lists_for_items(owner, [Item(id=int(item_id)) for item_id in item_ids])


@shared_task(name="Sync Smart List Items")
def sync_smart_list_task(list_id: int) -> None:
    """Sync a smart list's membership in the background."""
    from lists.models import CustomList

    try:
        custom_list = CustomList.objects.get(pk=list_id)
    except CustomList.DoesNotExist:
        return
    if not custom_list.is_smart:
        return
    try:
        custom_list.sync_smart_items()
    except Exception:
        logger.exception("Smart list sync failed for list_id=%s", list_id)
        raise


def schedule_smart_list_sync(custom_list, debounce_seconds=60):
    """Queue a background membership sync for a smart list, debounced.

    Used on GET paths so list pages render the current membership without
    blocking on the (write-heavy) sync; the result lands moments later.
    """
    from django.core.cache import cache

    if not custom_list.is_smart:
        return False
    lock_key = f"smart_list_sync_scheduled:{custom_list.id}"
    if not cache.add(lock_key, True, timeout=debounce_seconds):
        return False
    sync_smart_list_task.delay(custom_list.id)
    return True


@shared_task(name="Import MDBList Lists")
def import_mdblist_lists_task(user_id):
    """Celery task syncing all of a user's MDBList lists (also runs on a schedule)."""
    user = User.objects.get(pk=user_id)
    account = getattr(user, "mdblist_account", None)
    # A broken account still runs: the sync is the probe, and it clears the
    # flag when the key works again.
    if not account or not account.api_key:
        logger.info("Skipping MDBList sync for user %s (no account)", user.username)
        return
    try:
        mdblist_lists.import_mdblist_lists(user)
    except Exception:
        logger.exception("Failed to sync MDBList lists for user %s", user.username)
        raise


@shared_task(name="Import MDBList List")
def import_mdblist_list_by_reference_task(user_id, reference):
    """Celery task importing a single MDBList list by URL or ID."""
    user = User.objects.get(pk=user_id)
    try:
        mdblist_lists.import_mdblist_list_by_reference(user, reference)
    except Exception:
        logger.exception(
            "Failed to import MDBList list %s for user %s", reference, user.username
        )
        raise


@shared_task(name="Import Trakt Lists")
def import_trakt_lists_task(user_id, access_token, client_id=None):
    """Celery task for importing Trakt lists asynchronously."""
    user = User.objects.get(pk=user_id)
    try:
        trakt_lists.import_trakt_lists(user, access_token, client_id=client_id)
        logger.info("Successfully imported Trakt lists for user %s", user.username)
    except Exception:
        logger.exception("Failed to import Trakt lists for user %s", user.username)
        raise


@shared_task(name="Import List From CSV")
def import_list_csv_task(user_id, file_bytes, mode):
    """Celery task importing a single custom list from a CSV file."""
    from integrations.imports import yamtrack as yamtrack_imports
    from integrations.upload_staging import open_import_file

    user = User.objects.get(pk=user_id)
    try:
        with open_import_file(file_bytes) as uploaded_file:
            yamtrack_imports.importer(
                uploaded_file,
                user,
                mode,
                lists_only=True,
            )
        logger.info("Successfully imported list CSV for user %s", user.username)
    except Exception:
        logger.exception("Failed to import list CSV for user %s", user.username)
        raise
