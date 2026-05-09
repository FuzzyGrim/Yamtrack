from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0051_user_obfuscate_unseen_episodes"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="profile_private",
            field=models.BooleanField(
                default=True,
                help_text="Toggle profile visibility to anonymous users",
            ),
        ),
    ]