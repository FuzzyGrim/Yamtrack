from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0056_merge_20250917_0045"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="poster_accent_color",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
    ]

