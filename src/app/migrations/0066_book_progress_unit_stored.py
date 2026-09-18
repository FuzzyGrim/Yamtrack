from django.db import migrations, models

PAGES = "pages"
UNITS = ["pages", "percentage"]


def set_unit_on_existing_books(apps, _):
    """Existing progress predates percentages, so it is pages."""
    for model_name in ("Book", "HistoricalBook"):
        model = apps.get_model("app", model_name)
        model.objects.filter(progress_unit="").update(progress_unit=PAGES)


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0065_book_progress_unit_historicalbook_progress_unit"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="book",
            options={"ordering": ["user", "item"]},
        ),
        migrations.AlterField(
            model_name="book",
            name="progress_unit",
            field=models.CharField(
                choices=[("pages", "Pages"), ("percentage", "Percentage")],
                default=PAGES,
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="historicalbook",
            name="progress_unit",
            field=models.CharField(
                choices=[("pages", "Pages"), ("percentage", "Percentage")],
                default=PAGES,
                max_length=20,
            ),
        ),
        migrations.RunPython(
            set_unit_on_existing_books,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="book",
            constraint=models.CheckConstraint(
                condition=models.Q(("progress_unit__in", UNITS)),
                name="app_book_progress_unit_valid",
            ),
        ),
    ]
