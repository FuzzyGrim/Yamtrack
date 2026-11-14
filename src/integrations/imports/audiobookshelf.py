"""Audiobookshelf importer for audiobook and podcast progress."""

import hashlib
import logging
import re
import time
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

import app
from app import helpers as app_helpers
from app.log_safety import exception_summary
from app.models import (
    MediaTypes,
    Podcast,
    PodcastEpisode,
    PodcastShow,
    PodcastShowTracker,
    Sources,
    Status,
)
from app.providers import services
from app.services.synced_status import keep_held_status
from integrations import audiobookshelf_cover, connection_health, import_progress
from integrations.imports.helpers import (
    MediaImportError,
    decrypt_or_raise,
    stamp_entry_source,
)
from integrations.models import AudiobookshelfAccount
from integrations.safe_fetch import send_to_self_hosted

logger = logging.getLogger(__name__)

ENTRY_SOURCE = "audiobookshelf"
HTTP_BAD_REQUEST = 400
LISTENING_SESSION_PAGE_SIZE = 100
LISTENING_SESSION_MAX_PAGES = 20
BOOK_METADATA_PROVIDER_ORDER = (
    Sources.HARDCOVER.value,
    Sources.OPENLIBRARY.value,
)
TITLE_MATCH_THRESHOLD = 0.72
# How long a book whose provider enrichment came back thin is left alone before
# the providers are tried again. Without this, any book the providers can't
# match - a common case for non-English audiobooks, which carry an ASIN and no
# ISBN - is flagged unhealthy by _needs_book_repair on *every* sync and
# re-queries Hardcover/OpenLibrary forever (see #861).
BOOK_REPAIR_COOLDOWN = timedelta(days=30)
# Blank posters are the visible symptom of #861, so they get a much shorter
# cooldown: an install upgrading past the cover fix heals within a day instead
# of waiting a month.
BOOK_COVER_REPAIR_COOLDOWN = timedelta(days=1)
# (strptime format, characters to feed it) pairs, most specific first.
PUBLISHED_DATE_FORMATS = (
    ("%Y-%m-%d", 10),
    ("%d-%b-%Y", 11),
    ("%Y-%m", 7),
    ("%Y", 4),
)


class AudiobookshelfClientError(Exception):
    """Base ABS client error."""


class AudiobookshelfAuthError(AudiobookshelfClientError):
    """ABS auth failure."""


class AudiobookshelfClient:
    """Thin API client for Audiobookshelf."""

    def __init__(self, base_url: str, token: str):
        """Initialize with server base URL and API token."""
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, path: str, max_retries: int = 3, base_delay: float = 0.5):
        """GET a path, retrying transient network failures.

        A single dropped connection (TLS handshake timeout, reset, etc.) is
        common on self-hosted setups and shouldn't be treated the same as a
        real API error - retry it a few times before giving up (see #1047).
        """
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            try:
                response = send_to_self_hosted(
                    requests.get,
                    url,
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=20,
                )
            except requests.exceptions.RequestException:
                attempt += 1
                if attempt >= max_retries:
                    raise
                time.sleep(base_delay * attempt)
                continue
            break
        if response.status_code in (401, 403):
            msg = "Audiobookshelf token is invalid or expired"
            raise AudiobookshelfAuthError(msg)
        if response.status_code >= HTTP_BAD_REQUEST:
            msg = f"Audiobookshelf request failed ({response.status_code}) for {path}"
            raise AudiobookshelfClientError(msg)
        return response.json()

    def get_me(self):
        """Return authenticated user payload."""
        return self._request("/api/me")

    def get_listening_sessions(self, page: int, items_per_page: int):
        """Return one page (0-indexed, newest first) of the user's sessions."""
        return self._request(
            f"/api/me/listening-sessions?itemsPerPage={items_per_page}&page={page}",
        )

    def get_library_item(self, library_item_id: str, *, expanded: bool = False):
        """Return metadata for an Audiobookshelf library item.

        Podcast library items only expose per-episode durations in the
        expanded payload, so podcast lookups must pass expanded=True.
        """
        path = f"/api/items/{library_item_id}"
        if expanded:
            path = f"{path}?expanded=1"
        return self._request(path)


def importer(identifier, user, mode):
    """Import Audiobookshelf progress for a user."""
    return AudiobookshelfImporter(user).import_data()


