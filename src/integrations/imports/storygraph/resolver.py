import logging

from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)


class StoryGraphResolver:
    """Resolve StoryGraph rows to Spine book search results."""

    SOURCES = (Sources.HARDCOVER.value, Sources.OPENLIBRARY.value)

    def resolve_rows(self, rows):
        resolved = {}
        for row in rows:
            result = self.resolve_one(row)
            if result:
                resolved[row.index] = result
        return resolved

    def resolve_one(self, row):
        for source in self.SOURCES:
            for query in self._queries(row):
                result = self._search(query, source)
                if result:
                    result.setdefault("source", source)
                    return result
        return None

    def _queries(self, row):
        if row.isbn:
            yield row.isbn
        title_author = " ".join(part for part in [row.title, _first_author(row.authors)] if part)
        if title_author:
            yield title_author
        elif row.title:
            yield row.title

    def _search(self, query, source):
        try:
            results = services.search(MediaTypes.BOOK.value, query, 1, source).get("results", [])
        except services.ProviderAPIError as error:
            logger.warning("StoryGraph %s lookup failed for %s: %s", source, query, error)
            return None
        return results[0] if results else None


def _first_author(authors):
    return (authors or "").split(",")[0].strip()
