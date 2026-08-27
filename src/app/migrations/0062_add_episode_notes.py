from django.db import migrations, models


class Migration(migrations.Migration):
    """Add notes support to episode watches and their history."""

    dependencies = [
        ("app", "0061_episode_item_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="episode",
            name="notes",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="historicalepisode",
            name="notes",
            field=models.TextField(blank=True, default=""),
        ),
    ]
