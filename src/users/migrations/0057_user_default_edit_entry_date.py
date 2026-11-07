from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0056_user_home_hide_unreleased"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="default_edit_entry_date",
            field=models.CharField(
                choices=[("current_date", "Current Date"), ("no_date", "No End Date")],
                default="current_date",
                help_text="Default end date when opening the edit entry dialog",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    default_edit_entry_date__in=["current_date", "no_date"],
                ),
                name="default_edit_entry_date_valid",
            ),
        ),
    ]