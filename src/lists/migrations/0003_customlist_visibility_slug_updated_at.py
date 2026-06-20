from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


def backfill_list_slugs(apps, schema_editor):
    CustomList = apps.get_model("lists", "CustomList")
    used = {}
    for custom_list in CustomList.objects.order_by("owner_id", "id"):
        base = custom_list.name.lower().strip().replace(" ", "-") or "list"
        base = "".join(ch for ch in base if ch.isalnum() or ch == "-")[:255]
        key = custom_list.owner_id
        used.setdefault(key, set())
        slug = base
        suffix = 2
        while slug in used[key]:
            suffix_text = f"-{suffix}"
            slug = f"{base[:255 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        custom_list.slug = slug
        custom_list.save(update_fields=["slug"])
        used[key].add(slug)


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="customlist",
            name="slug",
            field=models.SlugField(
                blank=True,
                max_length=255,
                validators=[
                    django.core.validators.RegexValidator(
                        "^[a-z0-9-]*$",
                        "Use lowercase letters, numbers, and hyphens only.",
                    ),
                ],
            ),
        ),
        migrations.AddField(
            model_name="customlist",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="customlist",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("unlisted", "Unlisted"),
                    ("private", "Private"),
                ],
                default="private",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_list_slugs, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="customlist",
            constraint=models.UniqueConstraint(
                condition=models.Q(("slug", ""), _negated=True),
                fields=("owner", "slug"),
                name="lists_customlist_unique_owner_slug",
            ),
        ),
    ]
