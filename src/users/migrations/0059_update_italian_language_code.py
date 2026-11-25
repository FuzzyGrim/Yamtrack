from django.db import migrations


def use_region_code(apps, schema_editor):
    """Update users with the Italian language to its region-style code."""
    User = apps.get_model("users", "User")
    User.objects.filter(language="it").update(language="it-it")


def use_generic_code(apps, schema_editor):
    """Restore the previous Italian language code."""
    User = apps.get_model("users", "User")
    User.objects.filter(language="it-it").update(language="it")


class Migration(migrations.Migration):
    """Data migration to match the new Italian code in the LANGUAGES setting."""

    dependencies = [
        ("users", "0058_user_show_adult_titles"),
    ]

    operations = [
        migrations.RunPython(use_region_code, use_generic_code),
    ]
