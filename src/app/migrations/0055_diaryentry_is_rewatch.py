# Generated manually to add is_rewatch field to DiaryEntry

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0054_diaryentry_liked_historicaldiaryentry_liked'),
    ]

    operations = [
        migrations.AddField(
            model_name='diaryentry',
            name='is_rewatch',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='historicaldiaryentry',
            name='is_rewatch',
            field=models.BooleanField(default=False),
        ),
    ]
