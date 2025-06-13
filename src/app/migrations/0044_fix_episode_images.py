from django.db import migrations
from django.conf import settings
from app.models import MediaTypes
from app.providers import services
import logging

logger = logging.getLogger(__name__)

def update_episode_images(apps, schema_editor):
    Item = apps.get_model('app', 'Item')

    # Get all episode items with default image
    episode_items = Item.objects.filter(
        media_type=MediaTypes.EPISODE.value,
        image=settings.IMG_NONE
    )

    if not episode_items.exists():
        return
    
    logger.info("Starting episode image update migration")
    logger.info("Found %s episodes with default images to process", episode_items.count())

    items_to_update = []

    for item in episode_items:
        try:

            logger.info(
                "Updating image for %s S%sE%s",
                item.title,
                item.season_number,
                item.episode_number
            )
            season_metadata = services.get_media_metadata(
                MediaTypes.SEASON.value,
                item.media_id,
                item.source,
                [item.season_number]
            )

            for ep_meta in season_metadata.get('episodes', []):
                if ep_meta['episode_number'] == int(item.episode_number):
                    if ep_meta.get('still_path'):
                        item.image = f"https://image.tmdb.org/t/p/original{ep_meta['still_path']}"
                    elif 'image' in ep_meta:
                        item.image = ep_meta['image']
                    items_to_update.append(item)
                    break

        except Exception as e:
            print(f"Failed to update image for episode {item.id}: {str(e)}")

    if items_to_update:
        Item.objects.bulk_update(items_to_update, ['image'])

class Migration(migrations.Migration):
    dependencies = [
        ('app', '0043_remove_historicalanime_progress_changed_and_more'),
    ]

    operations = [
        migrations.RunPython(update_episode_images, reverse_code=migrations.RunPython.noop),
    ]
