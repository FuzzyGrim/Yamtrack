from django.db import migrations, models
import django.db.models.deletion


def delete_episodes_without_item(apps, _schema_editor):
    Episode = apps.get_model("app", "Episode")
    Episode.objects.filter(item__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0060_fix_reopened_completed_tv_seasons"),
    ]

    operations = [
        migrations.RunPython(
            delete_episodes_without_item,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="episode",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="app.item",
            ),
        ),
    ]
