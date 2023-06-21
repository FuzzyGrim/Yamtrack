from django.conf import settings
from app.utils import metadata
from app.models import Manga, Anime, Movie, TV, Season
from app.forms import MangaForm, AnimeForm, MovieForm, TVForm, SeasonForm

import aiofiles
import aiohttp
import asyncio
import datetime
import requests
import pathlib
import logging

logger = logging.getLogger(__name__)


def download_image(url, media_type):
    """
    Downloads an image from the given URL and saves it to the media directory with a filename
    based on the media type and the last element of the URL.

    Args:
        url (str): The URL of the image to download.
        media_type (str): The type of media the image is associated with.

    Returns:
        str: The filename of the downloaded image.
    """

    # rsplit is used to split the url at the last / and taking the last element
    # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg
    filename = f"{media_type}-{url.rsplit('/', 1)[-1]}"

    location = f"{settings.MEDIA_ROOT}/{filename}"

    # download image if it doesn't exist
    if not pathlib.Path(location).is_file():
        r = requests.get(url)
        with open(location, "wb") as f:
            f.write(r.content)

    return filename


async def images_downloader(images_to_download, media_type):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in images_to_download:
            tasks.append(download_image_async(session, url, media_type))
        await asyncio.gather(*tasks)


async def download_image_async(session, url, media_type):
    # rsplit is used to split the url at the last / and taking the last element
    # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg
    filename = f"{media_type}-{url.rsplit('/', 1)[-1]}"

    location = f"{settings.MEDIA_ROOT}/{filename}"

    # download image if it doesn't exist
    if not pathlib.Path(location).is_file():
        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(location, mode="wb")
                await f.write(await resp.read())
                await f.close()
                logger.info(f"Downloaded {filename}")


def clean_data(request, media_metadata):
    post = request.POST.copy()

    if post["score"] == "":
        post["score"] = None
    else:
        post["score"] = float(post["score"])

    if post["progress"] == "":
        post["progress"] = 0
    else:
        post["progress"] = int(post["progress"])

    if post["start"] == "":
        if post["status"] == "Watching":
            post["start"] = datetime.date.today()
        else:
            post["start"] = None

    if post["end"] == "":
        if post["status"] == "Completed":
            post["end"] = datetime.date.today()
        else:
            post["end"] = None

    if "season_number" in post:
        post["season_number"] = int(post["season_number"])
        seasons_metadata = media_metadata.get("seasons")
        selected_season_metadata = metadata.get_season_metadata_from_tv(
            post["season_number"], seasons_metadata
        )
        # if completed and has episode count, set progress to episode count
        if "episode_count" in selected_season_metadata and post["status"] == "Completed":
            post["progress"] = selected_season_metadata["episode_count"]
    else:
        if "num_episodes" in media_metadata and post["status"] == "Completed":
            post["progress"] = media_metadata["num_episodes"]

    return post


def get_client_ip(request):
    # get the user's IP address
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR")

    # if the IP address is not available in HTTP_X_FORWARDED_FOR
    if not ip_address:
        ip_address = request.META.get("REMOTE_ADDR")

    return ip_address


def media_mapper(media_type):
    """
    Maps the media type to its corresponding model and form class.

    Args:
    - media_type (str): The type of media to map.

    Returns:
    - tuple: A tuple containing the model and form class corresponding to the media type.
    """
    media_mapping = {
        'manga': {
            'model': Manga,
            'form_class': MangaForm,
        },
        'anime': {
            'model': Anime,
            'form_class': AnimeForm,
        },
        'movie': {
            'model': Movie,
            'form_class': MovieForm,
        },
        'tv': {
            'model': TV,
            'form_class': TVForm,
        },
        'season': {
            'model': Season,
            'form_class': SeasonForm,
        },
    }
    return media_mapping[media_type]['model'], media_mapping[media_type]['form_class']
