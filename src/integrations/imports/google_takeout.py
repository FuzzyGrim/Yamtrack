"""Import YouTube Music listens and YouTube watches from Google Takeout."""

from __future__ import annotations

import json
import logging
import re
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from django.apps import apps
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

import app
from app.models import MediaTypes, Sources, Status
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

# Takeout archives can be large; keep the uncompressed size under a hard cap.
MAX_TAKEOUT_UNCOMPRESSED_BYTES = 500 * 1024 * 1024

WATCH_HISTORY_BASENAME = "watch-history.json"
WATCHED_PREFIX = "Watched "
TOPIC_SUFFIX = " - Topic"
YOUTUBE_MUSIC_HEADER = "YouTube Music"
YOUTUBE_HEADER = "YouTube"

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Public YouTube thumbnail CDN — no API key required. hqdefault is present for
# nearly all videos; maxresdefault is higher quality but often missing.
YOUTUBE_THUMBNAIL_URL = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def youtube_thumbnail_url(video_id):
    """Return the public thumbnail URL for a YouTube video ID."""
    return YOUTUBE_THUMBNAIL_URL.format(video_id=video_id)


def importer(file, user, mode):
    """Import YouTube Music listens and YouTube video watches from Takeout."""
    return GoogleTakeoutImporter(file, user, mode).import_data()


