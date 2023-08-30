from django.conf import settings
from app.models import Manga, Anime, Movie, TV, Season
from app.forms import MangaForm, AnimeForm, MovieForm, TVForm, SeasonForm

import aiofiles
import aiohttp
import asyncio
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

    filename = get_image_filename(url, media_type)

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
    filename = get_image_filename(url, media_type)
    location = f"{settings.MEDIA_ROOT}/{filename}"

    # download image if it doesn't exist
    if not pathlib.Path(location).is_file():
        async with session.get(url) as resp:
            if resp.status == 200:
                f = await aiofiles.open(location, mode="wb")
                await f.write(await resp.read())
                await f.close()


def get_image_filename(url, media_type):
    """
    Returns the filename of the image based on the media type and the last element of the URL.
    """
    # rsplit is used to split the url at the last / and taking the last element
    # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg
    return f"{media_type}-{url.rsplit('/', 1)[-1]}"


def get_client_ip(request):
    # get the user's IP address
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR")

    # if the IP address is not available in HTTP_X_FORWARDED_FOR
    if not ip_address:
        ip_address = request.META.get("REMOTE_ADDR")

    return ip_address


def media_type_mapper(media_type):
    """
    Maps the media type to its corresponding model and form class.

    Args:
    - media_type (str): The type of media to map.

    Returns:
    - tuple: A tuple containing properties of the media type.
    """
    media_mapping = {
        "manga": {
            "model": Manga,
            "form": MangaForm,
            "list_layout": "app/media_table.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
                ("-progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
            "img_url": {
                "mal": "https://api-cdn.myanimelist.net/images/manga/{media_id}/{image_file}",
                "anilist": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/medium/{image_file}",
            },
        },
        "anime": {
            "model": Anime,
            "form": AnimeForm,
            "list_layout": "app/media_table.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
                ("-progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
            "img_url": {
                "mal": "https://api-cdn.myanimelist.net/images/anime/{media_id}/{image_file}",
                "anilist": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/{image_file}",
            },
        },
        "movie": {
            "model": Movie,
            "form": MovieForm,
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
                ("end_date", "End Date"),
            ],
            "img_url": "https://image.tmdb.org/t/p/w500/{image_file}",
        },
        "tv": {
            "model": TV,
            "form": TVForm,
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
            ],
            "img_url": "https://image.tmdb.org/t/p/w500/{image_file}",
        },
        "season": {
            "model": Season,
            "form": SeasonForm,
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
            ],
            "img_url": "https://image.tmdb.org/t/p/w500/{image_file}",
        },
    }
    return media_mapping[media_type]
