from django.test import SimpleTestCase

from app.models import Book, BookProgressUnits, Item, MediaTypes, Sources


class BookProgressUnitDisplayTests(SimpleTestCase):
    """Tests for Book progress unit display wording."""

    def _book(self, progress, progress_unit, max_progress=None):
        item = Item(
            media_id="book-1",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Book One",
            image="http://example.com/book.jpg",
        )
        book = Book(
            item=item,
            progress=progress,
            progress_unit=progress_unit,
        )
        book.max_progress = max_progress
        return book

    def test_chapter_progress_uses_singular_and_plural_labels(self):
        """Chapter displays do not double pluralize."""
        self.assertEqual(
            self._book(1, BookProgressUnits.CHAPTERS.value).progress_display,
            "1 Chapter",
        )
        self.assertEqual(
            self._book(7, BookProgressUnits.CHAPTERS.value).progress_display,
            "7 Chapters",
        )

    def test_page_progress_uses_singular_and_plural_labels(self):
        """Page displays do not double pluralize."""
        self.assertEqual(
            self._book(1, BookProgressUnits.PAGES.value).progress_display,
            "1 Page",
        )
        self.assertEqual(
            self._book(124, BookProgressUnits.PAGES.value).progress_display,
            "124 Pages",
        )
        self.assertEqual(
            self._book(124, BookProgressUnits.PAGES.value, 350).progress_display,
            "124 / 350 Pages",
        )

    def test_hour_progress_uses_singular_and_plural_labels(self):
        """Hour displays do not double pluralize."""
        self.assertEqual(
            self._book(1, BookProgressUnits.HOURS.value).progress_display,
            "1 Hour",
        )
        self.assertEqual(
            self._book(6, BookProgressUnits.HOURS.value).progress_display,
            "6 Hours",
        )

    def test_percent_progress_uses_percent_symbol_only(self):
        """Percent displays as a percent sign without pluralization."""
        self.assertEqual(
            self._book(42, BookProgressUnits.PERCENT.value).progress_display,
            "42%",
        )