class GoogleTakeoutImporter:
    """Import watch-history.json from a Google Takeout zip or JSON file."""

    def __init__(self, file, user, mode):
        """Initialize the importer with file, user, and mode.

        Args:
            file: Uploaded Takeout zip or watch-history.json
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        self.existing_media = helpers.get_existing_media(user)
        self.to_update = defaultdict(dict)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized Google Takeout importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import YouTube Music and YouTube watches from the uploaded file."""
        entries = self._load_entries()
        music_tracks, videos = self._aggregate_entries(entries)

        for track in music_tracks.values():
            try:
                self._process_track(track, MediaTypes.MUSIC.value)
            except Exception as error:
                error_msg = _("Error processing music entry: %(title)s") % {
                    "title": track["title"],
                }
                raise MediaImportUnexpectedError(error_msg) from error

        for video in videos.values():
            try:
                self._process_track(video, MediaTypes.VIDEO.value)
            except Exception as error:
                error_msg = _("Error processing video entry: %(title)s") % {
                    "title": video["title"],
                }
                raise MediaImportUnexpectedError(error_msg) from error

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)
        helpers.bulk_update_media(
            self.to_update,
            helpers.EXISTING_MEDIA_UPDATE_FIELDS,
            self.user,
        )

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        # Count updates toward the imported totals so "new" mode feedback
        # still reports media that gained listens/watches.
        for media_type, media_map in self.to_update.items():
            imported_counts[media_type] = imported_counts.get(media_type, 0) + len(
                media_map
            )

        logger.info(
            "Google Takeout import completed for user %s: %s",
            self.user.username,
            imported_counts,
        )
        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages if self.warnings else None

    def _load_entries(self):
        """Load watch-history entries from a zip archive or raw JSON file."""
        name = getattr(self.file, "name", "") or ""
        lower_name = name.lower()

        if lower_name.endswith(".zip") or self._looks_like_zip():
            return self._load_from_zip()

        return self._load_from_json()

    def _looks_like_zip(self):
        """Return whether the uploaded file starts with a zip local header."""
        position = self.file.tell()
        try:
            return self.file.read(4) == b"PK\x03\x04"
        finally:
            self.file.seek(position)

    def _load_from_zip(self):
        """Extract watch-history.json from a Takeout zip archive."""
        try:
            archive = zipfile.ZipFile(self.file)
        except zipfile.BadZipFile as error:
            msg = _("The uploaded file is not a valid Google Takeout archive.")
            raise MediaImportError(msg) from error

        uncompressed_size = 0
        history_path = None

        for info in archive.infolist():
            if info.is_dir():
                continue
            uncompressed_size += info.file_size
            if uncompressed_size > MAX_TAKEOUT_UNCOMPRESSED_BYTES:
                msg = _(
                    "The uncompressed Google Takeout archive is too large to import."
                )
                raise MediaImportError(msg)

            basename = info.filename.rsplit("/", 1)[-1].lower()
            if basename == WATCH_HISTORY_BASENAME:
                history_path = info.filename

        if history_path is None:
            msg = _(
                "Could not find watch-history.json in the archive. "
                "Export YouTube and YouTube Music history from Google Takeout "
                "as JSON."
            )
            raise MediaImportError(msg)

        try:
            raw = archive.read(history_path)
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            msg = _("Could not read watch-history.json from the archive.")
            raise MediaImportError(msg) from error

        if not isinstance(data, list):
            msg = _("watch-history.json must contain a list of activity entries.")
            raise MediaImportError(msg)

        return data

    def _load_from_json(self):
        """Load a standalone watch-history.json upload."""
        try:
            raw = self.file.read()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            msg = _(
                "Invalid file format. Upload a Google Takeout zip or "
                "watch-history.json file."
            )
            raise MediaImportError(msg) from error

        if not isinstance(data, list):
            msg = _("watch-history.json must contain a list of activity entries.")
            raise MediaImportError(msg)

        return data

    def _aggregate_entries(self, entries):
        """Group watched entries by product and YouTube video ID."""
        music = {}
        videos = {}

        for entry in entries:
            parsed = self._parse_entry(entry)
            if parsed is None:
                continue

            bucket = music if parsed["is_music"] else videos
            existing = bucket.get(parsed["media_id"])
            if existing is None:
                bucket[parsed["media_id"]] = {
                    "media_id": parsed["media_id"],
                    "title": parsed["title"],
                    "channel": parsed["channel"],
                    "url": parsed["url"],
                    "times": [parsed["watched_at"]],
                }
                continue

            existing["times"].append(parsed["watched_at"])
            # Prefer a real title over a bare URL leftover from deleted videos.
            if len(parsed["title"]) > len(existing["title"]):
                existing["title"] = parsed["title"]
            if parsed["channel"] and not existing["channel"]:
                existing["channel"] = parsed["channel"]

        return music, videos

    def _parse_entry(self, entry):
        """Parse a single Takeout activity record into a watch event."""
        if not isinstance(entry, dict):
            return None

        title = entry.get("title") or ""
        if not title.startswith(WATCHED_PREFIX):
            return None

        header = entry.get("header") or YOUTUBE_HEADER
        if header not in {YOUTUBE_HEADER, YOUTUBE_MUSIC_HEADER}:
            return None

        url = entry.get("titleUrl") or ""
        media_id = self._extract_video_id(url)
        if media_id is None:
            # Older exports sometimes put the URL in the title instead.
            media_id = self._extract_video_id(title)
        if media_id is None:
            cleaned_title = title[len(WATCHED_PREFIX) :].strip()
            self.warnings.append(
                _("%(title)s: Missing YouTube video ID - skipped")
                % {"title": cleaned_title or title}
            )
            return None

        watched_at = self._parse_time(entry.get("time"))
        if watched_at is None:
            self.warnings.append(
                _("%(media_id)s: Invalid watch time - skipped") % {"media_id": media_id}
            )
            return None

        cleaned_title = title[len(WATCHED_PREFIX) :].strip()
        if cleaned_title.startswith("http"):
            cleaned_title = media_id

        channel = self._extract_channel(entry)
        is_music = header == YOUTUBE_MUSIC_HEADER or "music.youtube.com" in url

        return {
            "media_id": media_id,
            "title": cleaned_title,
            "channel": channel,
            "url": url,
            "watched_at": watched_at,
            "is_music": is_music,
        }

    def _extract_video_id(self, value):
        """Extract an 11-character YouTube video ID from a URL or title string."""
        if not value:
            return None

        text = value.replace("\\u003d", "=").replace("\\u0026", "&")
        parsed = urlparse(text)
        query_id = parse_qs(parsed.query).get("v", [None])[0]
        if query_id and VIDEO_ID_RE.match(query_id):
            return query_id

        # Short links: youtu.be/<id>
        if parsed.netloc.endswith("youtu.be"):
            candidate = parsed.path.lstrip("/").split("/")[0]
            if VIDEO_ID_RE.match(candidate):
                return candidate

        match = re.search(r"(?:v=|/shorts/)([A-Za-z0-9_-]{11})", text)
        if match and VIDEO_ID_RE.match(match.group(1)):
            return match.group(1)

        return None

    def _extract_channel(self, entry):
        """Return the channel or artist name from Takeout subtitles."""
        subtitles = entry.get("subtitles") or []
        for subtitle in subtitles:
            if not isinstance(subtitle, dict):
                continue
            name = (subtitle.get("name") or "").strip()
            if not name:
                continue
            if name.endswith(TOPIC_SUFFIX):
                return name[: -len(TOPIC_SUFFIX)].strip()
            return name
        return ""

    def _parse_time(self, value):
        """Parse a Takeout UTC timestamp into an aware datetime."""
        if not value:
            return None
        watched_at = parse_datetime(value)
        if watched_at is None:
            return None
        if timezone.is_naive(watched_at):
            watched_at = timezone.make_aware(watched_at, UTC)
        return watched_at

    def _process_track(self, track, media_type):
        """Create or update a music/video entry from aggregated watch data."""
        media_id = track["media_id"]
        times = sorted(t for t in track["times"] if isinstance(t, datetime))
        if not times:
            return

        play_count = len(times)
        first_play = times[0]
        last_play = times[-1]

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.MANUAL.value,
            media_id,
            self.mode,
        ):
            existing = self.existing_media[media_type][Sources.MANUAL.value].get(
                media_id,
            )
            if self.mode == "new" and existing is not None:
                self._sync_existing(media_type, existing, play_count, times)
            return

        item, _created = app.models.Item.objects.update_or_create(
            media_id=media_id,
            source=Sources.MANUAL.value,
            media_type=media_type,
            defaults={
                "title": track["title"],
                "image": youtube_thumbnail_url(media_id),
            },
        )

        model = apps.get_model(app_label="app", model_name=media_type)
        notes = self._build_notes(track, play_count)

        instance = model(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=play_count,
            start_date=first_play,
            end_date=last_play,
            notes=notes,
        )
        instance._history_date = last_play
        self.bulk_media[media_type].append(instance)

    def _sync_existing(self, media_type, media, play_count, times):
        """Raise play/watch progress and dates on existing media in "new" mode."""
        values = {
            "status": Status.COMPLETED.value,
            "end_date": max(times),
        }
        if media.start_date is None:
            values["start_date"] = min(times)

        if play_count > media.progress:
            values["progress"] = play_count

        if helpers.sync_existing_media(media, values):
            self.to_update[media_type][media.pk] = media

    def _build_notes(self, track, play_count):
        """Build a short note with channel and play count context."""
        parts = []
        if track["channel"]:
            parts.append(
                _("Artist/Channel: %(channel)s") % {"channel": track["channel"]}
            )
        if track["url"]:
            parts.append(track["url"])
        if play_count > 1:
            parts.append(
                _("Plays in Takeout history: %(count)s") % {"count": play_count}
            )
        return "\n".join(parts)
