# Generated by Django 4.2 on 2023-06-21 21:06

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0002_remove_tv_progress"),
    ]

    operations = [
        migrations.AlterField(
            model_name="anime",
            name="progress",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="manga",
            name="progress",
            field=models.PositiveIntegerField(default=0),
        ),
    ]