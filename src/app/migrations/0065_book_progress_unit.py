from django.db import migrations, models

UNITS = [("pages", "Pages"), ("percentage", "Percentage")]


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0064_restore_tz_shifted_media_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="progress_unit",
            field=models.CharField(choices=UNITS, default="pages", max_length=20),
        ),
        migrations.AddField(
            model_name="historicalbook",
            name="progress_unit",
            field=models.CharField(choices=UNITS, default="pages", max_length=20),
        ),
        migrations.AddConstraint(
            model_name="book",
            constraint=models.CheckConstraint(
                condition=models.Q(("progress_unit__in", ["pages", "percentage"])),
                name="app_book_progress_unit_valid",
            ),
        ),
    ]
