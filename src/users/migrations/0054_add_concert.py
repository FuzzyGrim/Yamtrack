from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0061_add_concert'),
        ('users', '0053_user_private_profile_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='concert_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='user',
            name='concert_layout',
            field=models.CharField(
                choices=[('grid', 'Grid'), ('table', 'Table')],
                default='grid',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='concert_sort',
            field=models.CharField(
                choices=[
                    ('score', 'Rating'),
                    ('title', 'Title'),
                    ('progress', 'Progress'),
                    ('start_date', 'Start Date'),
                    ('end_date', 'End Date'),
                ],
                default='score',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='concert_status',
            field=models.CharField(
                choices=[
                    ('All', 'All'),
                    ('Completed', 'Completed'),
                    ('In progress', 'In Progress'),
                    ('Planning', 'Planning'),
                    ('Paused', 'Paused'),
                    ('Dropped', 'Dropped'),
                ],
                default='All',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='last_search_type',
            field=models.CharField(
                choices=[
                    ('tv', 'TV Show'),
                    ('season', 'TV Season'),
                    ('episode', 'Episode'),
                    ('movie', 'Movie'),
                    ('anime', 'Anime'),
                    ('manga', 'Manga'),
                    ('game', 'Game'),
                    ('book', 'Book'),
                    ('comic', 'Comic'),
                    ('boardgame', 'Boardgame'),
                    ('concert', 'Concert'),
                ],
                default='tv',
                max_length=10,
            ),
        ),
        migrations.RemoveConstraint(
            model_name='user',
            name='last_search_type_valid',
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    last_search_type__in=[
                        'tv', 'movie', 'anime', 'manga', 'game',
                        'book', 'comic', 'boardgame', 'concert',
                    ],
                ),
                name='last_search_type_valid',
            ),
        ),
    ]
