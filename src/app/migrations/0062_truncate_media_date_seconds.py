from django.db import migrations
from django.db.models.functions import TruncMinute

MEDIA_MODELS_WITH_START_AND_END_DATE = [
    "Movie",
    "Anime",
    "Manga",
    "Game",
    "Book",
    "Comic",
    "BoardGame",
    "BasicMedia",
]


def truncate_date_seconds(apps, _schema_editor):
    """Strip seconds/microseconds from start_date/end_date, imported with full precision."""
    for model_name in MEDIA_MODELS_WITH_START_AND_END_DATE:
        model = apps.get_model("app", model_name)
        for field in ("start_date", "end_date"):
            model.objects.filter(**{f"{field}__isnull": False}).update(
                **{field: TruncMinute(field)},
            )

    Episode = apps.get_model("app", "Episode")
    Episode.objects.filter(end_date__isnull=False).update(
        end_date=TruncMinute("end_date"),
    )


class Migration(migrations.Migration):
    """Normalize legacy start_date/end_date values imported with second precision."""

    dependencies = [
        ("app", "0061_episode_item_not_null"),
    ]

    operations = [
        migrations.RunPython(
            truncate_date_seconds,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
