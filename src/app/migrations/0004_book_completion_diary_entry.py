from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0003_booksession_historicalbooksession_alter_book_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="completion_diary_entry",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="completed_book",
                to="app.diaryentry",
            ),
        ),
    ]
