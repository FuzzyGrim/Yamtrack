from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0052_alter_user_date_format"),
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
