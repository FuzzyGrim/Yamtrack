from django.conf import settings
from django.core.files.temp import NamedTemporaryFile

import requests
import aiofiles
import os


def convert_mal_media_type(media_type):
    if media_type in ["anime", "movie", "special", "ova"]:
        return "anime"
    elif media_type in ["manga", "light_novel", "one_shot"]:
        return "manga"
    return "anime"


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