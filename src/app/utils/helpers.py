from django.conf import settings
from django.core.files.temp import NamedTemporaryFile

import aiofiles
import datetime
import os
import requests


def convert_mal_media_type(media_type):
    # replace _ with space and capitalize first letter
    return media_type.replace("_", " ").title()


def get_image_temp(url):
    img_temp = NamedTemporaryFile(delete=True)
    r = requests.get(url)
    img_temp.write(r.content)
    img_temp.flush()
    return img_temp


async def download_image(session, url, media_type):
    if url not in ["", "https://image.tmdb.org/t/p/w92None", "https://image.tmdb.org/t/p/w92"]:
        # rspilt is used to get the filename from the url by splitting the url at the last / and taking the last element
        location = f"{settings.MEDIA_ROOT}/images/{media_type}-{url.rsplit('/', 1)[-1]}"
    
        # Create the directory if it doesn't exist
        os.makedirs(f"{settings.MEDIA_ROOT}/images", exist_ok=True)

        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(location, mode='wb')
                await f.write(await resp.read())
                await f.close()

def fix_inputs(request, metadata):

    post = request.POST.copy()

    if post["score"] == "":
        post["score"] = None

    if "num_episodes" in metadata and post["status"] == "Completed":
        post["progress"] = metadata["num_episodes"]
        # tmdb doesn't count special episodes in num_episodes
        if "seasons" in metadata and metadata["seasons"][0]["season_number"] == 0:
            post["progress"] = int(post["progress"]) + metadata["seasons"][0]["episode_count"]
    elif post["progress"] == "":
        post["progress"] = 0

    if post["start"] == "":
        post["start"] = datetime.date.today()

    if post["end"] == "":
        post["end"] = None
    elif post["status"] == "Completed":
        post["end"] = datetime.date.today()

    return post