# Generated by Django 5.0.2 on 2024-02-26 18:07

from django.db import migrations, models


def change_layouts(apps, schema_editor):
    """Layouts names have been changed, so we need to update them."""

    User = apps.get_model("users", "User")
    for user in User.objects.all():
        user.anime_layout = "table"
        user.manga_layout = "table"
        user.tv_layout = "grid"
        user.season_layout = "grid"
        user.movie_layout = "grid"
        user.save()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0005_remove_user_default_layout_user_anime_layout_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="anime_layout",
            field=models.CharField(
                choices=[("grid", "Grid"), ("table", "Table")],
                default="table",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="manga_layout",
            field=models.CharField(
                choices=[("grid", "Grid"), ("table", "Table")],
                default="table",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="movie_layout",
            field=models.CharField(
                choices=[("grid", "Grid"), ("table", "Table")],
                default="grid",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="season_layout",
            field=models.CharField(
                choices=[("grid", "Grid"), ("table", "Table")],
                default="grid",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="tv_layout",
            field=models.CharField(
                choices=[("grid", "Grid"), ("table", "Table")],
                default="grid",
                max_length=20,
            ),
        ),
        migrations.RunPython(change_layouts),
    ]
