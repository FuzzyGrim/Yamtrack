import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0062_remove_boardgame_item_remove_boardgame_user_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="diaryentry",
            name="contains_spoilers",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="diaryentry",
            name="review_title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="diaryentry",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="diaryentry",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("followers", "Followers"),
                    ("private", "Private"),
                ],
                default="private",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="historicaldiaryentry",
            name="contains_spoilers",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="historicaldiaryentry",
            name="review_title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="historicaldiaryentry",
            name="updated_at",
            field=models.DateTimeField(
                blank=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="historicaldiaryentry",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("followers", "Followers"),
                    ("private", "Private"),
                ],
                default="public",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="diaryentry",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("followers", "Followers"),
                    ("private", "Private"),
                ],
                default="public",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="historicaldiaryentry",
            name="updated_at",
            field=models.DateTimeField(blank=True, editable=False),
        ),
    ]
