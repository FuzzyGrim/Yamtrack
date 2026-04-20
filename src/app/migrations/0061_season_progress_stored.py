from django.db import migrations, models


def populate_season_progress(apps, schema_editor):
    """Set Season.progress using the original computed logic:
    - IN_PROGRESS: episode with highest rewatch count (ties broken by highest episode number)
    - Other statuses: highest watched episode number
    """
    Season = apps.get_model("app", "Season")
    Episode = apps.get_model("app", "Episode")

    in_progress_status = "In progress"

    # Gather all episodes grouped by season
    episodes_by_season = {}
    for ep in Episode.objects.values("related_season_id", "item__episode_number"):
        sid = ep["related_season_id"]
        if sid not in episodes_by_season:
            episodes_by_season[sid] = []
        episodes_by_season[sid].append(ep["item__episode_number"] or 0)

    seasons_to_update = []
    for season in Season.objects.filter(pk__in=episodes_by_season.keys()):
        ep_numbers = episodes_by_season[season.pk]

        if season.status == in_progress_status:
            # Count rewatches per episode number, pick highest count then highest number
            episode_counts = {}
            for ep_num in ep_numbers:
                episode_counts[ep_num] = episode_counts.get(ep_num, 0) + 1
            new_progress = max(
                episode_counts,
                key=lambda n: (episode_counts[n], n),
            )
        else:
            new_progress = max(ep_numbers)

        if season.progress != new_progress:
            season.progress = new_progress
            seasons_to_update.append(season)

    if seasons_to_update:
        Season.objects.bulk_update(seasons_to_update, ["progress"])


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0060_fix_reopened_completed_tv_seasons"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="progress",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="historicalseason",
            name="progress",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(
            populate_season_progress,
            migrations.RunPython.noop,
        ),
    ]
