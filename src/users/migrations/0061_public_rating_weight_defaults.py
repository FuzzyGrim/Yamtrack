import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """Change the default public-score weights for new users.

    Existing rows are left alone, including weights a user already changed.
    """

    dependencies = [
        ('users', '0060_aggregated_public_ratings'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='rating_weight_imdb',
            field=models.PositiveSmallIntegerField(
                default=3,
                help_text='Weight of the IMDb score in the public rating',
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(10),
                ],
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='rating_weight_metacritic',
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text='Weight of the Metacritic score in the public rating',
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(10),
                ],
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='rating_weight_letterboxd',
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text='Weight of the Letterboxd score in the public rating',
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(10),
                ],
            ),
        ),
    ]
