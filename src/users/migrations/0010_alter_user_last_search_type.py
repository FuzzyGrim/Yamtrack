# Generated by Django 5.0.6 on 2024-08-03 21:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_alter_user_options'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='last_search_type',
            field=models.CharField(choices=[('movie', 'movie'), ('tv', 'tv'), ('season', 'season'), ('episode', 'episode'), ('anime', 'anime'), ('manga', 'manga'), ('game', 'game')], default='tv', max_length=10),
        ),
    ]
