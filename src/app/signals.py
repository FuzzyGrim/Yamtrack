from django.db.models import Avg, Sum, Min, Max
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Season
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Season)
def update_media(sender, instance, created, **kwargs):
    if created:
        logger.info(f"Created season {instance.number} of {instance.title}")
    else:
        logger.info(f"Updated season {instance.number} of {instance.title}")

    # Get the parent media instance
    media = instance.parent

    # Get all the seasons for the parent media instance
    seasons_all = Season.objects.filter(parent=media)

    # Update the media fields based on the aggregated values of the seasons
    media.score = seasons_all.aggregate(Avg("score"))["score__avg"]
    media.progress = seasons_all.aggregate(Sum("progress"))["progress__sum"]
    media.start_date = seasons_all.aggregate(Min("start_date"))["start_date__min"]
    media.end_date = seasons_all.aggregate(Max("end_date"))["end_date__max"]

    # If completed and not last season, set status to watching
    if media.status == "Completed" and instance.number != seasons_all.count():
        media.status = "Watching"
    # Else set status to last season status
    else:
        media.status = instance.status

    # Save the updated media instance
    media.save()

    logger.info(f"Updated {instance.title} ({media.media_id}) with season {instance.number} data")
