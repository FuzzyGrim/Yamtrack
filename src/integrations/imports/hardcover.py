"""Import a user's book library directly from Hardcover."""

import logging
from collections import defaultdict

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_PAGE_LIMIT = 100
MIN_RATING = 0.5
MAX_RATING = 5
INVALID_TOKEN_MESSAGE = "Invalid or expired Hardcover token"  # noqa: S105
USER_BOOKS_QUERY = """
query UserBooks($limit: Int!, $offset: Int!) {
  me {
    username
    user_books(limit: $limit, offset: $offset, order_by: {id: asc}) {
      book_id status_id rating date_added updated_at private_notes review_raw
      book { title pages cached_image(path: "url") }
      user_book_reads(order_by: {started_at: desc}) {
        started_at finished_at progress_pages
      }
    }
  }
}
"""
USERNAME_QUERY = "query CurrentUser { me { username } }"


def importer(token, user, mode):
    """Import books using an encrypted Hardcover personal API token."""
    return HardcoverImporter(token, user, mode).import_data()


def get_username(token):
    """Validate a raw API token and return its Hardcover username."""
    response = _api_request(token, USERNAME_QUERY, {})
    username = _get_current_user(response).get("username")
    if not username:
        raise MediaImportError(INVALID_TOKEN_MESSAGE)
    return username


def _api_request(token, query, variables):
    """Send an authenticated GraphQL request to Hardcover."""
    authorization = token if token.startswith("Bearer ") else f"Bearer {token}"
    try:
        return services.api_request(
            Sources.HARDCOVER.value,
            "POST",
            HARDCOVER_API_URL,
            params={"query": query, "variables": variables},
            headers={"Authorization": authorization},
        )
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.unauthorized:
            raise MediaImportError(INVALID_TOKEN_MESSAGE) from error
        raise


def _get_current_user(response):
    """Return the authenticated user from Hardcover's array-shaped `me` result."""
    me = response.get("data", {}).get("me")
    if isinstance(me, list):
        return me[0] if me else {}
    return me if isinstance(me, dict) else {}


class HardcoverImporter:
    """Class to handle importing a user's books from Hardcover."""

    def __init__(self, encrypted_token, user, mode):
        """Initialize an importer for a user and import mode."""
        self.token = helpers.decrypt(encrypted_token)
        self.user = user
        self.mode = mode
        self.warnings = []
        self.existing_media = helpers.get_existing_media(user)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)

    def import_data(self):
        """Fetch all library pages, then persist the imported books."""
        for user_book in self._get_user_books():
            try:
                self._process_user_book(user_book)
            except MediaImportError as error:
                self.warnings.append(str(error))
            except Exception as error:
                book = user_book.get("book", {})
                message = (
                    "Error processing Hardcover book: "
                    f"{book.get('title', user_book['book_id'])}"
                )
                raise MediaImportUnexpectedError(message) from error

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)
        return (
            {media_type: len(media) for media_type, media in self.bulk_media.items()},
            "\n".join(dict.fromkeys(self.warnings)),
        )

    def _get_user_books(self):
        offset = 0
        user_books = []
        while True:
            response = _api_request(
                self.token,
                USER_BOOKS_QUERY,
                {"limit": HARDCOVER_PAGE_LIMIT, "offset": offset},
            )
            page = _get_current_user(response).get("user_books", [])
            user_books.extend(page)
            if len(page) < HARDCOVER_PAGE_LIMIT:
                return user_books
            offset += HARDCOVER_PAGE_LIMIT

    def _process_user_book(self, user_book):
        status = self._get_status(user_book["status_id"])
        book = user_book.get("book") or {}
        title = book.get("title") or f"Hardcover book {user_book['book_id']}"
        if status is None:
            self.warnings.append(
                f"{title} ({user_book['book_id']}): Unsupported Hardcover status "
                f"{user_book['status_id']}.",
            )
            return

        media_id = str(user_book["book_id"])
        item, _ = app.models.Item.objects.update_or_create(
            media_id=media_id,
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            defaults={"title": title, "image": self._get_image(book)},
        )
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.BOOK.value,
            Sources.HARDCOVER.value,
            media_id,
            self.mode,
        ):
            return

        latest_read = (user_book.get("user_book_reads") or [None])[0] or {}
        instance = app.models.Book(
            item=item,
            user=self.user,
            score=self._get_rating(user_book.get("rating")),
            progress=self._get_progress(status, book.get("pages"), latest_read),
            status=status,
            start_date=self._get_date(latest_read.get("started_at")),
            end_date=self._get_date(latest_read.get("finished_at")),
            notes=user_book.get("private_notes") or user_book.get("review_raw") or "",
        )
        instance._history_date = self._history_date(user_book, latest_read)
        self.bulk_media[MediaTypes.BOOK.value].append(instance)

    @staticmethod
    def _get_status(status_id):
        statuses = {
            1: Status.PLANNING.value,
            2: Status.IN_PROGRESS.value,
            3: Status.COMPLETED.value,
            4: Status.PAUSED.value,
            5: Status.DROPPED.value,
        }
        return statuses.get(status_id)

    @staticmethod
    def _get_rating(rating):
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            return None
        return rating * 2 if MIN_RATING <= rating <= MAX_RATING else None

    @staticmethod
    def _get_date(value):
        date = parse_datetime(value) if value else None
        if date and timezone.is_naive(date):
            return timezone.make_aware(date)
        return date

    def _history_date(self, user_book, latest_read):
        dates = [
            self._get_date(user_book.get("date_added")),
            self._get_date(latest_read.get("finished_at")),
            self._get_date(user_book.get("updated_at")),
        ]
        return max((date for date in dates if date is not None), default=timezone.now())

    @staticmethod
    def _get_progress(status, pages, latest_read):
        if status == Status.COMPLETED.value:
            return pages or 0
        return latest_read.get("progress_pages") or 0

    @staticmethod
    def _get_image(book):
        """Return a cover URL from either Hardcover cached-image response shape."""
        image = book.get("cached_image")
        if isinstance(image, dict):
            return image.get("url") or ""
        return image or ""
