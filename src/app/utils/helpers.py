from django.conf import settings

import aiofiles
import datetime
import requests
from pathlib import Path


def convert_mal_media_type(media_type):
    # replace _ with space and capitalize first letter
    return media_type.replace("_", " ").title()


def download_image(url, media_type):
    # rsplit is used to split the url at the last / and taking the last element
    # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg

    filename = f"{media_type}-{url.rsplit('/', 1)[-1]}"
    location = f"{settings.MEDIA_ROOT}/{filename}"

    if not Path(location).is_file():
        r = requests.get(url)
        with open(location, "wb") as f:
            f.write(r.content)

    return filename


async def download_image_async(session, url, media_type):

    # rsplit is used to split the url at the last / and taking the last element
    # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg
    filename = f"{media_type}-{url.rsplit('/', 1)[-1]}"
    location = f"{settings.MEDIA_ROOT}/{filename}"

    if not Path(location).is_file():
        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(location, mode="wb")
                await f.write(await resp.read())
                await f.close()

    return filename


def fix_inputs(request, metadata):
    post = request.POST.copy()

    if post["score"] == "":
        post["score"] = None

    if "num_episodes" in metadata and post["status"] == "Completed":
        post["progress"] = metadata["num_episodes"]
        # tmdb doesn't count special episodes in num_episodes
        if "seasons" in metadata and metadata["seasons"][0]["season_number"] == 0:
            post["progress"] = (
                int(post["progress"]) + metadata["seasons"][0]["episode_count"]
            )
    elif post["progress"] == "":
        post["progress"] = 0

    if post["start"] == "":
        post["start"] = datetime.date.today()

    if post["end"] == "":
        post["end"] = None
    elif post["status"] == "Completed":
        post["end"] = datetime.date.today()

    return post
