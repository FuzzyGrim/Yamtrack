# Generated by Django 5.0.3 on 2024-03-16 11:20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_alter_user_anime_layout_alter_user_manga_layout_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='editable',
        ),
        migrations.AddField(
            model_name='user',
            name='is_demo',
            field=models.BooleanField(default=False),
        ),
    ]