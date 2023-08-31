import asyncio
import logging
import pathlib

import aiofiles
import aiohttp
import requests
from django.conf import settings
from django.http import HttpRequest

from app.forms import AnimeForm, MangaForm, MovieForm, SeasonForm, TVForm
from app.models import TV, Anime, Manga, Movie, Season

logger = logging.getLogger(__name__)


def download_image(url: str, media_type: str) -> str:
    """Download an image from the given URL and saves it to the media directory.

    Returns the filename of the downloaded image.
    """

    filename = get_image_filename(url, media_type)

    location = f"{settings.MEDIA_ROOT}/{filename}"

    # download image if it doesn't exist
    if not pathlib.Path(location).is_file():
        r = requests.get(url, timeout=10)
        with open(location, "wb") as f:
            f.write(r.content)

    return filename


async def images_downloader(images_to_download: list, media_type: str) -> None:
    """Create tasks to download images asynchronously."""

    async with aiohttp.ClientSession() as session:
        tasks = [
            download_image_async(session, url, media_type) for url in images_to_download
        ]
        await asyncio.gather(*tasks)


async def download_image_async(
    session: aiohttp.ClientSession,
    url: str,
    media_type: str,
) -> None:
    """Download images asynchronously using aiohttp."""

    filename = get_image_filename(url, media_type)
    location = f"{settings.MEDIA_ROOT}/{filename}"

    # download image if it doesn't exist
    if not pathlib.Path(location).is_file():
        async with session.get(url) as resp:
            if resp.status == 200:  # noqa: PLR2004
                f = await aiofiles.open(location, mode="wb")
                await f.write(await resp.read())
                await f.close()


def get_image_filename(url: str, media_type: str) -> str:
    """Return filename of image based on the media type and last element of URL."""
    # rsplit is used to split the url at the last / and taking the last element
    # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg
    return f"{media_type}-{url.rsplit('/', 1)[-1]}"


def get_client_ip(request: HttpRequest) -> str:
    """Return the client's IP address.

    Used when logging for user registration and login.
    """

    # get the user's IP address
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR")

    # if the IP address is not available in HTTP_X_FORWARDED_FOR
    if not ip_address:
        ip_address = request.META.get("REMOTE_ADDR")

    return ip_address


def media_type_mapper(media_type: str) -> dict:
    """Map the media type to its corresponding model, form and other properties."""

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
