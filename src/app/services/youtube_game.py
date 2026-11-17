"""Read the game YouTube tags on a video from its public watch page.

The Data API has no field for this. The watch page embeds it in `ytInitialData`
(undocumented, so it can change). Only a game YouTube itself attached is returned;
a title is never guessed from the video's words.
"""

import json
import logging
import re
from datetime import timedelta

import requests
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from app.models import Item, MediaTypes, Sources, VideoGameTag, VideoPlay

logger = logging.getLogger(__name__)

VIDEO_ID = re.compile(r"^[\w-]{11}$")
MAX_BYTES = 6_000_000
MAX_DEPTH = 40
TIMEOUT = (5, 15)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
MARKER = "var ytInitialData = "
# Checked again later: a creator can tag a stream after it ends. Only while
# someone has watched the video recently; rechecking every video ever watched,
# every week, forever, was a full batch of page fetches on every run.
NOT_FOUND_RECHECK = timedelta(days=7)
RECHECK_WATCHED_WITHIN = timedelta(days=30)
UNAVAILABLE_BACKOFF = timedelta(hours=6)
MAX_FAILURES = 5


BAD_ID = "invalid video id"
TOO_LARGE = "page too large"
NO_DATA = "no ytInitialData"
UNPARSEABLE = "unparseable ytInitialData"


class WatchPageUnavailableError(Exception):
    """The page could not be fetched or no longer has the expected shape."""


def _walk(node, depth=0):
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from _walk(child, depth + 1)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child, depth + 1)


def extract_games(initial_data):
    """Return the games YouTube attached, from the current page layout.

    The section is recognised by its Gaming icon, not its "Games" heading, which
    YouTube translates.
    """
    games = []
    for node in _walk(initial_data):
        section = node.get("videoAttributesSectionViewModel")
        if not isinstance(section, dict):
            continue
        button = (section.get("footerButton") or {}).get("buttonViewModel") or {}
        if button.get("iconName") != "GAMING_LOGO":
            continue
        for entry in section.get("videoAttributeViewModels") or []:
            attribute = (entry or {}).get("videoAttributeViewModel") or {}
            title = (attribute.get("title") or "").strip()
            endpoint = (attribute.get("onTap") or {}).get("innertubeCommand") or {}
            game_id = (endpoint.get("browseEndpoint") or {}).get("browseId") or ""
            if title:
                games.append(
                    {
                        "title": title,
                        "game_id": game_id,
                        "year": str(attribute.get("subtitle") or ""),
                    }
                )
    return games


def fetch_initial_data(video_id):
    """Download the watch page and parse its embedded data."""
    if not VIDEO_ID.match(video_id):
        raise WatchPageUnavailableError(BAD_ID)
    try:
        with requests.get(
            "https://www.youtube.com/watch",
            params={"v": video_id},
            headers=HEADERS,
            timeout=TIMEOUT,
            stream=True,
        ) as response:
            response.raise_for_status()
            body = response.raw.read(MAX_BYTES + 1, decode_content=True)
    except requests.RequestException as exc:
        raise WatchPageUnavailableError(type(exc).__name__) from exc
    if len(body) > MAX_BYTES:
        raise WatchPageUnavailableError(TOO_LARGE)
    html = body.decode("utf-8", errors="replace")
    start = html.find(MARKER)
    if start < 0:
        # A consent or bot-check page has no data; do not call that "no game".
        raise WatchPageUnavailableError(NO_DATA)
    try:
        data, _end = json.JSONDecoder().raw_decode(html[start + len(MARKER) :])
    except ValueError as exc:
        raise WatchPageUnavailableError(UNPARSEABLE) from exc
    return data


def refresh_game_tag(item_id):
    """Look up one YouTube video item and record the result. Returns the status."""
    item = Item.objects.get(pk=item_id)
    if item.source != Sources.YOUTUBE.value or item.media_type != MediaTypes.VIDEO.value:
        return None
    existing = VideoGameTag.objects.filter(item=item).first()
    failures = existing.failures if existing else 0
    try:
        games = extract_games(fetch_initial_data(item.media_id))
    except WatchPageUnavailableError as exc:
        logger.info("youtube_game: unavailable item=%s reason=%s", item.pk, exc)
        VideoGameTag.objects.update_or_create(
            item=item,
            defaults={
                "status": VideoGameTag.Status.UNAVAILABLE,
                "checked_at": timezone.now(),
                "failures": min(failures + 1, MAX_FAILURES),
            },
        )
        return VideoGameTag.Status.UNAVAILABLE
    # The first game is the one the watch page leads with.
    game = games[0] if games else None
    VideoGameTag.objects.update_or_create(
        item=item,
        defaults={
            "status": VideoGameTag.Status.FOUND if game else VideoGameTag.Status.NOT_FOUND,
            "game_title": game["title"][:255] if game else "",
            "game_id": game["game_id"][:64] if game else "",
            "release_year": game["year"][:8] if game else "",
            "checked_at": timezone.now(),
            "failures": 0,
        },
    )
    return VideoGameTag.Status.FOUND if game else VideoGameTag.Status.NOT_FOUND


def lookup_due(item):
    """Whether this item should be looked up now. Cheap: one indexed read."""
    if item.source != Sources.YOUTUBE.value:
        return False
    tag = VideoGameTag.objects.filter(item=item).first()
    if tag is None:
        return True
    age = timezone.now() - tag.checked_at
    if tag.status == VideoGameTag.Status.FOUND:
        return False
    if tag.status == VideoGameTag.Status.NOT_FOUND:
        return age >= NOT_FOUND_RECHECK and _watched_recently(item.pk)
    return tag.failures < MAX_FAILURES and age >= UNAVAILABLE_BACKOFF


def _recent_play(item_ref):
    return VideoPlay.objects.filter(
        video__item_id=item_ref,
        end_date__gte=timezone.now() - RECHECK_WATCHED_WITHIN,
    )


def _watched_recently(item_id):
    return _recent_play(item_id).exists()


def backfill_due_items(limit=20):
    """Look up a bounded batch of videos that have no tag yet or are due a recheck.

    Run on a schedule so videos reported before this existed fill in gradually
    without a burst of requests to YouTube.
    @returns How many items were looked up.
    """
    now = timezone.now()
    base = Item.objects.filter(
        source=Sources.YOUTUBE.value, media_type=MediaTypes.VIDEO.value
    )
    ids = list(
        base.filter(video_game_tag__isnull=True).values_list("pk", flat=True)[:limit]
    )
    if len(ids) < limit:
        due = Q(
            Exists(_recent_play(OuterRef("pk"))),
            video_game_tag__status=VideoGameTag.Status.NOT_FOUND,
            video_game_tag__checked_at__lte=now - NOT_FOUND_RECHECK,
        ) | Q(
            video_game_tag__status=VideoGameTag.Status.UNAVAILABLE,
            video_game_tag__failures__lt=MAX_FAILURES,
            video_game_tag__checked_at__lte=now - UNAVAILABLE_BACKOFF,
        )
        ids += list(
            base.filter(due).values_list("pk", flat=True)[: limit - len(ids)]
        )
    for pk in ids:
        refresh_game_tag(pk)
    return len(ids)
