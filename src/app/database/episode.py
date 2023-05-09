from app.models import Episode
import logging

logger = logging.getLogger(__name__)


def add_update_episodes(request, episodes_checked, season_metadata, season_db):
    episodes_to_create = []
    episdoes_to_update = []

    for episode in episodes_checked:
        episode = int(episode)
        if "release" in request.POST:
            # set watch date to air date if available
            if season_metadata["episodes"][episode - 1]["air_date"]:
                watch_date = season_metadata["episodes"][episode - 1]["air_date"]
            else:
                watch_date = None
        else:
            # set watch date from form
            watch_date = request.POST.get("date")

        try:
            episode_db = Episode.objects.get(season=season_db, number=episode)
            episode_db.watch_date = watch_date
            episdoes_to_update.append(episode_db)

        except Episode.DoesNotExist:
            episode_db = Episode(
                season=season_db,
                number=episode,
                watch_date=watch_date,
            )
            episodes_to_create.append(episode_db)

    Episode.objects.bulk_create(episodes_to_create)
    Episode.objects.bulk_update(episdoes_to_update, ["watch_date"])

    logger.info(f"Added and updates episodes for {season_db}")
