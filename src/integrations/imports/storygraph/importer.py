import logging
from collections import defaultdict

from django.conf import settings
from django.db import transaction

from app.models import Book, DiaryEntry, Item, MediaTypes, Sources, Status
from app.services import create_diary_entry
from integrations.imports.storygraph.parser import parse_export
from integrations.imports.storygraph.resolver import StoryGraphResolver

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import books from a StoryGraph CSV export."""
    return StoryGraphImporter(file, user, mode).import_data()


class StoryGraphImporter:
    """Import StoryGraph library rows into Spine books and diary entries."""

    def __init__(self, file, user, mode, resolver=None):
        self.file = file
        self.user = user
        self.mode = mode
        self.resolver = resolver or StoryGraphResolver()
        self.warnings = []
        self.counts = defaultdict(int)

    def import_data(self):
        rows = parse_export(self.file)
        resolved = self.resolver.resolve_rows(rows)

        with transaction.atomic():
            if self.mode == "overwrite":
                self._cleanup()
            for row in rows:
                self._import_row(row, resolved.get(row.index))

        warnings = "\n".join(dict.fromkeys(self.warnings))
        return dict(self.counts), warnings or None

    def _cleanup(self):
        Book.objects.filter(user=self.user).delete()
        DiaryEntry.objects.filter(user=self.user, item__media_type=MediaTypes.BOOK.value).delete()

    def _import_row(self, row, result):
        if row.status is None:
            self.warnings.append(f"{row.title or 'StoryGraph row'}: Unknown StoryGraph status {row.read_status!r}")
            return
        if not result:
            self.warnings.append(f"{row.title or 'StoryGraph row'}: Couldn't resolve StoryGraph book")
            return

        item = self._get_or_create_item(row, result)
        book = Book.objects.filter(user=self.user, item=item).first()
        created = False
        if book is None:
            book = self._create_book(item, row)
            created = True
            self.counts[MediaTypes.BOOK.value] += 1

        latest_entry = self._import_diary_entries(item, row)
        if row.rating is not None and (created or latest_entry):
            self.counts["ratings"] += 1
        if row.review and (created or latest_entry):
            self.counts["reviews"] += 1
        if created and latest_entry:
            book.completion_diary_entry = latest_entry
            book.save(update_fields=["completion_diary_entry"])

    def _get_or_create_item(self, row, result):
        source = result.get("source") or Sources.HARDCOVER.value
        total_pages = result.get("max_progress") or result.get("total_pages")
        defaults = {
            "title": result.get("title") or row.title or str(result["media_id"]),
            "image": result.get("image") or settings.IMG_NONE,
        }
        if total_pages:
            defaults["total_pages"] = total_pages
        return Item.objects.update_or_create(
            media_id=str(result["media_id"]),
            source=source,
            media_type=MediaTypes.BOOK.value,
            defaults=defaults,
        )[0]

    def _create_book(self, item, row):
        read_dates = [read_date.end for read_date in row.read_dates]
        end_date = max(read_dates) if row.status == Status.COMPLETED.value and read_dates else None
        return Book.objects.create(
            user=self.user,
            item=item,
            status=row.status,
            progress=0,
            end_date=end_date,
            score=row.rating,
            notes=self._notes(row),
            completed_manually=False,
        )

    def _import_diary_entries(self, item, row):
        if not row.can_create_diary:
            return None

        latest_date = max(read_date.end for read_date in row.read_dates)
        latest_entry = None
        for index, read_date in enumerate(sorted(row.read_dates, key=lambda date: date.end)):
            if self._diary_for_date(item, read_date.end):
                continue
            is_latest = read_date.end == latest_date
            entry = create_diary_entry(
                self.user,
                item,
                consumed_at=read_date.end,
                rating=row.rating if is_latest else None,
                review=row.review if is_latest else "",
                is_rewatch=index > 0,
                tags=row.tags if is_latest else [],
            )
            self.counts["diary"] += 1
            if is_latest:
                latest_entry = entry
        return latest_entry

    def _diary_for_date(self, item, consumed_at):
        return DiaryEntry.objects.filter(
            user=self.user,
            item=item,
            consumed_at__date=consumed_at.date(),
        ).first()

    def _notes(self, row):
        lines = ["Imported from StoryGraph"]
        mapping = [
            ("Authors", row.authors),
            ("Contributors", row.contributors),
            ("ISBN/UID", row.isbn_uid),
            ("Format", row.format),
            ("Read status", row.read_status),
            ("Date added", _date_text(row.date_added)),
            ("Last date read", _date_text(row.last_date_read)),
            ("Dates read", row.dates_read_raw),
            ("Read count", str(row.read_count) if row.read_count else ""),
            ("Owned", row.raw.get("Owned?")),
            ("Moods", row.raw.get("Moods")),
            ("Pace", row.raw.get("Pace")),
            ("Character- or Plot-Driven", row.raw.get("Character- or Plot-Driven?")),
            ("Strong Character Development", row.raw.get("Strong Character Development?")),
            ("Loveable Characters", row.raw.get("Loveable Characters?")),
            ("Diverse Characters", row.raw.get("Diverse Characters?")),
            ("Flawed Characters", row.raw.get("Flawed Characters?")),
            ("Content Warnings", row.raw.get("Content Warnings")),
            ("Content Warning Description", row.raw.get("Content Warning Description")),
        ]
        for label, value in mapping:
            if value:
                lines.append(f"{label}: {value}")

        extra_reads = row.read_count - len(row.read_dates)
        if extra_reads > 0:
            lines.append(f"Undated reads: {extra_reads}")
        if row.review and not row.can_create_diary:
            lines.append(f"Review: {row.review}")
        if row.tags and not row.can_create_diary:
            lines.append(f"Tags: {', '.join(row.tags)}")
        return "\n".join(lines)


def _date_text(value):
    return value.strftime("%Y/%m/%d") if value else ""
