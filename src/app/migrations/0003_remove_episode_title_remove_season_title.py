# Generated by Django 4.2 on 2023-05-06 15:49

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0002_episode"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="episode",
            name="title",
        ),
        migrations.RemoveField(
            model_name="season",
            name="title",
        ),
    ]
