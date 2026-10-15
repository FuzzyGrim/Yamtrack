from django.conf import settings
from django.db import migrations

MEDIA_TYPE_TV = "tv"
MEDIA_TYPE_SEASON = "season"
BATCH_SIZE = 500


def fill_season_images_from_tv(apps, _):
    """Use the show's poster for seasons stored without their own image."""
    Item = apps.get_model("app", "Item")

    seasons = list(
        Item.objects
        .filter(media_type=MEDIA_TYPE_SEASON, image=settings.IMG_NONE)
        .only("id", "media_id", "source", "image"),
    )
    if not seasons:
        return

    tv_images = {
        (media_id, source): image
        for media_id, source, image in Item.objects
        .filter(
            media_type=MEDIA_TYPE_TV,
            media_id__in={season.media_id for season in seasons},
        )
        .exclude(image=settings.IMG_NONE)
        .values_list("media_id", "source", "image")
    }
    if not tv_images:
        return

    items_to_update = []
    for season in seasons:
        image = tv_images.get((season.media_id, season.source))
        if image:
            season.image = image
            items_to_update.append(season)

    if items_to_update:
        Item.objects.bulk_update(items_to_update, ["image"], batch_size=BATCH_SIZE)


class Migration(migrations.Migration):
    """Backfill season posters with the parent show poster when missing."""

    dependencies = [
        ("app", "0062_truncate_media_date_seconds"),
    ]

    operations = [
        migrations.RunPython(
            fill_season_images_from_tv,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
