from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0056_remove_user_last_search_type_valid_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="display_name",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.AlterField(
            model_name="user",
            name="profile_private",
            field=models.BooleanField(
                default=False,
                help_text="Toggle profile visibility to anonymous users",
            ),
        ),
    ]
