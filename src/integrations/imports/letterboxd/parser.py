import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from django.utils import timezone

from integrations.imports.helpers import MediaImportError


STANDARD_FILES = {
    "diary.csv",
    "watched.csv",
    "watchlist.csv",
    "ratings.csv",
    "reviews.csv",
    "likes/films.csv",
}
SKIP_PREFIXES = ("deleted/", "orphaned/", "__MACOSX/")
SKIP_FILES = {"profile.csv", "comments.csv", "likes/lists.csv", "likes/reviews.csv"}
LIST_HEADER = ["Position", "Name", "Year", "URL", "Description"]


@dataclass
class LetterboxdList:
    name: str
    description: str
    rows: list[dict] = field(default_factory=list)


@dataclass
class LetterboxdExport:
    diary: list[dict] = field(default_factory=list)
    watched: list[dict] = field(default_factory=list)
    watchlist: list[dict] = field(default_factory=list)
    ratings: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    likes: list[dict] = field(default_factory=list)
    lists: list[LetterboxdList] = field(default_factory=list)

    def rows_with_uris(self):
        """Yield all rows that can resolve to a film."""
        yield from self.diary
        yield from self.watched
        yield from self.watchlist
        yield from self.ratings
        yield from self.reviews
        yield from self.likes
        for custom_list in self.lists:
            yield from custom_list.rows


def parse_export(file_or_bytes):
    """Parse a Letterboxd export zip."""
    payload = file_or_bytes.read() if hasattr(file_or_bytes, "read") else file_or_bytes
    export = LetterboxdExport()

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        msg = "Invalid file format. Please upload a Letterboxd ZIP export."
        raise MediaImportError(msg) from error

    for member in archive.infolist():
        path = _clean_path(member.filename)
        if not path or member.is_dir() or _skip_path(path):
            continue
        if path in STANDARD_FILES:
            rows = [_normalize_row(row) for row in _read_dicts(archive, member)]
            setattr(export, _attr_for_path(path), rows)
        elif path.startswith("lists/") and path.endswith(".csv"):
            export.lists.append(_read_list(archive, member, path))

    return export


def _clean_path(path):
    return str(PurePosixPath(path.replace("\\", "/")))


def _skip_path(path):
    return path in SKIP_FILES or path.startswith(SKIP_PREFIXES)


def _attr_for_path(path):
    return "likes" if path == "likes/films.csv" else path.removesuffix(".csv")


def _text(archive, member):
    return archive.read(member).decode("utf-8-sig")


def _read_dicts(archive, member):
    return list(csv.DictReader(io.StringIO(_text(archive, member))))


def _read_list(archive, member, path):
    rows = list(csv.reader(io.StringIO(_text(archive, member))))
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if [cell.strip() for cell in row[: len(LIST_HEADER)]] == LIST_HEADER
        ),
        None,
    )
    if header_index is None:
        raise MediaImportError(f"{path}: Unsupported Letterboxd list CSV format.")

    metadata = {
        row[0].strip().lower(): row[1].strip()
        for row in rows[:header_index]
        if len(row) > 1 and row[0].strip()
    }
    name = metadata.get("name") or PurePosixPath(path).stem
    description = metadata.get("description", "")
    headers = rows[header_index]
    items = [_normalize_row(dict(zip(headers, row, strict=False))) for row in rows[header_index + 1 :] if row]
    return LetterboxdList(name=name, description=description, rows=items)


def _normalize_row(row):
    row = {key.strip(): (value or "").strip() for key, value in row.items() if key}
    row["uri"] = row.get("Letterboxd URI") or row.get("URL") or ""
    row["name"] = row.get("Name") or ""
    row["year"] = _int_or_none(row.get("Year"))
    row["rating"] = _rating(row.get("Rating"))
    row["date"] = _date(row.get("Watched Date") or row.get("Date"))
    row["rewatch"] = row.get("Rewatch") == "Yes"
    row["tags"] = [tag.strip() for tag in row.get("Tags", "").split(",") if tag.strip()]
    row["review"] = row.get("Review", "")
    row["position"] = _int_or_none(row.get("Position"))
    return row


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _rating(value):
    if not value:
        return None
    try:
        rating = Decimal(value) * 2
    except (InvalidOperation, TypeError):
        return None
    return rating if Decimal("0") <= rating <= Decimal("10") else None


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.get_current_timezone())