class AudiobookshelfImporter:
    """Import progress from Audiobookshelf."""

    def __init__(self, user):
        """Initialize importer and validate account access."""
        self.user = user
        try:
            self.account = user.audiobookshelf_account
        except AudiobookshelfAccount.DoesNotExist as error:
            msg = "Connect Audiobookshelf before importing"
            raise MediaImportError(msg) from error

        try:
            token = decrypt_or_raise(self.account.api_token)
        except MediaImportError as error:
            connection_health.record_failure(self.account, str(error), auth=True)
            raise

        self.client = AudiobookshelfClient(self.account.base_url, token)
        self.warnings = []
        self.enable_provider_enrichment = not settings.TESTING

    def import_data(self):
        """Import changed progress entries since the cursor."""
        imported_counts = defaultdict(int)
        try:
            me = self.client.get_me()
        except AudiobookshelfAuthError as error:
            connection_health.record_failure(self.account, str(error), auth=True)
            raise MediaImportError(str(error)) from error
        except requests.exceptions.RequestException as error:
            # An unreachable or unresponsive server is not a rejected token, so
            # the account is not marked broken (see connection-health.md).
            msg = (
                "Audiobookshelf server did not respond "
                f"({exception_summary(error)})"
            )
            connection_health.record_failure(self.account, msg, auth=False)
            raise MediaImportError(msg) from error

        progress_entries = me.get("mediaProgress") or []
        last_sync_ms = self.account.last_sync_ms or 0
        book_progress_entries, podcast_progress_entries = self._split_progress_entries(
            progress_entries,
        )
        existing_items = self._existing_book_items(book_progress_entries)
        changed_entries, unchanged_entries = self._partition_book_progress_entries(
            book_progress_entries,
            last_sync_ms,
        )
        max_seen, changed_processed = self._import_changed_books(
            changed_entries,
            existing_items,
            imported_counts,
            last_sync_ms,
        )

        if changed_entries:
            logger.info(
                "Audiobookshelf changed book entries processed=%s imported=%s",
                len(changed_entries),
                changed_processed,
            )

        repair_candidates, skipped_healthy_repairs = self._select_repair_candidates(
            unchanged_entries,
            existing_items,
        )

        if unchanged_entries:
            logger.info(
                "Audiobookshelf unchanged repair candidates=%s skipped_healthy=%s",
                len(repair_candidates),
                skipped_healthy_repairs,
            )

        repaired_count = self._repair_unchanged_books(
            repair_candidates,
            existing_items,
            imported_counts,
        )

        if repair_candidates:
            logger.info(
                "Audiobookshelf unchanged book repairs applied=%s",
                repaired_count,
            )

        self._import_listening_sessions()

        changed_podcast_entries, _ = self._partition_book_progress_entries(
            podcast_progress_entries,
            last_sync_ms,
        )
        podcast_max_seen, podcast_processed = self._import_changed_podcast_episodes(
            changed_podcast_entries,
            imported_counts,
            last_sync_ms,
        )
        # The cursor is shared with the book pass, so it has to cover podcast
        # entries too or their progress is replayed on every run.
        max_seen = max(max_seen, podcast_max_seen)

        if changed_podcast_entries:
            logger.info(
                "Audiobookshelf changed podcast entries processed=%s imported=%s",
                len(changed_podcast_entries),
                podcast_processed,
            )

        self.account.last_sync_ms = max_seen
        self.account.last_sync_at = timezone.now()
        self.account.connection_broken = False
        self.account.last_error_message = ""
        self.account.save(
            update_fields=[
                "last_sync_ms",
                "last_sync_at",
                "connection_broken",
                "last_error_message",
                "updated_at",
            ],
        )

        return dict(imported_counts), "\n".join(dict.fromkeys(self.warnings))

    def _upsert_book(
        self,
        progress_entry: dict[str, Any],
        item_metadata: dict[str, Any],
    ):
        library_item_id = str(progress_entry.get("libraryItemId"))
        media_id = self._stable_media_id(self.account.base_url, library_item_id)

        media_info = item_metadata.get("media") or {}
        metadata = (
            media_info.get("metadata") or item_metadata.get("mediaMetadata") or {}
        )

        fallback_title = f"Audiobookshelf {library_item_id}"
        title = metadata.get("title") or item_metadata.get("title") or fallback_title

        authors_list = self._extract_author_names(metadata)
        isbn_values = self._extract_isbns(metadata)
        publishers = self._extract_publisher(metadata)
        genres = self._extract_genres(metadata)
        series_name, series_position = self._extract_series_info(metadata)

        image = self._resolve_cover_image(library_item_id, item_metadata)

        # Enrichment is still worth running for thin metadata even when the
        # cover is already settled - it fills in authors, publisher, genres and
        # the release date.
        should_enrich = (
            self._needs_cover_from_provider(image)
            or not authors_list
            or not publishers
            or not genres
        )
        provider_metadata = (
            self._resolve_provider_metadata(
                title=title,
                authors=authors_list,
                isbns=isbn_values,
            )
            if should_enrich and self.enable_provider_enrichment
            else None
        )

        if self._needs_cover_from_provider(image):
            provider_image = (
                provider_metadata.get("image")
                if isinstance(provider_metadata, dict)
                else None
            )
            # A provider match with no cover art of its own (Hardcover returns
            # settings.IMG_NONE in that case) must not blank out a working
            # fallback cover we already have (see #861).
            if provider_image and provider_image != settings.IMG_NONE:
                image = provider_image
        if not image:
            image = settings.IMG_NONE

        provider_authors = self._extract_provider_authors(provider_metadata)
        if not authors_list and provider_authors:
            authors_list = provider_authors

        provider_isbns = self._extract_provider_isbns(provider_metadata)
        if not isbn_values and provider_isbns:
            isbn_values = provider_isbns

        if not publishers:
            publishers = self._extract_provider_publisher(provider_metadata)
        if not genres:
            genres = self._extract_provider_genres(provider_metadata)
        release_datetime = (
            app_helpers.extract_release_datetime(provider_metadata)
            if isinstance(provider_metadata, dict)
            else None
        )
        if release_datetime is None:
            release_datetime = self._extract_published_datetime(metadata)
        if not series_name:
            series_name = (
                provider_metadata.get("series_name")
                if isinstance(provider_metadata, dict)
                else None
            )
        if series_position is None:
            series_position = (
                provider_metadata.get("series_position")
                if isinstance(provider_metadata, dict)
                else None
            )

        provider_title = (
            provider_metadata.get("title")
            if isinstance(provider_metadata, dict)
            else None
        )
        title_fields = app.models.Item.title_fields_from_metadata(
            {
                "title": title,
                "original_title": provider_metadata.get("original_title")
                if isinstance(provider_metadata, dict)
                else None,
                "localized_title": provider_metadata.get("localized_title")
                if isinstance(provider_metadata, dict)
                else None,
            },
            fallback_title=provider_title or title,
        )

        if not title_fields.get("original_title"):
            title_fields["original_title"] = title_fields.get("title") or title
        if not title_fields.get("localized_title"):
            title_fields["localized_title"] = title_fields.get("title") or title
        duration_seconds = (
            media_info.get("duration") or progress_entry.get("duration") or 0
        )
        runtime_minutes = int(duration_seconds // 60) if duration_seconds else None
        metadata_fetched_at = timezone.now()

        item, _ = app.models.Item.objects.update_or_create(
            media_id=media_id,
            source=Sources.AUDIOBOOKSHELF.value,
            media_type=MediaTypes.BOOK.value,
            defaults={
                **title_fields,
                "title": title,
                "image": image,
                "authors": authors_list,
                "narrators": self._extract_narrator_names(metadata),
                "isbn": isbn_values,
                "publishers": publishers,
                "genres": genres,
                "runtime_minutes": runtime_minutes,
                "release_datetime": release_datetime,
                "series_name": series_name,
                "series_position": series_position,
                "format": "audiobook",
                "metadata_fetched_at": metadata_fetched_at,
            },
        )

        progress_seconds = int(progress_entry.get("currentTime") or 0)
        progress_minutes = max(0, progress_seconds // 60)
        is_finished = self._is_finished_progress_entry(progress_entry)

        if is_finished and progress_minutes == 0 and runtime_minutes:
            # ABS commonly resets currentTime to 0 after completion; use full runtime
            # as a fallback so completed books don't show zero progress.
            progress_minutes = runtime_minutes

        if is_finished:
            status = Status.COMPLETED.value
        elif progress_minutes > 0:
            status = Status.IN_PROGRESS.value
        else:
            status = Status.PLANNING.value

        finished_at = self._parse_datetime(progress_entry.get("finishedAt"))
        started_at = self._parse_datetime(progress_entry.get("startedAt"))

        existing = app.models.Book.objects.filter(user=self.user, item=item).first()
        defaults = keep_held_status(
            existing,
            {
                "progress": progress_minutes,
                "status": status,
                "start_date": started_at,
                "end_date": finished_at if is_finished else None,
                "notes": "Format: Audiobook (Audiobookshelf)",
            },
            self._parse_datetime(progress_entry.get("lastUpdate")),
        )
        media, _ = app.models.Book.objects.update_or_create(
            user=self.user,
            item=item,
            defaults=defaults,
        )
        return stamp_entry_source(media, ENTRY_SOURCE)

    def _is_finished_progress_entry(self, progress_entry: dict[str, Any]) -> bool:
        """Return whether ABS marks a title as completed."""
        if self._parse_bool(progress_entry.get("isFinished")):
            return True

        progress_value = progress_entry.get("progress")
        if progress_value is not None:
            try:
                if float(progress_value) >= 1:
                    return True
            except (TypeError, ValueError):
                logger.debug(
                    "Audiobookshelf progress value could not be parsed for completion: %s",
                    progress_value,
                )

        return self._parse_datetime(progress_entry.get("finishedAt")) is not None

    def _parse_bool(self, value: Any) -> bool:
        """Parse boolean-like values commonly returned by integrations."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, (int, float)):
            return value != 0
        return False

    def _import_listening_sessions(self):
        """Store dated listening sessions for audiobooks the user tracks.

        Sessions are newest first, so paging stops at the first page that holds
        nothing new. A server that cannot list sessions only costs a warning.
        """
        for page in range(LISTENING_SESSION_MAX_PAGES):
            try:
                payload = self.client.get_listening_sessions(
                    page,
                    LISTENING_SESSION_PAGE_SIZE,
                )
            except (AudiobookshelfClientError, requests.exceptions.RequestException):
                self.warnings.append("Audiobookshelf listening history unavailable")
                return
            sessions = payload.get("sessions") or []
            created = sum(self._store_listening_session(row) for row in sessions)
            if not sessions or not created or page + 1 >= (payload.get("numPages") or 0):
                return

    def _store_listening_session(self, row: dict[str, Any]) -> bool:
        """Save one session against its Book; return whether it was new."""
        library_item_id = row.get("libraryItemId")
        started_ms = self._coerce_positive_int(row.get("startedAt"))
        seconds = row.get("timeListening") or 0
        if not library_item_id or row.get("episodeId") or not started_ms:
            return False
        minutes = round(seconds / 60)
        if minutes < 1:
            return False
        current_time = row.get("currentTime")
        position_minutes = (
            round(current_time / 60) if isinstance(current_time, int | float) else None
        )

        book = app.models.Book.objects.filter(
            user=self.user,
            item__source=Sources.AUDIOBOOKSHELF.value,
            item__media_id=self._stable_media_id(
                self.account.base_url,
                library_item_id,
            ),
        ).first()
        if book is None:
            return False
        session_id = str(row.get("id") or started_ms)
        values = {
            "started_at": datetime.fromtimestamp(started_ms / 1000, tz=UTC),
            "minutes": minutes,
            "position_minutes": position_minutes,
        }
        # Each run re-reads the newest page of sessions. Re-saving one that
        # has not changed would refresh History and Statistics for its day and
        # mark Discover stale on every scheduled run.
        existing = app.models.BookListeningSession.objects.filter(
            book=book,
            session_id=session_id,
        ).first()
        if existing and all(
            getattr(existing, field) == value for field, value in values.items()
        ):
            return False
        _, created = app.models.BookListeningSession.objects.update_or_create(
            book=book,
            session_id=session_id,
            defaults=values,
        )
        return created

    def _stable_media_id(self, base_url: str, library_item_id: str):
        value = f"{base_url.strip().lower()}::{library_item_id}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

    def _split_progress_entries(self, progress_entries):
        """Split ABS progress entries into book and podcast episode entries.

        A podcast entry carries an episodeId (mediaItemType podcastEpisode);
        its libraryItemId identifies the show, not the episode.
        """
        book_progress_entries = []
        podcast_progress_entries = []
        for entry in progress_entries:
            library_item_id = entry.get("libraryItemId")
            if not library_item_id:
                continue

            if entry.get("episodeId"):
                podcast_progress_entries.append((str(library_item_id), entry))
                continue

            book_progress_entries.append((str(library_item_id), entry))
        return book_progress_entries, podcast_progress_entries

    def _partition_book_progress_entries(self, book_progress_entries, last_sync_ms):
        changed_entries = []
        unchanged_entries = []
        for library_item_id, entry in book_progress_entries:
            if int(entry.get("lastUpdate") or 0) > last_sync_ms:
                changed_entries.append((library_item_id, entry))
            else:
                unchanged_entries.append((library_item_id, entry))
        return changed_entries, unchanged_entries

    def _import_changed_books(
        self,
        changed_entries,
        existing_items,
        imported_counts,
        last_sync_ms,
    ):
        max_seen = last_sync_ms
        changed_processed = 0
        total = len(changed_entries)
        for i, (library_item_id, entry) in enumerate(changed_entries, start=1):
            import_progress.report(i, total, "Audiobookshelf: books")
            max_seen = max(max_seen, int(entry.get("lastUpdate") or 0))

            item_metadata = self._fetch_library_item(library_item_id)
            if item_metadata is None:
                continue

            media = self._upsert_book(entry, item_metadata)
            if media:
                imported_counts[MediaTypes.BOOK.value] += 1
                changed_processed += 1
                existing_items[library_item_id] = media.item
        return max_seen, changed_processed

    def _select_repair_candidates(self, unchanged_entries, existing_items):
        repair_candidates = []
        skipped_healthy_repairs = 0
        for library_item_id, entry in unchanged_entries:
            if self._needs_book_repair(existing_items.get(library_item_id)):
                repair_candidates.append((library_item_id, entry))
                continue

            skipped_healthy_repairs += 1
            logger.debug(
                "Audiobookshelf repair skipped for healthy book library_item_id=%s",
                library_item_id,
            )
        return repair_candidates, skipped_healthy_repairs

    def _repair_unchanged_books(
        self,
        repair_candidates,
        existing_items,
        imported_counts,
    ):
        repaired_count = 0
        total = len(repair_candidates)
        for i, (library_item_id, entry) in enumerate(repair_candidates, start=1):
            import_progress.report(i, total, "Audiobookshelf: repairing books")
            item_metadata = self._fetch_library_item(library_item_id)
            if item_metadata is None:
                continue

            media = self._upsert_book(entry, item_metadata)
            if media:
                imported_counts[MediaTypes.BOOK.value] += 1
                repaired_count += 1
                existing_items[library_item_id] = media.item
        return repaired_count

    def _existing_book_items(self, progress_entries):
        media_id_map = {
            library_item_id: self._stable_media_id(
                self.account.base_url,
                library_item_id,
            )
            for library_item_id, _entry in progress_entries
        }
        if not media_id_map:
            return {}

        items = app.models.Item.objects.filter(
            media_id__in=media_id_map.values(),
            source=Sources.AUDIOBOOKSHELF.value,
            media_type=MediaTypes.BOOK.value,
        )
        items_by_media_id = {item.media_id: item for item in items}
        return {
            library_item_id: items_by_media_id.get(media_id)
            for library_item_id, media_id in media_id_map.items()
            if media_id in items_by_media_id
        }

    def _fetch_library_item(self, library_item_id: str, *, expanded: bool = False):
        kwargs = {"expanded": True} if expanded else {}
        try:
            return self.client.get_library_item(library_item_id, **kwargs)
        except (AudiobookshelfClientError, requests.exceptions.RequestException):
            self.warnings.append(f"{library_item_id}: failed to fetch metadata")
            return None

    def _import_changed_podcast_episodes(
        self,
        changed_entries,
        imported_counts,
        last_sync_ms,
    ):
        """Import podcast episode progress, one library fetch per show."""
        max_seen = last_sync_ms
        processed = 0
        entries_by_show = defaultdict(list)
        for library_item_id, entry in changed_entries:
            max_seen = max(max_seen, int(entry.get("lastUpdate") or 0))
            entries_by_show[library_item_id].append(entry)

        total = len(changed_entries)
        current = 0
        for library_item_id, entries in entries_by_show.items():
            item_metadata = self._fetch_library_item(library_item_id, expanded=True)
            if item_metadata is None:
                current += len(entries)
                import_progress.report(current, total, "Audiobookshelf: podcasts")
                continue

            show = self._ensure_podcast_show(library_item_id, item_metadata)
            episodes_by_id = self._index_podcast_episodes(item_metadata)

            for entry in entries:
                current += 1
                import_progress.report(current, total, "Audiobookshelf: podcasts")
                episode_id = str(entry.get("episodeId") or "")
                episode_metadata = episodes_by_id.get(episode_id)
                if episode_metadata is None:
                    self.warnings.append(
                        f"{library_item_id}: episode {episode_id} "
                        "missing from library item",
                    )
                    continue

                if self._record_podcast_progress(show, episode_metadata, entry):
                    imported_counts[MediaTypes.PODCAST.value] += 1
                    processed += 1
        return max_seen, processed

    def _index_podcast_episodes(self, item_metadata: dict[str, Any]):
        media_info = item_metadata.get("media") or {}
        episodes = media_info.get("episodes") or []
        return {
            str(episode.get("id")): episode
            for episode in episodes
            if isinstance(episode, dict) and episode.get("id")
        }

    def _podcast_show_uuid(self, library_item_id: str):
        return f"abs_{self._stable_media_id(self.account.base_url, library_item_id)}"

    def _ensure_podcast_show(self, library_item_id: str, item_metadata: dict[str, Any]):
        """Create or update the podcast show for an ABS library item."""
        media_info = item_metadata.get("media") or {}
        metadata = media_info.get("metadata") or {}
        feed_url = str(metadata.get("feedUrl") or "").strip()
        podcast_uuid = self._podcast_show_uuid(library_item_id)

        show = None
        if feed_url:
            show = PodcastShow.objects.filter(rss_feed_url=feed_url).first()
        if show is None:
            show = PodcastShow.objects.filter(podcast_uuid=podcast_uuid).first()

        title = (
            metadata.get("title")
            or item_metadata.get("title")
            or f"Audiobookshelf {library_item_id}"
        )
        defaults = {
            "title": str(title)[:255],
            "slug": slugify(title)[:255],
            "author": str(metadata.get("author") or "")[:255],
            "description": str(metadata.get("description") or ""),
            "language": str(metadata.get("language") or "")[:10],
            "genres": self._extract_genres(metadata),
            "image": self._podcast_show_image(library_item_id, item_metadata, metadata),
            "rss_feed_url": feed_url,
        }

        if show is None:
            return PodcastShow.objects.create(
                podcast_uuid=podcast_uuid,
                source=Sources.AUDIOBOOKSHELF.value,
                **defaults,
            )

        # An existing show may have been created by another integration
        # (Pocket Casts, GPodder) for the same feed; keep its source.
        update_fields = []
        for field, value in defaults.items():
            if value and getattr(show, field) != value:
                setattr(show, field, value)
                update_fields.append(field)
        if update_fields:
            show.save(update_fields=update_fields)
        return show

    def _podcast_show_image(
        self,
        library_item_id: str,
        item_metadata: dict[str, Any],
        metadata: dict[str, Any],
    ):
        """Prefer the public feed artwork over the authenticated ABS cover."""
        image_url = self._normalize_cover_url(metadata.get("imageUrl"))
        if (
            image_url
            and urlparse(image_url).netloc.lower()
            != urlparse(
                self.account.base_url,
            ).netloc.lower()
        ):
            return image_url
        if self._abs_cover_value(item_metadata) or image_url:
            return audiobookshelf_cover.build_cover_proxy_url(
                self.account.id,
                library_item_id,
            )
        return ""

    def _ensure_podcast_episode(self, show, episode_metadata: dict[str, Any]):
        """Create or update the catalog episode for an ABS podcast episode."""
        episode_id = str(episode_metadata.get("id") or "")
        enclosure = episode_metadata.get("enclosure") or {}
        audio_url = str(enclosure.get("url") or "")[:500]
        guid = str(episode_metadata.get("guid") or "").strip()
        episode_uuid = (guid or audio_url or f"abs_{episode_id}")[:500]

        episode = PodcastEpisode.objects.filter(
            show=show,
            episode_uuid=episode_uuid,
        ).first()
        if episode is None and audio_url:
            episode = PodcastEpisode.objects.filter(
                show=show,
                audio_url=audio_url,
            ).first()

        title = str(episode_metadata.get("title") or "Unknown Episode")
        updates = {
            "title": title[:500],
            "slug": slugify(title)[:255],
            "published": self._parse_datetime(episode_metadata.get("publishedAt")),
            "duration": self._podcast_episode_duration(episode_metadata),
            "audio_url": audio_url,
            "episode_number": self._coerce_positive_int(
                episode_metadata.get("episode"),
            ),
            "season_number": self._coerce_positive_int(
                episode_metadata.get("season"),
            ),
            "file_type": str(enclosure.get("type") or "")[:50],
            "episode_type": str(episode_metadata.get("episodeType") or "")[:50],
        }

        if episode is None:
            episode, _ = PodcastEpisode.objects.get_or_create(
                show=show,
                episode_uuid=episode_uuid,
                defaults=updates,
            )
            return episode

        update_fields = []
        for field, value in updates.items():
            if value not in (None, "") and getattr(episode, field) != value:
                setattr(episode, field, value)
                update_fields.append(field)
        if update_fields:
            episode.save(update_fields=update_fields)
        return episode

    def _podcast_episode_duration(self, episode_metadata: dict[str, Any]):
        duration = episode_metadata.get("duration")
        if not duration:
            audio_file = episode_metadata.get("audioFile") or {}
            duration = audio_file.get("duration")
        try:
            seconds = int(float(duration))
        except (TypeError, ValueError):
            return None
        return seconds if seconds > 0 else None

    def _coerce_positive_int(self, value):
        """Coerce ABS season/episode numbers, which arrive as strings."""
        if value in (None, ""):
            return None
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    def _ensure_podcast_item(self, show, episode, total_seconds):
        """Return the trackable item for a podcast episode."""
        runtime_minutes = None
        if total_seconds:
            runtime_minutes = max(1, total_seconds // 60)
        elif episode.duration:
            runtime_minutes = max(1, episode.duration // 60)

        defaults = {
            "title": episode.title,
            "image": show.image or settings.IMG_NONE,
        }
        if runtime_minutes:
            defaults["runtime_minutes"] = runtime_minutes
        if episode.published:
            defaults["release_datetime"] = episode.published

        # Keyed on the show source so a feed already tracked through another
        # integration keeps a single item per episode.
        item, _ = app.models.Item.objects.get_or_create(
            media_id=episode.episode_uuid,
            source=show.source,
            media_type=MediaTypes.PODCAST.value,
            defaults=defaults,
        )
        update_fields = []
        if item.title != episode.title:
            item.title = episode.title
            update_fields.append("title")
        if runtime_minutes and item.runtime_minutes != runtime_minutes:
            item.runtime_minutes = runtime_minutes
            update_fields.append("runtime_minutes")
        if episode.published and item.release_datetime != episode.published:
            item.release_datetime = episode.published
            update_fields.append("release_datetime")
        if update_fields:
            item.save(update_fields=update_fields)
        return item

    def _record_podcast_progress(
        self,
        show,
        episode_metadata: dict[str, Any],
        progress_entry: dict[str, Any],
    ):
        """Write a single ABS podcast episode progress entry."""
        is_completed = self._is_finished_progress_entry(progress_entry)
        position_seconds = int(float(progress_entry.get("currentTime") or 0))
        if not is_completed and position_seconds <= 0:
            # Touched but never started; ABS keeps such rows around.
            return False

        episode = self._ensure_podcast_episode(show, episode_metadata)
        total_seconds = (
            int(float(progress_entry.get("duration") or 0)) or episode.duration or None
        )

        # Completed rows get lifted to the full runtime by Media.process_status.
        progress_minutes = max(1, position_seconds // 60) if position_seconds > 0 else 0

        item = self._ensure_podcast_item(show, episode, total_seconds)
        PodcastShowTracker.objects.get_or_create(
            user=self.user,
            show=show,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        end_date = None
        if is_completed:
            end_date = self._parse_datetime(
                progress_entry.get("finishedAt"),
            ) or self._parse_datetime(progress_entry.get("lastUpdate"))

        status_value = (
            Status.COMPLETED.value if is_completed else Status.IN_PROGRESS.value
        )
        provider_status = 3 if is_completed else 2

        entries = list(
            Podcast.objects.filter(user=self.user, item=item)
            .select_related("episode", "show", "item")
            .order_by("-created_at"),
        )
        latest_in_progress = next(
            (entry for entry in entries if entry.end_date is None),
            None,
        )
        latest_completed = next(
            (entry for entry in entries if entry.end_date is not None),
            None,
        )

        if latest_in_progress is not None:
            if (
                latest_in_progress.played_up_to_seconds == (position_seconds or None)
                and latest_in_progress.end_date == end_date
                and latest_in_progress.status == status_value
            ):
                return False
            self._update_podcast_row(
                latest_in_progress,
                show=show,
                episode=episode,
                status=status_value,
                progress_minutes=progress_minutes,
                position_seconds=position_seconds,
                provider_status=provider_status,
                end_date=end_date,
            )
            return True

        if (
            latest_completed is not None
            and is_completed
            and latest_completed.played_up_to_seconds == (position_seconds or None)
        ):
            return False

        Podcast.objects.create(
            entry_source=ENTRY_SOURCE,
            user=self.user,
            item=item,
            show=show,
            episode=episode,
            status=status_value,
            progress=progress_minutes,
            played_up_to_seconds=position_seconds or None,
            last_seen_status=provider_status,
            position_updated_at=timezone.now() if position_seconds else None,
            start_date=self._parse_datetime(progress_entry.get("startedAt")),
            end_date=end_date,
        )
        return True

    def _update_podcast_row(
        self,
        podcast,
        *,
        show,
        episode,
        status,
        progress_minutes,
        position_seconds,
        provider_status,
        end_date,
    ):
        """Update an existing in-progress podcast row in place."""
        updates = {
            "show": show,
            "episode": episode,
            "status": status,
            "progress": progress_minutes,
            "played_up_to_seconds": position_seconds or None,
            "last_seen_status": provider_status,
        }
        if end_date is not None:
            updates["end_date"] = end_date

        update_fields = []
        for field, value in updates.items():
            if getattr(podcast, field) != value:
                setattr(podcast, field, value)
                update_fields.append(field)

        # Keeps ?updated_since= delta sync on the playback progress API honest;
        # progressed_at only monitors 'progress', not the seconds position.
        if "played_up_to_seconds" in update_fields:
            podcast.position_updated_at = timezone.now()
            update_fields.append("position_updated_at")

        if update_fields:
            podcast.save(update_fields=update_fields)

    def _needs_book_repair(self, item):
        if item is None:
            return True

        image = item.image or ""
        # A raw ABS-hosted URL can never load in the browser, so it is always
        # worth re-repairing. This terminates by itself: the repair rewrites it
        # into a proxy URL, which _is_stale_abs_cover then ignores.
        if self._is_stale_abs_cover(image):
            return True

        if item.metadata_fetched_at is None:
            return True
        age = timezone.now() - item.metadata_fetched_at

        # A blank poster is the user-visible symptom of #861, so rows written
        # before the cover fix should heal on the next day's sync rather than
        # waiting out the full metadata cooldown. It still needs *a* cooldown:
        # when ABS genuinely has no artwork and no provider matches, the image
        # stays IMG_NONE and would otherwise be re-repaired on every sync.
        if (
            not image or image == settings.IMG_NONE
        ) and age >= BOOK_COVER_REPAIR_COOLDOWN:
            return True

        # Books the providers simply cannot match - non-English audiobooks
        # carrying an ASIN and no ISBN, say - would otherwise be flagged
        # unhealthy on every single sync and re-query Hardcover/OpenLibrary
        # forever (#861). The cooldown makes that retry periodic instead.
        if age < BOOK_REPAIR_COOLDOWN:
            return False

        # A missing ISBN is not a defect for audiobooks: ABS carries an ASIN for
        # most audio editions and no ISBN at all, so requiring one here marked
        # those items unhealthy forever and re-repaired them on every sync.
        return any(
            (
                not item.authors,
                not item.publishers,
                not item.genres,
                item.release_datetime is None,
                not item.original_title,
                not item.localized_title,
            ),
        )

    def _is_stale_abs_cover(self, image_url: str) -> bool:
        """Return True for a stored cover URL that predates the ABS cover proxy.

        Older rows (including ones written before the proxy fix, see #861) may
        still hold a raw ABS-hosted URL - even the previously "normalised"
        /api/items/.../cover form, which requires a bearer token a plain
        <img src> can never attach. Any such URL needs to be re-repaired into
        the proxy form so the poster actually loads. It deliberately says
        nothing about blank or placeholder covers: _needs_book_repair handles
        those under a cooldown, so an item ABS has no artwork for is not
        re-repaired on every sync.
        """
        if not image_url or image_url == settings.IMG_NONE:
            return False
        if audiobookshelf_cover.is_cover_proxy_url(image_url):
            # A proxy URL only resolves while its signature verifies, and
            # rotating SECRET_KEY invalidates every stored token at once.
            # Nothing else would ever rewrite them, so an unverifiable token
            # is stale rather than healthy. Re-signing it on repair makes this
            # terminate on the next pass.
            token = image_url.rsplit("/", 1)[-1]
            return audiobookshelf_cover.resolve_cover_proxy_token(token) is None
        return self._is_own_abs_host(image_url)

    def _is_own_abs_host(self, image_url: str) -> bool:
        """Return whether `image_url` points at this account's own ABS server."""
        parsed = urlparse(image_url)
        if parsed.scheme not in {"http", "https"}:
            return False
        abs_host = urlparse(self.account.base_url).netloc.lower()
        return parsed.netloc.lower() == abs_host

    def _extract_author_names(self, metadata: dict[str, Any]):
        raw_authors = metadata.get("authors")
        if not raw_authors:
            fallback_author = metadata.get("authorName") or metadata.get("author")
            raw_authors = [fallback_author] if fallback_author else []
        if not isinstance(raw_authors, list):
            raw_authors = [raw_authors]

        authors = []
        for raw_author in raw_authors:
            if isinstance(raw_author, dict):
                value = raw_author.get("name") or raw_author.get("author")
            else:
                value = raw_author
            normalized = str(value or "").strip()
            if normalized:
                authors.append(normalized)
        return list(dict.fromkeys(authors))

    def _extract_narrator_names(self, metadata: dict[str, Any]):
        raw = metadata.get("narrators") or metadata.get("narratorName") or []
        if not isinstance(raw, list):
            raw = [raw]
        names = [str(value or "").strip() for value in raw]
        return list(dict.fromkeys(name for name in names if name))

    def _extract_isbns(self, metadata: dict[str, Any]):
        candidates = []
        for key in ("isbn", "isbn10", "isbn13", "isbn_10", "isbn_13"):
            value = metadata.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            elif value:
                candidates.append(value)

        identifiers = metadata.get("identifiers")
        if isinstance(identifiers, dict):
            for key in ("isbn", "isbn10", "isbn13", "isbn_10", "isbn_13"):
                value = identifiers.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
                elif value:
                    candidates.append(value)

        normalized = []
        for candidate in candidates:
            value = candidate
            if isinstance(candidate, dict):
                value = (
                    candidate.get("value")
                    or candidate.get("identifier")
                    or candidate.get("isbn")
                )
            isbn = self._normalize_isbn(value)
            if isbn:
                normalized.append(isbn)

        return list(dict.fromkeys(normalized))

    def _normalize_isbn(self, value):
        if not value:
            return ""
        candidate = re.sub(r"[^0-9Xx]", "", str(value)).upper()
        if len(candidate) in (10, 13):
            return candidate
        return ""

    def _extract_published_datetime(self, metadata: dict[str, Any]):
        """Build a release datetime from the ABS publication fields.

        ABS exposes publishedDate ("2022-08-26") and publishedYear, which is
        usually a bare year but sometimes carries a full date ("26-Aug-2022").
        Used as a fallback when provider enrichment supplies no release date,
        so books keep a release date even without a provider match.
        """
        for raw in (metadata.get("publishedDate"), metadata.get("publishedYear")):
            if not raw:
                continue
            value = str(raw).strip()
            for fmt, length in PUBLISHED_DATE_FORMATS:
                try:
                    parsed = datetime.strptime(value[:length], fmt)  # noqa: DTZ007
                except ValueError:
                    continue
                return parsed.replace(tzinfo=UTC)
        return None

    def _extract_publisher(self, metadata: dict[str, Any]):
        raw = metadata.get("publisher") or metadata.get("publishers")
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        return str(raw or "").strip()

    def _extract_genres(self, metadata: dict[str, Any]):
        raw_genres = metadata.get("genres") or metadata.get("genre") or []
        if not isinstance(raw_genres, list):
            raw_genres = [raw_genres] if raw_genres else []
        normalized = [str(value).strip() for value in raw_genres if str(value).strip()]
        return list(dict.fromkeys(normalized))

    def _extract_series_info(self, metadata: dict[str, Any]):
        series_name = metadata.get("seriesName")
        series_position = metadata.get("seriesSequence")
        if not series_name:
            raw_series = metadata.get("series")
            if isinstance(raw_series, list) and raw_series:
                first_series = raw_series[0]
                if isinstance(first_series, dict):
                    series_name = first_series.get("name")
                    if series_position is None:
                        series_position = (
                            first_series.get("sequence")
                            or first_series.get("position")
                            or first_series.get("seriesSequence")
                        )
                else:
                    series_name = first_series
        normalized_name = str(series_name).strip() if series_name else None
        try:
            normalized_position = (
                float(series_position) if series_position is not None else None
            )
        except (TypeError, ValueError):
            normalized_position = None
        return normalized_name, normalized_position

    def _normalize_cover_url(self, cover_value):
        if not cover_value:
            return ""
        cover = str(cover_value).strip()
        if not cover:
            return ""

        parsed = urlparse(cover)
        if parsed.scheme in {"http", "https"}:
            return cover
        if parsed.scheme:
            return ""
        if cover.startswith("/"):
            return f"{self.account.base_url.rstrip('/')}{cover}"
        return urljoin(f"{self.account.base_url.rstrip('/')}/", cover)

    def _abs_cover_value(self, item_metadata: dict[str, Any]):
        """Return the raw cover value Audiobookshelf reports for an item.

        Depending on the ABS version the cover lives at the library item's top
        level, under ``media``, or only as an image entry in ``libraryFiles``.
        Reading just the top-level key made Floppy conclude the item had no
        artwork at all and fall back to whatever a metadata provider happened
        to return - or to the placeholder (see #861).
        """
        cover_value = item_metadata.get("coverPath")
        if cover_value:
            return cover_value

        media_info = item_metadata.get("media")
        if isinstance(media_info, dict) and media_info.get("coverPath"):
            return media_info["coverPath"]

        library_files = item_metadata.get("libraryFiles")
        if isinstance(library_files, list):
            for library_file in library_files:
                if not isinstance(library_file, dict):
                    continue
                if library_file.get("fileType") != "image":
                    continue
                file_metadata = library_file.get("metadata")
                path = (
                    file_metadata.get("path")
                    if isinstance(file_metadata, dict)
                    else None
                )
                if path:
                    return path
        return ""

    def _resolve_cover_image(self, library_item_id: str, item_metadata: dict[str, Any]):
        """Return the cover URL for an ABS book, preferring the user's own art.

        Audiobookshelf artwork is what the user curated, so it wins over
        Hardcover/OpenLibrary art: a provider match carrying a dead or simply
        wrong cover URL used to replace a perfectly good ABS cover (see #861).
        The ABS ``/api/items/:id/cover`` endpoint requires a bearer token a
        plain ``<img src>`` can never attach, so any ABS-hosted cover is served
        through Floppy's own authenticated proxy rather than the raw ABS URL.
        """
        cover_value = self._abs_cover_value(item_metadata)
        image = self._normalize_cover_url(cover_value)
        # A coverPath on some other host is a remote cover the user configured
        # in ABS; it is already directly loadable, so keep it as-is.
        if image and not self._is_own_abs_host(image):
            return image
        if cover_value:
            return audiobookshelf_cover.build_cover_proxy_url(
                self.account.id,
                library_item_id,
            )
        return ""

    def _needs_cover_from_provider(self, image_url):
        """Return whether provider art is needed because we have no cover.

        Audiobookshelf art now wins outright (see #861), so provider artwork
        is only used when ABS gave us nothing to show.
        """
        return not image_url or image_url == settings.IMG_NONE

    def _resolve_provider_metadata(
        self, title: str, authors: list[str], isbns: list[str]
    ):
        if not title:
            return None

        search_plan = [(isbn, True) for isbn in isbns]

        author_hint = authors[0] if authors else ""
        if author_hint:
            search_plan.append((f"{title} {author_hint}".strip(), False))
        search_plan.append((title, False))

        seen_queries = set()
        for provider_source in BOOK_METADATA_PROVIDER_ORDER:
            for query, is_isbn_query in search_plan:
                normalized_query = query.strip()
                if not normalized_query:
                    continue
                dedupe_key = (provider_source, normalized_query)
                if dedupe_key in seen_queries:
                    continue
                seen_queries.add(dedupe_key)

                try:
                    response = services.search(
                        MediaTypes.BOOK.value,
                        normalized_query,
                        1,
                        provider_source,
                    )
                except Exception as error:
                    logger.debug(
                        "Audiobookshelf metadata search failed provider=%s isbn_query=%s error=%s",
                        provider_source,
                        is_isbn_query,
                        exception_summary(error),
                    )
                    continue

                results = (
                    response.get("results", []) if isinstance(response, dict) else []
                )
                if not results:
                    continue

                candidate = (
                    results[0]
                    if is_isbn_query
                    else self._pick_best_title_match(results, title)
                )
                if not candidate:
                    continue

                try:
                    provider_metadata = services.get_media_metadata(
                        MediaTypes.BOOK.value,
                        str(candidate.get("media_id")),
                        provider_source,
                    )
                except Exception as error:
                    logger.debug(
                        "Audiobookshelf metadata fetch failed provider=%s error=%s",
                        provider_source,
                        exception_summary(error),
                    )
                    continue

                if self._provider_metadata_matches(
                    provider_metadata,
                    title=title,
                    authors=authors,
                    isbns=isbns,
                ):
                    return provider_metadata

        return None

    def _pick_best_title_match(self, results: list[dict[str, Any]], title: str):
        best_result = None
        best_score = 0.0

        for result in results[:5]:
            candidate_title = str(result.get("title") or "").strip()
            score = self._title_similarity(title, candidate_title)
            if score > best_score:
                best_score = score
                best_result = result

        if best_result and best_score >= TITLE_MATCH_THRESHOLD:
            return best_result
        return None

    def _provider_metadata_matches(
        self,
        provider_metadata: dict[str, Any] | None,
        title: str,
        authors: list[str],
        isbns: list[str],
    ):
        if not isinstance(provider_metadata, dict):
            return False

        provider_isbns = set(self._extract_provider_isbns(provider_metadata))
        if provider_isbns and set(isbns).intersection(provider_isbns):
            return True

        provider_title = str(provider_metadata.get("title") or "").strip()
        title_score = self._title_similarity(title, provider_title)
        if title_score < TITLE_MATCH_THRESHOLD:
            return False

        provider_authors = self._extract_provider_authors(provider_metadata)
        if not authors or not provider_authors:
            return True

        normalized_target_authors = {
            self._normalize_name(author)
            for author in authors
            if self._normalize_name(author)
        }
        normalized_provider_authors = {
            self._normalize_name(author)
            for author in provider_authors
            if self._normalize_name(author)
        }
        if normalized_target_authors.intersection(normalized_provider_authors):
            return True
        return title_score >= TITLE_MATCH_THRESHOLD

    def _title_similarity(self, left: str, right: str):
        normalized_left = self._normalize_name(left)
        normalized_right = self._normalize_name(right)
        if not normalized_left or not normalized_right:
            return 0.0
        return SequenceMatcher(None, normalized_left, normalized_right).ratio()

    def _normalize_name(self, value):
        """Casefold and strip punctuation while keeping non-ASCII letters.

        The old form was ``re.sub(r"[^a-z0-9]+", " ", value.lower())``, which
        deleted every non-ASCII character: a Cyrillic or Japanese title
        normalised to the empty string and so could never match a provider
        result, and accented Latin titles were degraded below
        TITLE_MATCH_THRESHOLD (see #861).

        Accents are folded off Latin letters, so "Zeitbrüch" still matches
        "Zeitbruch". They are *not* folded off other scripts, where a
        combining mark changes the word rather than its spelling: stripping
        them would collapse Japanese は/ば/ぱ and the Cyrillic ye/yo pair,
        scoring different works as a perfect match and letting a provider
        overwrite the item's metadata with another book's.
        """
        decomposed = unicodedata.normalize("NFKD", str(value or ""))
        kept = []
        base_is_ascii = False
        for char in decomposed:
            if unicodedata.combining(char):
                # NFKD decomposes an accented Latin letter to an ASCII base
                # plus its mark, so an ASCII base is exactly the case where
                # dropping the mark is a spelling variant rather than a
                # different character.
                if not base_is_ascii:
                    kept.append(char)
                continue
            base_is_ascii = char.isascii()
            kept.append(char)
        # Recompose so a mark kept above rejoins its base into a single
        # alphanumeric character; left bare it would read as punctuation
        # below and split the word in half.
        recomposed = unicodedata.normalize("NFC", "".join(kept))
        normalized = re.sub(r"[\W_]+", " ", recomposed.casefold())
        return re.sub(r"\s+", " ", normalized).strip()

    def _extract_provider_authors(self, provider_metadata: dict[str, Any] | None):
        if not isinstance(provider_metadata, dict):
            return []

        details = provider_metadata.get("details", {})
        if not isinstance(details, dict):
            details = {}

        raw_authors = details.get("authors") or details.get("author") or []
        if isinstance(raw_authors, str):
            raw_authors = [
                part.strip() for part in raw_authors.split(",") if part.strip()
            ]
        elif not isinstance(raw_authors, list):
            raw_authors = [raw_authors] if raw_authors else []

        authors = []
        for raw_author in raw_authors:
            if isinstance(raw_author, dict):
                value = raw_author.get("name") or raw_author.get("person")
            else:
                value = raw_author
            normalized = str(value or "").strip()
            if normalized:
                authors.append(normalized)
        return list(dict.fromkeys(authors))

    def _extract_provider_isbns(self, provider_metadata: dict[str, Any] | None):
        if not isinstance(provider_metadata, dict):
            return []
        details = provider_metadata.get("details", {})
        if not isinstance(details, dict):
            details = {}

        raw_isbns = details.get("isbn") or []
        if not isinstance(raw_isbns, list):
            raw_isbns = [raw_isbns] if raw_isbns else []

        normalized = []
        for raw_isbn in raw_isbns:
            isbn = self._normalize_isbn(raw_isbn)
            if isbn:
                normalized.append(isbn)
        return list(dict.fromkeys(normalized))

    def _extract_provider_publisher(self, provider_metadata: dict[str, Any] | None):
        if not isinstance(provider_metadata, dict):
            return ""
        details = provider_metadata.get("details", {})
        if not isinstance(details, dict):
            details = {}
        raw = details.get("publishers") or details.get("publisher") or ""
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        return str(raw or "").strip()

    def _extract_provider_genres(self, provider_metadata: dict[str, Any] | None):
        if not isinstance(provider_metadata, dict):
            return []
        raw_genres = provider_metadata.get("genres") or []
        if not isinstance(raw_genres, list):
            raw_genres = [raw_genres] if raw_genres else []
        normalized = [str(value).strip() for value in raw_genres if str(value).strip()]
        return list(dict.fromkeys(normalized))

    def _parse_datetime(self, value):
        if not value:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            return parsed
        return None
