import logging
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from app.models import DiaryEntry, Item, MediaLike, MediaTypes, Movie, Sources, Status
from app.providers import tmdb
from app.services import create_diary_entry, set_media_like, update_diary_entry_tags
from integrations.imports.letterboxd.parser import parse_export
from integrations.imports.letterboxd.resolver import LetterboxdResolver
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from a Letterboxd ZIP export."""
    return LetterboxdImporter(file, user, mode).import_data()


class LetterboxdImporter:
    """Import Letterboxd export data into Spine movies."""

    def __init__(self, file, user, mode, resolver=None):
        self.file = file
        self.user = user
        self.mode = mode
        self.resolver = resolver or LetterboxdResolver()
        self.warnings = []
        self.counts = defaultdict(int)
        self.items_by_tmdb_id = {}
        self.resolved_by_film = {}

    def import_data(self):
        """Import the export."""
        export = parse_export(self.file)
        rows = list(export.rows_with_uris())
        resolved = self.resolver.resolve_rows(rows)
        self.resolved_by_film = self._film_key_index(rows, resolved)

        with transaction.atomic():
            if self.mode == "overwrite":
                self._cleanup()
            self._import_diary(export.diary, resolved)
            self._import_reviews(export.reviews, resolved)
            self._import_watched(export.watched, resolved)
            self._import_ratings(export.ratings, resolved)
            self._import_watchlist(export.watchlist, resolved)
            self._import_lists(export.lists, resolved)
            self._import_likes(export.likes, resolved)

        warnings = "\n".join(dict.fromkeys(self.warnings))
        return dict(self.counts), warnings if warnings else None

    def _cleanup(self):
        Movie.objects.filter(user=self.user).delete()
        DiaryEntry.objects.filter(
            user=self.user,
            item__media_type=MediaTypes.MOVIE.value,
        ).delete()
        MediaLike.objects.filter(
            user=self.user,
            item__media_type=MediaTypes.MOVIE.value,
        ).delete()
        CustomList.objects.filter(
            owner=self.user,
            import_source="letterboxd",
        ).delete()

    def _import_diary(self, rows, resolved):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            self._ensure_movie(item, Status.COMPLETED.value, row.get("date"), row.get("rating"))
            if self.mode == "new" and self._diary_for_date(item, row.get("date")):
                continue
            create_diary_entry(
                self.user,
                item,
                consumed_at=row.get("date"),
                rating=row.get("rating"),
                is_rewatch=row.get("rewatch", False),
                tags=row.get("tags", []),
            )
            self.counts["diary"] += 1

    def _import_reviews(self, rows, resolved):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            self._ensure_movie(item, Status.COMPLETED.value, row.get("date"), row.get("rating"))
            entry = self._diary_for_date(item, row.get("date"))
            if entry:
                fields = []
                for field in ("review", "rating", "is_rewatch"):
                    value = row.get("rewatch") if field == "is_rewatch" else row.get(field)
                    if value not in (None, ""):
                        setattr(entry, field, value)
                        fields.append(field)
                if fields:
                    entry.save(update_fields=fields)
                if row.get("tags"):
                    update_diary_entry_tags(entry, row["tags"])
            else:
                create_diary_entry(
                    self.user,
                    item,
                    consumed_at=row.get("date"),
                    rating=row.get("rating"),
                    review=row.get("review", ""),
                    is_rewatch=row.get("rewatch", False),
                    tags=row.get("tags", []),
                )
            self.counts["reviews"] += 1

    def _import_watched(self, rows, resolved):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            _, changed = self._ensure_movie(item, Status.COMPLETED.value, row.get("date"))
            if changed:
                self.counts[MediaTypes.MOVIE.value] += 1

    def _import_ratings(self, rows, resolved):
        for row in rows:
            if row.get("rating") is None:
                continue
            item = self._item_for(row, resolved)
            if not item:
                continue
            movie, _ = self._ensure_movie(item, Status.COMPLETED.value, row.get("date"))
            entry = self._diary_for_rating(item, row.get("date"))
            if entry:
                if entry.rating is None:
                    entry.rating = row["rating"]
                    entry.save(update_fields=["rating"])
            elif movie.score is None:
                movie.score = row["rating"]
                movie.save(update_fields=["score"])
            self.counts["ratings"] += 1

    def _import_watchlist(self, rows, resolved):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            movie = Movie.objects.filter(user=self.user, item=item).first()
            if movie and movie.status == Status.COMPLETED.value:
                continue
            _, changed = self._ensure_movie(item, Status.PLANNING.value, row.get("date"))
            if changed:
                self.counts["watchlist"] += 1

    def _import_lists(self, lists, resolved):
        for letterboxd_list in lists:
            custom_list, created = CustomList.objects.get_or_create(
                owner=self.user,
                name=letterboxd_list.name,
                import_source="letterboxd",
                defaults={
                    "slug": f"letterboxd-{slugify(letterboxd_list.name)}"[:255],
                    "description": letterboxd_list.description,
                    "visibility": CustomList.Visibility.PRIVATE,
                },
            )
            if not created and letterboxd_list.description:
                custom_list.description = letterboxd_list.description
                custom_list.save(update_fields=["description"])
            for row in letterboxd_list.rows:
                item = self._item_for(row, resolved)
                if not item:
                    continue
                _, item_created = CustomListItem.objects.get_or_create(
                    custom_list=custom_list,
                    item=item,
                    defaults={"position": row.get("position")},
                )
                if item_created:
                    self.counts["list_items"] += 1
            self.counts["lists"] += int(created)

    def _import_likes(self, rows, resolved):
        for row in rows:
            item = self._item_for(row, resolved)
            if not item:
                continue
            created = not MediaLike.objects.filter(user=self.user, item=item).exists()
            set_media_like(self.user, item, True, audit=False)
            if created:
                self.counts["likes"] += 1

    def _film_key(self, row):
        name = (row.get("name") or "").strip().casefold()
        if not name:
            return None
        return (name, row.get("year"))

    def _film_key_index(self, rows, resolved):
        index = {}
        for row in rows:
            result = resolved.get(row.get("uri"))
            if not result:
                continue
            key = self._film_key(row)
            if key:
                index[key] = result
        return index

    def _item_for(self, row, resolved):
        result = resolved.get(row.get("uri"))
        if not result:
            result = self.resolved_by_film.get(self._film_key(row))
        if not result:
            self._warn_unresolved(row)
            return None
        tmdb_id = str(result["tmdb_id"])
        if tmdb_id not in self.items_by_tmdb_id:
            self.items_by_tmdb_id[tmdb_id] = self._get_or_create_item(tmdb_id, row)
        return self.items_by_tmdb_id[tmdb_id]

    def _get_or_create_item(self, tmdb_id, row):
        try:
            metadata = tmdb.movie(tmdb_id)
        except Exception as error:
            logger.warning("Could not fetch TMDB movie %s: %s", tmdb_id, error)
            metadata = {}
        return Item.objects.update_or_create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": metadata.get("title") or row.get("name") or tmdb_id,
                "image": metadata.get("image") or settings.IMG_NONE,
            },
        )[0]

    def _ensure_movie(self, item, status, when=None, rating=None):
        movie, created = Movie.objects.get_or_create(
            user=self.user,
            item=item,
            defaults={
                "status": status,
                "progress": 1 if status == Status.COMPLETED.value else 0,
                "end_date": when if status == Status.COMPLETED.value else None,
                "score": rating,
            },
        )
        if created:
            return movie, True
        fields = []
        if status == Status.COMPLETED.value and movie.status != Status.COMPLETED.value:
            movie.status = status
            movie.progress = 1
            fields.extend(["status", "progress"])
        if status == Status.COMPLETED.value and when and not movie.end_date:
            movie.end_date = when
            fields.append("end_date")
        if rating is not None and movie.score is None:
            movie.score = rating
            fields.append("score")
        if fields:
            movie.save(update_fields=fields)
        return movie, bool(fields)

    def _diary_for_date(self, item, consumed_at):
        if consumed_at is None:
            return None
        return DiaryEntry.objects.filter(
            user=self.user,
            item=item,
            consumed_at__date=consumed_at.date(),
        ).first()

    def _diary_for_rating(self, item, rated_at):
        entry = self._diary_for_date(item, rated_at)
        if entry:
            return entry
        entries = list(DiaryEntry.objects.filter(user=self.user, item=item).order_by("-consumed_at")[:2])
        return entries[0] if len(entries) == 1 else None

    def _warn_unresolved(self, row):
        label = row.get("name") or row.get("uri") or "Unknown film"
        self.warnings.append(f"{label}: Couldn't resolve Letterboxd film to TMDB")
