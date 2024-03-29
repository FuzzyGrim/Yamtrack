# Generated by Django 5.0.2 on 2024-02-25 21:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_user_editable'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='default_layout',
        ),
        migrations.AddField(
            model_name='user',
            name='anime_layout',
            field=models.CharField(choices=[('app/media_grid.html', 'grid'), ('app/media_table.html', 'table')], default='app/media_table.html', max_length=20),
        ),
        migrations.AddField(
            model_name='user',
            name='manga_layout',
            field=models.CharField(choices=[('app/media_grid.html', 'grid'), ('app/media_table.html', 'table')], default='app/media_grid.html', max_length=20),
        ),
        migrations.AddField(
            model_name='user',
            name='movie_layout',
            field=models.CharField(choices=[('app/media_grid.html', 'grid'), ('app/media_table.html', 'table')], default='app/media_grid.html', max_length=20),
        ),
        migrations.AddField(
            model_name='user',
            name='season_layout',
            field=models.CharField(choices=[('app/media_grid.html', 'grid'), ('app/media_table.html', 'table')], default='app/media_grid.html', max_length=20),
        ),
        migrations.AddField(
            model_name='user',
            name='tv_layout',
            field=models.CharField(choices=[('app/media_grid.html', 'grid'), ('app/media_table.html', 'table')], default='app/media_grid.html', max_length=20),
        ),
    ]
