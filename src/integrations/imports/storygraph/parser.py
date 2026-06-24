import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from app.models import Status
from integrations.imports.helpers import MediaImportError


REQUIRED_COLUMNS = {
    "Title",
    "Authors",
    "Contributors",
    "ISBN/UID",
    "Format",
    "Read Status",
    "Date Added",
    "Last Date Read",
    "Dates Read",
    "Read Count",
    "Moods",
    "Pace",
    "Character- or Plot-Driven?",
    "Strong Character Development?",
    "Loveable Characters?",
    "Diverse Characters?",
    "Flawed Characters?",
    "Star Rating",
    "Review",
    "Content Warnings",
    "Content Warning Description",
    "Tags",
    "Owned?",
}

STATUS_MAP = {
    "read": Status.COMPLETED.value,
    "currently-reading": Status.IN_PROGRESS.value,
    "to-read": Status.PLANNING.value,
    "paused": Status.PAUSED.value,
    "did-not-finish": Status.DROPPED.value,
}

ISBN_RE = re.compile(r"[\s=\"'-]+")


@dataclass(frozen=True)
class StoryGraphReadDate:
    end: datetime
    raw: str


@dataclass
class StoryGraphRow:
    index: int
    raw: dict
    title: str
    authors: str
    contributors: str
    isbn_uid: str
    isbn: str
    format: str
    read_status: str
    status: str | None
    date_added: datetime | None
    last_date_read: datetime | None
    dates_read_raw: str
    read_dates: list[StoryGraphReadDate] = field(default_factory=list)
    read_count: int = 0
    rating: Decimal | None = None
    review: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def can_create_diary(self):
        return self.read_status == "read" and bool(self.read_dates)


def parse_export(file_or_bytes):
    """Parse a StoryGraph library CSV export."""
    payload = file_or_bytes.read() if hasattr(file_or_bytes, "read") else file_or_bytes
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        msg = "Invalid file format. Please upload a StoryGraph CSV export."
        raise MediaImportError(msg) from error

    reader = csv.DictReader(io.StringIO(text))
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        msg = f"Unsupported StoryGraph CSV format. Missing columns: {', '.join(sorted(missing))}."
        raise MediaImportError(msg)

    return [_normalize_row(index, row) for index, row in enumerate(reader, start=1)]


def _normalize_row(index, row):
    row = {key.strip(): (value or "").strip() for key, value in row.items() if key}
    dates_read_raw = row.get("Dates Read", "")
    read_dates = _read_dates(dates_read_raw)
    last_date_read = _date(row.get("Last Date Read"))
    if not read_dates and last_date_read:
        read_dates = [StoryGraphReadDate(end=last_date_read, raw=row["Last Date Read"])]

    read_status = row.get("Read Status", "").casefold()
    isbn_uid = row.get("ISBN/UID", "")
    return StoryGraphRow(
        index=index,
        raw=row,
        title=row.get("Title", ""),
        authors=row.get("Authors", ""),
        contributors=row.get("Contributors", ""),
        isbn_uid=isbn_uid,
        isbn=_isbn(isbn_uid),
        format=row.get("Format", ""),
        read_status=read_status,
        status=STATUS_MAP.get(read_status),
        date_added=_date(row.get("Date Added")),
        last_date_read=last_date_read,
        dates_read_raw=dates_read_raw,
        read_dates=read_dates,
        read_count=_int(row.get("Read Count")),
        rating=_rating(row.get("Star Rating")),
        review=row.get("Review", ""),
        tags=[tag.strip() for tag in row.get("Tags", "").split(",") if tag.strip()],
    )


def _int(value):
    try:
        return int(value) if value else 0
    except ValueError:
        return 0


def _rating(value):
    if not value:
        return None
    try:
        rating = Decimal(value) * 2
    except (InvalidOperation, TypeError):
        return None
    return rating if Decimal("0") <= rating <= Decimal("10") else None


def _read_dates(value):
    dates = []
    for part in re.split(r"[,;]", value or ""):
        raw = part.strip()
        if not raw:
            continue
        end_text = raw.split("-")[-1].strip()
        parsed = _date(end_text)
        if parsed:
            dates.append(StoryGraphReadDate(end=parsed, raw=raw))
    return dates


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y/%m/%d")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.get_current_timezone())


def _isbn(value):
    cleaned = ISBN_RE.sub("", value or "").upper()
    if _valid_isbn13(cleaned) or _valid_isbn10(cleaned):
        return cleaned
    return ""


def _valid_isbn13(value):
    if len(value) != 13 or not value.isdigit():
        return False
    total = sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(value[:12]))
    return (10 - total % 10) % 10 == int(value[-1])


def _valid_isbn10(value):
    if len(value) != 10 or not value[:9].isdigit() or not (value[-1].isdigit() or value[-1] == "X"):
        return False
    total = sum((10 - index) * (10 if digit == "X" else int(digit)) for index, digit in enumerate(value))
    return total % 11 == 0
