from django.db import migrations, models


def set_manual_completion_flags(apps, schema_editor):
    Book = apps.get_model('app', 'Book')
    for book in Book.objects.all():
        book.completed_manually = book.completion_diary_entry_id is None
        book.save(update_fields=['completed_manually'])


def unset_manual_completion_flags(apps, schema_editor):
    Book = apps.get_model('app', 'Book')
    Book.objects.update(completed_manually=False)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0005_backfill_book_completion_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="completed_manually",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(set_manual_completion_flags, unset_manual_completion_flags),
    ]
