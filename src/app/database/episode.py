from app.models import Episode
import logging

logger = logging.getLogger(__name__)


def add_update_episodes(request, episodes_checked, season_metadata, season_db):
    """
    Add new or update existing episodes for a season
    """
    episodes_to_create = []
    episdoes_to_update = []

    # create dict of episode number and air date pairs
    # cant get air date with season_metadata["episodes"][episode - 1]["air_date"]
    # because there could be missing episodes in the middle
    if "release" in request.POST:
        air_dates = {}
        for episode_metadata in season_metadata["episodes"]:
            if episode_metadata['episode_number'] in episodes_checked:
                air_dates[episode_metadata['episode_number']] = episode_metadata['air_date']

    for episode in episodes_checked:
        episode = int(episode)
        if "release" in request.POST:
            # set watch date to air date
            # air date could be null
            watch_date = air_dates.get(episode)
        else:
            # set watch date from form
            watch_date = request.POST.get("date")

        # update episode watch date
        try:
            episode_db = Episode.objects.get(season=season_db, number=episode)
            episode_db.watch_date = watch_date
            episdoes_to_update.append(episode_db)
        # create new episode if it doesn't exist
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
