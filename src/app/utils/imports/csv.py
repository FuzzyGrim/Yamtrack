from app.models import Anime, Manga, Movie, TV, Season, Episode
from app.utils import helpers

from csv import DictReader
from datetime import datetime

import logging
import asyncio

logger = logging.getLogger(__name__)


def import_csv(file, user):
    if not file.name.endswith(".csv"):
        logger.error(
            "Error importing your list, make sure it's a CSV file and try again."
        )
        return False

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    bulk_media = {
        "anime": [],
        "manga": [],
        "movie": [],
        "tv": [],
        "season": [],
    }

    episodes = []

    bulk_images = {
        "anime": [],
        "manga": [],
        "movie": [],
        "tv": [],
        "season": [],
    }

    for row in reader:
        media_type = row["media_type"]

        match media_type:
            case "tv":
                bulk_media["tv"].append(
                    TV(
                        media_id=row["media_id"],
                        title=row["title"],
                        image=row["image"],
                        score=float(row["score"]) if row["score"] else None,
                        notes=row["notes"],
                        user=user,
                    )
                )
                # tv-f496cm9enuEsZkSPzCwnTESEK5s.jpg -> https://image.tmdb.org/t/p/w500/f496cm9enuEsZkSPzCwnTESEK5s.jpg
                bulk_images["tv"].append(
                    f"https://image.tmdb.org/t/p/w500/{row['image'].split('-', 1)[-1]}"
                )

            case "season":
                bulk_media["season"].append(
                    Season(
                        media_id=row["media_id"],
                        title=row["title"],
                        image=row["image"],
                        score=float(row["score"]) if row["score"] else None,
                        status=row["status"],
                        notes=row["notes"],
                        season_number=row["season_number"],
                        user=user,
                    )
                )
                bulk_images["season"].append(
                    f"https://image.tmdb.org/t/p/w500/{row['image'].split('-', 1)[-1]}"
                )

            case "episode":
                episodes.append(
                    {
                        "instance": Episode(
                            episode_number=row["episode_number"],
                            watch_date=datetime.strptime(
                                row["watch_date"], "%Y-%m-%d"
                            ).date()
                            if row["watch_date"]
                            else None,
                        ),
                        "media_id": row["media_id"],
                        "season_number": row["season_number"],
                    }
                )

            case "movie":
                bulk_media["movie"].append(
                    Movie(
                        media_id=row["media_id"],
                        title=row["title"],
                        image=row["image"],
                        score=float(row["score"]) if row["score"] else None,
                        status=row["status"],
                        end_date=datetime.strptime(
                            row["end_date"], "%Y-%m-%d"
                        ).date()
                        if row["end_date"]
                        else None,
                        notes=row["notes"],
                        user=user,
                    )
                )
                bulk_images["movie"].append(
                    f"https://image.tmdb.org/t/p/w500/{row['image'].split('-', 1)[-1]}"
                )

            case "anime":
                bulk_media["anime"].append(
                    Anime(
                        media_id=row["media_id"],
                        title=row["title"],
                        image=row["image"],
                        score=float(row["score"]) if row["score"] else None,
                        progress=row["progress"],
                        status=row["status"],
                        start_date=datetime.strptime(
                            row["start_date"], "%Y-%m-%d"
                        ).date()
                        if row["start_date"]
                        else None,
                        end_date=datetime.strptime(
                            row["end_date"], "%Y-%m-%d"
                        ).date()
                        if row["end_date"]
                        else None,
                        notes=row["notes"],
                        user=user,
                    )
                )
                # check if anilist or mal
                # mal -> anime-11111l.jpg (all digits except last letter "l")
                # anilist -> anime-dv332fds.jpg
                if row["image"][5:-5].isdigit() and row["image"][-5] == "l":
                    bulk_images["anime"].append(
                        f"https://api-cdn.myanimelist.net/images/anime/{row['media_id']}/{row['image'].split('-', 1)[-1]}"
                    )
                else:
                    bulk_images["anime"].append(
                        f"https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/{row['image'].split('-', 1)[-1]}"
                    )

            case "manga":
                bulk_media["manga"].append(
                    Manga(
                        media_id=row["media_id"],
                        title=row["title"],
                        image=row["image"],
                        score=float(row["score"]) if row["score"] else None,
                        progress=row["progress"],
                        status=row["status"],
                        start_date=datetime.strptime(
                            row["start_date"], "%Y-%m-%d"
                        ).date()
                        if row["start_date"]
                        else None,
                        end_date=datetime.strptime(
                            row["end_date"], "%Y-%m-%d"
                        ).date()
                        if row["end_date"]
                        else None,
                        notes=row["notes"],
                        user=user,
                    )
                )
                # check if anilist or mal
                # mal -> manga-11111l.jpg (all digits except last letter "l")
                # anilist -> manga-dv332fds.jpg
                if row["image"][5:-5].isdigit() and row["image"][-5] == "l":
                    bulk_images["manga"].append(
                        f"https://api-cdn.myanimelist.net/images/manga/{row['media_id']}/{row['image'].split('-', 1)[-1]}"
                    )
                else:
                    bulk_images["manga"].append(
                        f"https://s4.anilist.co/file/anilistcdn/media/manga/cover/medium/{row['image'].split('-', 1)[-1]}"
                    )

    # download images
    for media_type, images in bulk_images.items():
        asyncio.run(helpers.images_downloader(images, media_type))

    # bulk create media
    for media_type, medias in bulk_media.items():
        model_type = helpers.media_type_mapper(media_type)["model"]
        model_type.objects.bulk_create(medias, ignore_conflicts=True)

    for episode in episodes:
        media_id = episode["media_id"]
        season_number = episode["season_number"]
        episode["instance"].related_season = Season.objects.get(
            media_id=media_id, season_number=season_number, user=user
        )

    episode_instances = [episode["instance"] for episode in episodes]
    Episode.objects.bulk_create(episode_instances, ignore_conflicts=True)
