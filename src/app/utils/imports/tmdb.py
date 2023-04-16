from aiohttp import ClientSession
from asyncio import ensure_future, gather, run
from csv import DictReader
from decouple import config

import datetime
import logging

from app.models import Media, Season
from app.utils import helpers

TMDB_API = config("TMDB_API", default="")
logger = logging.getLogger(__name__)


def import_tmdb(file, user):
    logger.info(f"Importing from TMDB csv file to {user}")

    if "ratings" in file.name:
        status = "Completed"
    else:
        status = "Planning"

    if not file.name.endswith(".csv"):
        logger.error(
            'Error importing your list, make sure it\'s a CSV file containing the word "ratings" or "watchlist" in the name'
        )
        return False

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    run(tmdb_get_media_list(reader, user, status))

    logger.info("Finished importing from TMDB csv file")

    return True


async def tmdb_get_media_list(reader, user, status):
    async with ClientSession() as session:
        task = []
        for row in reader:
            if await Media.objects.filter(
                media_id=row["TMDb ID"],
                media_type=row["Type"],
                user=user,
            ).aexists():
                logger.warning(
                    f"{row['Type'].capitalize()}: {row['Name']} ({row['TMDb ID']}) already exists in database. Skipping..."
                )
            else:
                # Checks if is a tv show or movie because it could be episode which is not supported
                if row["Type"] == "tv":
                    url = f"https://api.themoviedb.org/3/tv/{row['TMDb ID']}?api_key={TMDB_API}"
                    task.append(
                        ensure_future(tmdb_get_media(session, url, row, user, status))
                    )
                    logger.info(
                        f"TV: {row['Name']} ({row['TMDb ID']}) added to import list."
                    )

                elif row["Type"] == "movie":
                    url = f"https://api.themoviedb.org/3/movie/{row['TMDb ID']}?api_key={TMDB_API}"
                    task.append(
                        ensure_future(tmdb_get_media(session, url, row, user, status))
                    )
                    logger.info(
                        f"Movie: {row['Name']} ({row['TMDb ID']}) added to import list."
                    )
        await gather(*task)


async def tmdb_get_media(session, url, row, user, status):
    async with session.get(url) as resp:
        response = await resp.json()

        if row["Your Rating"] == "":
            score = None
        else:
            score = float(row["Your Rating"])

        if response["poster_path"] is None:
            image = "none.svg"
        else:
            filename = await helpers.download_image_async(
                session,
                f"https://image.tmdb.org/t/p/w300{response['poster_path']}",
                row["Type"],
            )
            image = f"{filename}"

        if "number_of_episodes" in response and status == "Completed":
            progress = response["number_of_episodes"]
        else:
            progress = 0

        start_date = datetime.datetime.strptime(
            row["Date Rated"], "%Y-%m-%dT%H:%M:%SZ"
        ).date()

        media = await Media.objects.acreate(
            media_id=row["TMDb ID"],
            title=row["Name"],
            media_type=row["Type"],
            score=score,
            progress=progress,
            status=status,
            user=user,
            image=image,
            start_date=start_date,
            end_date=None,
        )

        if "number_of_seasons" in response:
            seasons_list = []
            if response["seasons"][0]["season_number"] == 0:
                offset = 0
            else:
                offset = 1

            for season_num in range(offset, response["number_of_seasons"] + 1):
                season_obj = Season(
                    parent=media,
                    title=row["Name"],
                    number=season_num,
                    score=score,
                    status=status,
                    progress=0,
                    start_date=start_date,
                    end_date=None,
                )

                # if completed, progress is the number of episodes in season
                if ("episode_count" in response["seasons"][season_num] and status == "Completed"):
                    season_obj.progress = response["seasons"][season_num - offset]["episode_count"]

                seasons_list.append(season_obj)
            await Season.objects.abulk_create(seasons_list)
