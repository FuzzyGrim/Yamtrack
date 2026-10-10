"""Persist provider-verified Stage identity without coalescing attendance."""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from app.models import Item, MediaTypes, Sources, Stage, StageRedirect
from app.providers import services
from lists.models import CustomListItem


def identity_conflict():
    """Leave saved data untouched when identity evidence is inconsistent."""
    raise services.ProviderAPIError(
        Sources.WIKIDATA.value,
        ValueError(),
        "Conflicting Stage identity; saved records were not changed",
    )


def serialize_redirects():
    """Lock the identity graph: PostgreSQL advisory lock or SQLite write lock."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1497451860])
    elif connection.vendor == "sqlite":
        StageRedirect.objects.filter(pk__isnull=True).update(revision=F("revision"))
    else:
        identity_conflict()


@transaction.atomic
def record_redirect(alias_id, entity):
    """Record an explicit provider redirect and reconcile already-saved items."""
    canonical_id = entity["id"]
    if alias_id == canonical_id:
        return
    serialize_redirects()
    redirect = entity.get("redirects", {})
    revision = entity.get("lastrevid")
    if (
        redirect.get("from") != alias_id
        or redirect.get("to") != canonical_id
        or not re.fullmatch(r"Q[1-9][0-9]*", canonical_id)
        or not isinstance(revision, int)
        or revision <= 0
    ):
        identity_conflict()
    existing = (
        StageRedirect.objects.select_for_update().filter(alias_id=alias_id).first()
    )
    terminal_id = StageRedirect.resolve(canonical_id)
    if terminal_id == alias_id:
        identity_conflict()
    if existing and StageRedirect.resolve(existing.canonical_id) != terminal_id:
        identity_conflict()
    StageRedirect.objects.get_or_create(
        alias_id=alias_id,
        defaults={"canonical_id": canonical_id, "revision": revision},
    )
    canonical_id = terminal_id
    items = (
        Item.objects.select_for_update()
        .filter(
            source=Sources.WIKIDATA.value,
            media_type=MediaTypes.STAGE.value,
            media_id__in=[alias_id, canonical_id],
        )
        .order_by("pk")
    )
    items_by_id = {item.media_id: item for item in items}
    old = items_by_id.get(alias_id)
    if old is None:
        return
    target = items_by_id.get(canonical_id)
    if target is None:
        target = rename_saved_work(old, canonical_id)
        if target is None:
            return
    merge_saved_work(old, target)


def rename_saved_work(old, canonical_id):
    """Retry as a merge if a concurrent catalog write created the target first."""
    old.media_id = canonical_id
    if old.stage_artwork:
        old.stage_artwork = retarget_artwork(old.stage_artwork, canonical_id)
    try:
        with transaction.atomic():
            old.save(update_fields=["media_id", "stage_artwork"])
    except IntegrityError:
        target = (
            Item.objects.select_for_update()
            .filter(
                media_id=canonical_id,
                source=Sources.WIKIDATA.value,
                media_type=MediaTypes.STAGE.value,
            )
            .first()
        )
        if target is None:
            raise
        return target
    return None


def merge_saved_work(old, target):
    """Move only known personal references; reject unexpected cascade losses."""
    exclusions = get_user_model().notification_excluded_items.through
    supported = {Stage, CustomListItem, exclusions}
    for relation in old._meta.related_objects:
        if relation.many_to_many or relation.related_model in supported:
            continue
        if relation.related_model.objects.filter(**{relation.field.name: old}).exists():
            identity_conflict()
    if (
        old.stage_artwork
        and not target.stage_artwork
        and target.image in {"", settings.IMG_NONE}
    ):
        target.image = old.image
        target.stage_artwork = retarget_artwork(old.stage_artwork, target.media_id)
        target.save(update_fields=["image", "stage_artwork"])
    Stage.objects.filter(item=old).update(item=target)
    for membership in CustomListItem.objects.filter(item=old):
        existing = CustomListItem.objects.filter(
            item=target, custom_list=membership.custom_list
        ).first()
        if existing:
            if membership.date_added < existing.date_added:
                CustomListItem.objects.filter(pk=existing.pk).update(
                    date_added=membership.date_added
                )
            membership.delete()
        else:
            membership.item = target
            membership.save(update_fields=["item"])
    for membership in exclusions.objects.filter(item=old):
        exclusions.objects.get_or_create(user_id=membership.user_id, item=target)
        membership.delete()
    old.delete()


def retarget_artwork(artwork, media_id):
    """Associate equivalent work imagery without changing its original evidence."""
    return {
        **artwork,
        "evidence_work_id": artwork.get("evidence_work_id", artwork.get("work_id")),
        "work_id": media_id,
    }


def canonical_id(media_id):
    """Use persisted provider evidence without consulting a remote service."""
    return StageRedirect.resolve(media_id)
