from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0050_user_watch_provider_region"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="home_separate_incoming",
            field=models.BooleanField(
                default=False,
                help_text="Separate incoming media from in-progress on home page",
            ),
        ),
    ]
