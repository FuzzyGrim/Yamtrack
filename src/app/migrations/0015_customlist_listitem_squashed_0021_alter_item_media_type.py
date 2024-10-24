# Generated by Django 5.0.7 on 2024-09-09 09:33

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):

    replaces = [('app', '0015_customlist_listitem'), ('app', '0016_rename_user_customlist_owner_and_more'), ('app', '0017_remove_customlistitem_custom_list_and_more'), ('app', '0018_item'), ('app', '0019_anime_item_episode_item_game_item_manga_item_and_more'), ('app', '0020_alter_episode_options_alter_anime_unique_together_and_more'), ('app', '0021_alter_item_media_type')]

    dependencies = [
        ('app', '0001_squashed_0014_historicalanime_historicalepisode_historicalgame_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomList',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, default='')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('collaborators', models.ManyToManyField(blank=True, related_name='collaborated_lists', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
                'unique_together': {('name', 'owner')},
            },
        ),
        migrations.CreateModel(
            name='Item',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('media_id', models.PositiveIntegerField()),
                ('media_type', models.CharField(max_length=12)),
                ('title', models.CharField(max_length=255)),
                ('image', models.URLField()),
                ('season_number', models.PositiveIntegerField(null=True)),
                ('episode_number', models.PositiveIntegerField(null=True)),
            ],
            options={
                'ordering': ['media_id'],
                'unique_together': {('media_id', 'media_type', 'season_number', 'episode_number')},
            },
        ),
        migrations.CreateModel(
            name='CustomListItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date_added', models.DateTimeField(auto_now_add=True)),
                ('custom_list', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.customlist')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.item')),
            ],
            options={
                'ordering': ['date_added'],
                'unique_together': {('item', 'custom_list')},
            },
        ),
        migrations.AddField(
            model_name='customlist',
            name='items',
            field=models.ManyToManyField(blank=True, related_name='custom_lists', through='app.CustomListItem', to='app.item'),
        ),
        migrations.DeleteModel(
            name='CustomListItem',
        ),
        migrations.DeleteModel(
            name='CustomList',
        ),
        migrations.DeleteModel(
            name='Item',
        ),
        migrations.CreateModel(
            name='Item',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('media_id', models.PositiveIntegerField()),
                ('media_type', models.CharField(max_length=12)),
                ('title', models.CharField(max_length=255)),
                ('image', models.URLField()),
                ('season_number', models.PositiveIntegerField(null=True)),
                ('episode_number', models.PositiveIntegerField(null=True)),
            ],
            options={
                'ordering': ['media_id'],
                'unique_together': {('media_id', 'media_type', 'season_number', 'episode_number')},
            },
        ),
        migrations.AddField(
            model_name='anime',
            name='item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='app.item'),
        ),
        migrations.AddField(
            model_name='episode',
            name='item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='app.item'),
        ),
        migrations.AddField(
            model_name='game',
            name='item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='app.item'),
        ),
        migrations.AddField(
            model_name='manga',
            name='item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='app.item'),
        ),
        migrations.AddField(
            model_name='movie',
            name='item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='app.item'),
        ),
        migrations.AddField(
            model_name='season',
            name='item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='app.item'),
        ),
        migrations.AddField(
            model_name='tv',
            name='item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='app.item'),
        ),
        migrations.AlterModelOptions(
            name='episode',
            options={'ordering': ['related_season', 'item']},
        ),
        migrations.AlterUniqueTogether(
            name='anime',
            unique_together={('item', 'user')},
        ),
        migrations.AlterUniqueTogether(
            name='episode',
            unique_together={('related_season', 'item')},
        ),
        migrations.AlterUniqueTogether(
            name='game',
            unique_together={('item', 'user')},
        ),
        migrations.AlterUniqueTogether(
            name='manga',
            unique_together={('item', 'user')},
        ),
        migrations.AlterUniqueTogether(
            name='movie',
            unique_together={('item', 'user')},
        ),
        migrations.AlterUniqueTogether(
            name='season',
            unique_together={('related_tv', 'item')},
        ),
        migrations.AlterUniqueTogether(
            name='tv',
            unique_together={('item', 'user')},
        ),
        migrations.RemoveField(
            model_name='anime',
            name='image',
        ),
        migrations.RemoveField(
            model_name='anime',
            name='media_id',
        ),
        migrations.RemoveField(
            model_name='anime',
            name='title',
        ),
        migrations.RemoveField(
            model_name='episode',
            name='episode_number',
        ),
        migrations.RemoveField(
            model_name='game',
            name='image',
        ),
        migrations.RemoveField(
            model_name='game',
            name='media_id',
        ),
        migrations.RemoveField(
            model_name='game',
            name='title',
        ),
        migrations.RemoveField(
            model_name='manga',
            name='image',
        ),
        migrations.RemoveField(
            model_name='manga',
            name='media_id',
        ),
        migrations.RemoveField(
            model_name='manga',
            name='title',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='image',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='media_id',
        ),
        migrations.RemoveField(
            model_name='movie',
            name='title',
        ),
        migrations.RemoveField(
            model_name='season',
            name='image',
        ),
        migrations.RemoveField(
            model_name='season',
            name='media_id',
        ),
        migrations.RemoveField(
            model_name='season',
            name='season_number',
        ),
        migrations.RemoveField(
            model_name='season',
            name='title',
        ),
        migrations.RemoveField(
            model_name='tv',
            name='image',
        ),
        migrations.RemoveField(
            model_name='tv',
            name='media_id',
        ),
        migrations.RemoveField(
            model_name='tv',
            name='title',
        ),
        migrations.AlterField(
            model_name='item',
            name='media_type',
            field=models.CharField(choices=[('movie', 'Movie'), ('tv', 'TV Show'), ('season', 'Season'), ('episode', 'Episode'), ('anime', 'Anime'), ('manga', 'Manga'), ('game', 'Game')], max_length=10),
        ),
    ]
