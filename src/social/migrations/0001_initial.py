from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("app", "0063_diaryentry_social_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="Activity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("verb", models.CharField(max_length=50)),
                ("target_type", models.CharField(max_length=50)),
                ("target_id", models.PositiveBigIntegerField()),
                ("visibility", models.CharField(choices=[("public", "Public"), ("followers", "Followers"), ("private", "Private"), ("unlisted", "Unlisted")], default="public", max_length=20)),
                ("snapshot", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="activities", to=settings.AUTH_USER_MODEL)),
                ("item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="activities", to="app.item")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="Block",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("blocked", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="blocks_received", to=settings.AUTH_USER_MODEL)),
                ("blocker", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="blocks_sent", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ContentLike",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("target_type", models.CharField(choices=[("diary", "Diary entry"), ("list", "Custom list")], max_length=20)),
                ("target_id", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Follow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("accepted", "Accepted")], default="accepted", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("from_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="following_edges", to=settings.AUTH_USER_MODEL)),
                ("to_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="follower_edges", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="SocialAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=80)),
                ("target_type", models.CharField(blank=True, default="", max_length=50)),
                ("target_id", models.PositiveBigIntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="social_audit_actions", to=settings.AUTH_USER_MODEL)),
                ("target_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="social_audit_targets", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(fields=["-created_at", "-id"], name="social_acti_created_317bc6_idx"),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(fields=["actor", "-created_at", "-id"], name="social_acti_actor_i_7538dd_idx"),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(fields=["item", "-created_at", "-id"], name="social_acti_item_id_b0627b_idx"),
        ),
        migrations.AddConstraint(
            model_name="block",
            constraint=models.UniqueConstraint(fields=("blocker", "blocked"), name="social_block_unique_blocker_blocked"),
        ),
        migrations.AddConstraint(
            model_name="block",
            constraint=models.CheckConstraint(condition=models.Q(("blocker", models.F("blocked")), _negated=True), name="social_block_no_self_block"),
        ),
        migrations.AddIndex(
            model_name="block",
            index=models.Index(fields=["blocker", "-created_at"], name="social_bloc_blocker_19dbef_idx"),
        ),
        migrations.AddIndex(
            model_name="block",
            index=models.Index(fields=["blocked", "-created_at"], name="social_bloc_blocked_4a77a2_idx"),
        ),
        migrations.AddConstraint(
            model_name="contentlike",
            constraint=models.UniqueConstraint(fields=("user", "target_type", "target_id"), name="social_contentlike_unique_user_target"),
        ),
        migrations.AddIndex(
            model_name="contentlike",
            index=models.Index(fields=["target_type", "target_id", "-created_at"], name="social_cont_target__7ff884_idx"),
        ),
        migrations.AddIndex(
            model_name="contentlike",
            index=models.Index(fields=["user", "-created_at"], name="social_cont_user_id_61f306_idx"),
        ),
        migrations.AddConstraint(
            model_name="follow",
            constraint=models.UniqueConstraint(fields=("from_user", "to_user"), name="social_follow_unique_from_to"),
        ),
        migrations.AddConstraint(
            model_name="follow",
            constraint=models.CheckConstraint(condition=models.Q(("from_user", models.F("to_user")), _negated=True), name="social_follow_no_self_follow"),
        ),
        migrations.AddIndex(
            model_name="follow",
            index=models.Index(fields=["from_user", "status", "-created_at"], name="social_foll_from_us_2d2f8a_idx"),
        ),
        migrations.AddIndex(
            model_name="follow",
            index=models.Index(fields=["to_user", "status", "-created_at"], name="social_foll_to_user_b9e049_idx"),
        ),
        migrations.AddIndex(
            model_name="socialauditlog",
            index=models.Index(fields=["actor", "-created_at"], name="social_soci_actor_i_bb5709_idx"),
        ),
        migrations.AddIndex(
            model_name="socialauditlog",
            index=models.Index(fields=["target_user", "-created_at"], name="social_soci_target__6e5db7_idx"),
        ),
        migrations.AddIndex(
            model_name="socialauditlog",
            index=models.Index(fields=["action", "-created_at"], name="social_soci_action_9bfaff_idx"),
        ),
    ]
