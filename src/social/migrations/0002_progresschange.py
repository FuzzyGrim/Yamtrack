from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0063_diaryentry_social_fields"),
        ("social", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProgressChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("previous_progress", models.JSONField()),
                ("current_progress", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="progress_changes", to=settings.AUTH_USER_MODEL)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="progress_changes", to="app.item")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="progresschange",
            index=models.Index(fields=["actor", "item", "-created_at"], name="social_prog_actor_i_023810_idx"),
        ),
        migrations.AddIndex(
            model_name="progresschange",
            index=models.Index(fields=["-created_at", "id"], name="social_prog_created_5522f8_idx"),
        ),
    ]
