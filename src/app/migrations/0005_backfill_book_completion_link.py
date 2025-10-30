from django.db import migrations


def backfill_completion_links(apps, schema_editor):
    Book = apps.get_model('app', 'Book')
    DiaryEntry = apps.get_model('app', 'DiaryEntry')

    for book in Book.objects.filter(completion_diary_entry__isnull=True, status='Completed'):
        entries = DiaryEntry.objects.filter(user=book.user, item=book.item).order_by('-consumed_at')
        if entries.count() != 1:
            continue
        entry = entries.first()
        total_pages = book.item.total_pages
        if total_pages and book.progress and int(book.progress) >= total_pages:
            book.completion_diary_entry_id = entry.id
            book.save(update_fields=['completion_diary_entry'])


def reverse_backfill(apps, schema_editor):
    Book = apps.get_model('app', 'Book')
    Book.objects.update(completion_diary_entry=None)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0004_book_completion_diary_entry"),
    ]

    operations = [
        migrations.RunPython(backfill_completion_links, reverse_backfill),
    ]
