from app.models import Episode

from datetime import date
import logging

logger = logging.getLogger(__name__)


def add_episodes_for_season(season):
    """
    Creates episodes when season is added or when season's progress is updated
    """

    for ep_num in range(1, season.progress + 1):
        Episode.objects.create(season=season, number=ep_num, watch_date=date.today())
        logger.info(f"Added episode {ep_num} of season {season.number} of {season.parent.title}")
