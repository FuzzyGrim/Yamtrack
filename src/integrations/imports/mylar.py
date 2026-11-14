"""Mylar3 importer for comic issue collection ownership sync."""

import logging
from collections import defaultdict
from http import HTTPStatus

import requests
from django.conf import settings
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from integrations import connection_health, import_progress
from integrations.imports.helpers import (
    ConnectionAuthError,
    MediaImportError,
    decrypt_or_raise,
    find_item_across_buckets,
)
from integrations.models import MylarInstance
from integrations.safe_fetch import SelfHostedUrlError, send_to_self_hosted
from integrations.source_sync import (
    prune_collection_source_states,
    sync_collection_source_state,
)

logger = logging.getLogger(__name__)

# Mylar3 issue statuses that mean the file is on disk.
OWNED_STATUSES = frozenset({"downloaded", "archived"})
# Mylar3 answers a rejected key with HTTP 200 and one of these messages.
AUTH_ERROR_MESSAGES = frozenset({"incorrect api key", "api not enabled"})


class MylarClient:
    """Thin API client for the Mylar3 ``/api`` endpoint."""

    def __init__(self, base_url: str, api_key: str):
        """Store the server URL and API key."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, cmd: str, **params):
        try:
            response = send_to_self_hosted(
                requests.get,
                f"{self.base_url}/api",
                params={"apikey": self.api_key, "cmd": cmd, **params},
                timeout=20,
            )
        except SelfHostedUrlError as error:
            msg = f"Could not reach Mylar3: {error}"
            raise MediaImportError(msg) from error
        except requests.RequestException as error:
            # The key travels in the query string, so the exception text (which
            # repeats the URL) must not reach the stored error message.
            msg = f"Could not reach Mylar3 ({type(error).__name__})"
            raise MediaImportError(msg) from error
        if response.status_code in (401, 403):
            msg = "Mylar3 API key is invalid or unauthorized"
            raise ConnectionAuthError(msg)
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            msg = f"Mylar3 request failed ({response.status_code}) for {cmd}"
            raise MediaImportError(msg)
        try:
            payload = response.json()
        except ValueError as error:
            msg = f"Mylar3 returned an unreadable response for {cmd}"
            raise MediaImportError(msg) from error
        if not isinstance(payload, dict) or not payload.get("success"):
            error = payload.get("error") if isinstance(payload, dict) else None
            message = str((error or {}).get("message") or "unknown error")
            if message.strip().lower() in AUTH_ERROR_MESSAGES:
                msg = f"Mylar3 rejected the API key: {message}"
                raise ConnectionAuthError(msg)
            msg = f"Mylar3 request failed for {cmd}: {message}"
            raise MediaImportError(msg)
        return payload.get("data")

    def healthcheck(self):
        """Verify connection."""
        return self._request("getVersion")

    def series(self):
        """Fetch the series rows in the library."""
        return self._request("getIndex") or []

    def comic(self, comic_id):
        """Fetch one series with its issues and annuals."""
        return self._request("getComic", id=comic_id) or {}


def comic_issue_item(issue_id, title, image):
    """Return the Comic Vine issue Item, creating it from the server's data.

    The issue id is the Comic Vine issue id, so no provider lookup is
    needed; the details page fills in the rest the first time it is opened.
    """
    issue_id = str(issue_id or "").strip()
    if not issue_id.isdigit():
        return None

    identity = {
        "media_id": issue_id,
        "source": Sources.COMICVINE.value,
        "media_type": MediaTypes.COMIC_ISSUE.value,
    }
    existing = find_item_across_buckets(**identity)
    if existing:
        return existing

    image = str(image or "")
    item, _ = Item.objects.get_or_create(
        **identity,
        library_media_type=MediaTypes.COMIC_ISSUE.value,
        season_number=None,
        episode_number=None,
        defaults={
            "title": title,
            "original_title": title,
            "localized_title": title,
            "image": image if image.startswith("https://") else settings.IMG_NONE,
        },
    )
    return item


def importer(identifier, user, mode, instance_id=None):
    """Import Mylar3 collection ownership."""
    instance = (
        MylarInstance.objects.get(pk=instance_id, user=user) if instance_id else None
    )
    return MylarImporter(user, instance=instance).import_data()


class MylarImporter:
    """Mark the comic issues a Mylar3 library has on disk as owned.

    Kapowarr's importer subclasses this: both key comics by Comic Vine id, so
    only the client and how owned issues are read differ.
    """

    source = "mylar"
    label = "Mylar3"
    instances_attr = "mylar_instances"
    client_class = MylarClient

    def __init__(self, user, instance=None):
        """Bind the importer to a user with a connected instance."""
        self.user = user
        if instance is not None:
            self.instance = instance
        else:
            self.instance = getattr(user, self.instances_attr).first()
        if self.instance is None:
            msg = f"Connect {self.label} before importing"
            raise MediaImportError(msg)

        try:
            api_key = decrypt_or_raise(self.instance.api_key)
        except MediaImportError as error:
            # An unreadable stored key needs a reconnect as much as a rejected one.
            connection_health.record_failure(self.instance, error, auth=True)
            raise

        self.client = self.client_class(self.instance.base_url, api_key)
        self.warnings = []

    def import_data(self):
        """Return the import data."""
        imported_counts = defaultdict(int)
        owned_item_ids = set()

        try:
            for issue_id, title, image in self._owned_issues():
                item = self._resolve_issue_item(issue_id, title, image)
                if item is None:
                    imported_counts["skipped_missing_ids"] += 1
                    continue
                owned_item_ids.add(item.id)
                _entry, changed = sync_collection_source_state(
                    user=self.user,
                    item=item,
                    source=self.source,
                    source_instance_id=self.instance.pk,
                )
                # An unchanged copy is not an import (see radarr.py).
                if changed:
                    imported_counts["collection"] += 1
                    imported_counts["updated"] += 1
                else:
                    imported_counts["unchanged"] += 1
        except MediaImportError as error:
            connection_health.record_failure(
                self.instance,
                error,
                auth=isinstance(error, ConnectionAuthError),
            )
            raise

        # Only after every series was read: a partial run must not drop copies.
        removed = prune_collection_source_states(
            user=self.user,
            source=self.source,
            source_instance_id=self.instance.pk,
            keep_item_ids=owned_item_ids,
        )
        if removed:
            imported_counts["removed"] += removed
        self.instance.last_sync_at = timezone.now()
        connection_health.record_success(self.instance, extra_fields=["last_sync_at"])

        return dict(imported_counts), "\n".join(dict.fromkeys(self.warnings))

    def _owned_issues(self):
        """Yield (Comic Vine issue id, title, image) for each issue on disk."""
        series_rows = self.client.series()
        total = len(series_rows)
        for i, series in enumerate(series_rows, start=1):
            import_progress.report(i, total, self.label)
            comic_id = series.get("id")
            if not comic_id:
                continue
            detail = self.client.comic(comic_id)
            series_name = series.get("name") or ""
            groups = (
                (detail.get("issues"), series_name),
                (detail.get("annuals"), f"{series_name} Annual"),
            )
            for issues, name in groups:
                for issue in issues or []:
                    status = str(issue.get("status") or "").strip().lower()
                    if status not in OWNED_STATUSES:
                        continue
                    title = f"{name} #{issue.get('number') or '?'}"
                    if issue.get("name"):
                        title = f"{title}: {issue['name']}"
                    yield issue.get("id"), title, issue.get("imageURL")

    def _resolve_issue_item(self, issue_id, title, image):
        """Return the Comic Vine issue Item, creating it from the server's data."""
        return comic_issue_item(issue_id, title, image)
