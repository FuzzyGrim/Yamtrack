from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0067_aggregated_public_ratings'),
    ]

    operations = [
        migrations.AddField(
            model_name='itemrating',
            name='imdb_votes',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='itemrating',
            name='tmdb_votes',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
