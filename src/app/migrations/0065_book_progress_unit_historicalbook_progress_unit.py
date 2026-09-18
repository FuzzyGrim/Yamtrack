from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0064_restore_tz_shifted_media_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="progress_unit",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="historicalbook",
            name="progress_unit",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
