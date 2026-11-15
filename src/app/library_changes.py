"""Keep ``updated_at`` honest for changes that are not edits of the row itself.

A tracking row's own writes stamp ``updated_at`` (see ``Media.save`` and
``ImportScopedQuerySet``). List and tag membership live in other tables yet show
up on the media list (``lists``, ``tags``), so an incremental client would miss
them. These signals stamp the affected user's tracking rows for that item.
"""

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from app.models import TV, Episode, ItemTag, Media, Season
from lists.models import CustomListItem


def _tracking_models():
    return [
        model
        for model in apps.get_models()
        if issubclass(model, Media) and not model._meta.abstract
    ]


def touch_tracking_rows(user_id, item_id):
    """Stamp every tracking row of this user for this item as just changed."""
    if not user_id or not item_id:
        return
    now = timezone.now()
    for model in _tracking_models():
        # Plain QuerySet.update: this is bookkeeping, not a tracking mutation,
        # so it must not trip the import-scope fence or its side effects.
        models.QuerySet.update(
            model._base_manager.filter(user_id=user_id, item_id=item_id),
            updated_at=now,
        )


@receiver(post_save, sender=ItemTag)
@receiver(post_delete, sender=ItemTag)
def touch_on_tag_change(sender, instance, **kwargs):
    """Stamp the owner's entry when a tag is added to or removed from it."""
    if kwargs.get("raw"):
        return
    try:
        user_id = instance.tag.user_id
    except ObjectDoesNotExist:  # the tag itself is being deleted
        return
    touch_tracking_rows(user_id, instance.item_id)


@receiver(post_save, sender=CustomListItem)
@receiver(post_delete, sender=CustomListItem)
def touch_on_list_change(sender, instance, **kwargs):
    """Stamp the owner's entry when it is added to or removed from a list."""
    if kwargs.get("raw"):
        return
    try:
        owner_id = instance.custom_list.owner_id
    except ObjectDoesNotExist:  # the list itself is being deleted
        return
    touch_tracking_rows(owner_id, instance.item_id)


def touch_seasons(season_ids):
    """Stamp these seasons and the shows that own them as just changed."""
    now = timezone.now()
    seasons = [
        model for model in _tracking_models() if model._meta.model_name == "season"
    ]
    for model in seasons:
        rows = model._base_manager.filter(pk__in=season_ids)
        tv_ids = set(rows.values_list("related_tv_id", flat=True))
        models.QuerySet.update(rows, updated_at=now)
        _touch_shows(tv_ids, now)


def _touch_shows(tv_ids, now):
    tv_ids = set(tv_ids) - {None}
    for tv_model in _tracking_models():
        if tv_model._meta.model_name == "tv":
            models.QuerySet.update(
                tv_model._base_manager.filter(pk__in=tv_ids),
                updated_at=now,
            )


@receiver(post_save, sender=Episode)
@receiver(post_delete, sender=Episode)
def touch_on_episode_change(sender, instance, **kwargs):
    """Watching or unwatching an episode changes its season and its show."""
    if kwargs.get("raw") or not instance.related_season_id:
        return
    origin = kwargs.get("origin")
    if isinstance(origin, TV):
        # Deleting a show cascades here; its seasons go with it, so there is
        # nothing left to stamp.
        return
    if isinstance(origin, Season) and origin.pk == instance.related_season_id:
        # Deleting a season cascades to each of its episodes. The season row
        # goes too, so only its show needs a stamp, and once is enough.
        if not getattr(origin, "_touched_show", False):
            origin._touched_show = True
            _touch_shows({origin.related_tv_id}, timezone.now())
        return
    touch_seasons({instance.related_season_id})
