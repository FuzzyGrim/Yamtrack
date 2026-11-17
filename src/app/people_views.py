from urllib.parse import quote

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from app import (
    credits,  # noqa: A004  # app.credits module, not the site builtin
    helpers,
    statistics_cache,
)
from app.media_list_filters import media_list_entries_for_items
from app.media_list_views import build_filter_data_from_items
from app.models import (
    TV,
    BasicMedia,
    Book,
    Comic,
    CreditRoleType,
    Episode,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Sources,
    Status,
    Studio,
)
from app.providers import (
    comicvine,
    hardcover,
    igdb,
    mangabaka,
    mangaupdates,
    openlibrary,
    services,
    tmdb,
    tvdb,
)
from users.models import MediaSortChoices

logger = __import__("logging").getLogger(__name__)


def attach_card_media(user, entries, source):
    """Give each credit entry the item and tracking row the shared media card reads.

    A title the viewer has an Item for uses it, with their tracking row loaded the
    library's way, so the card shows their rating, status, progress and hover
    actions. Any other title is shown from the provider's data, like search results.
    """
    tracked_items = {
        entry["tracked_item"].pk: entry["tracked_item"]
        for entry in entries
        if entry.get("tracked_item")
    }
    media_by_item_id = {}
    if tracked_items and user.is_authenticated:
        rows_by_type = {}
        for loaded in media_list_entries_for_items(user, list(tracked_items.values())):
            if loaded.media is not None:
                media_by_item_id[loaded.item.pk] = loaded.media
                rows_by_type.setdefault(loaded.item.media_type, []).append(
                    loaded.media,
                )
        for media_type, rows in rows_by_type.items():
            BasicMedia.objects.annotate_max_progress(rows, media_type)
    for entry in entries:
        tracked_item = entry.get("tracked_item")
        entry["media"] = media_by_item_id.get(tracked_item.pk) if tracked_item else None
        if entry["media"] is not None:
            entry["card_item"] = entry["media"].item
        else:
            entry["card_item"] = tracked_item or {
                **entry,
                "source": entry.get("source") or source,
            }


def _people_credit_contract(entry, source, user):
    """Shape one filmography entry for the shared native credit card."""
    from django.urls import reverse
    from django.utils.text import slugify

    from app.templatetags.app_tags import image_url, media_type_readable, media_url

    media_type = entry.get("media_type")
    media_id = str(entry.get("media_id") or "")
    title = entry.get("title") or "Unknown Title"
    tracked_item = entry.get("tracked_item")
    href = media_url(tracked_item) if tracked_item else ""
    if not href:
        href = reverse(
            "media_details",
            kwargs={
                "source": entry.get("source") or source,
                "media_type": media_type,
                "media_id": media_id,
                "title": slugify(title) or media_id or "item",
            },
        )
    departments = entry.get("all_departments") or []
    role = (
        entry.get("role")
        or ", ".join(department for department in departments if department)
        or entry.get("department")
        or ""
    )
    from app.card_surfaces import card_context
    from app.icon_specs import file_icon

    card_ctx = card_context(
        {"user": user},
        "credit",
        {"item": entry.get("card_item"), "media": entry.get("media"),
         "title": title, "card_media_type": media_type, "credit": entry},
    )
    card = card_ctx["react_props"]["model"]
    card["hoverMetadata"] = card_ctx["card_display"] != "always"
    status_icons = {
        "Completed": "completed", "In progress": "in-progress", "Paused": "paused",
        "Planning": "planning", "Dropped": "dropped",
    }
    chip = card.get("statusChip") or {}
    if chip.get("kind") == "status" and chip.get("status") in status_icons:
        chip["icon"] = file_icon(f"states/{status_icons[chip['status']]}")
    return {
        "id": f"{media_type}:{media_id}",
        "title": title,
        "mediaType": media_type,
        "mediaTypeLabel": media_type_readable(media_type) if media_type else "",
        "source": entry.get("source") or source,
        "mediaId": media_id,
        "year": entry.get("year"),
        "role": role,
        "imageUrl": image_url(entry.get("image") or ""),
        "href": href,
        "watched": bool(entry.get("is_watched")),
        "watchedRuntimeLabel": entry.get("watched_person_runtime_display") or "",
        "voteAverage": entry.get("vote_average"),
        "card": card,
    }


def _people_filters_contract(context):
    """Return the filmography toolbar state and options (what the web toolbar shows)."""
    filter_data = context.get("filter_data") or {}
    return {
        "sort": context.get("current_sort"),
        "direction": context.get("current_direction"),
        "department": context.get("current_department") or "",
        "year": context.get("current_year") or "",
        "genre": context.get("current_genre") or "",
        "rating": context.get("current_rating") or "all",
        "collection": context.get("current_collection") or "all",
        "sortChoices": [
            {"value": str(value), "label": str(label)}
            for value, label in context.get("sort_choices") or []
        ],
        "departments": [str(value) for value in filter_data.get("departments") or []],
        "years": [
            {"value": str(year["value"]), "label": str(year["label"])}
            for year in filter_data.get("years") or []
        ],
        "genres": [str(value) for value in filter_data.get("genres") or []],
    }


def _people_person_contract(context):
    """Return the person page values the native and browser adapters consume."""
    from users.templatetags.ui_tags import _person_hero_props

    hero = _person_hero_props(context)
    person = context.get("person") or {}
    return {
        "kind": "person",
        "person": hero["person"],
        "counts": hero["counts"],
        "labels": hero["labels"],
        "historyUrl": hero["historyUrl"],
        "canViewHistory": hero["canViewHistory"],
        "showBiography": hero["showBiography"],
        "isAuthor": bool(context.get("is_author")),
        # The play totals are still building: ask again shortly.
        "talentPending": bool(context.get("talent_pending")),
        "source": context.get("source"),
        "personId": str(person.get("person_id") or ""),
        "sourceUrl": context.get("source_url") or "",
        "filters": _people_filters_contract(context),
        "watched": [
            _people_credit_contract(entry, context.get("source"), context["user"])
            for entry in context.get("watched_filmography") or []
        ],
        "filmography": [
            _people_credit_contract(entry, context.get("source"), context["user"])
            for entry in context.get("filmography") or []
        ],
    }


def _people_studio_contract(context):
    """Return the studio page values the native and browser adapters consume."""
    from users.templatetags.ui_tags import _studio_hero_props

    hero = _studio_hero_props(context)
    studio = context.get("studio")
    return {
        "kind": "studio",
        "studio": hero["studio"],
        "counts": hero["counts"],
        "labels": hero["labels"],
        "source": context.get("source"),
        "studioId": str(getattr(studio, "source_studio_id", "") or ""),
        "sourceUrl": context.get("studio_source_url") or "",
        "titles": [
            _people_credit_contract(entry, context.get("source"), context["user"])
            for entry in context.get("credited_titles") or []
        ],
    }


# The web page asks every 3 s while totals build; after about two minutes it
# stops and the numbers appear on the next visit.
PERSON_TALENT_POLL_LIMIT = 40


def _talent_poll_attempt(request, source_config):
    """Return the poll attempt for a talent poll request, else None."""
    if request.GET.get("talent") != "1" or not request.headers.get("HX-Request"):
        return None
    if source_config["is_author"] or not request.user.is_authenticated:
        return None
    try:
        return max(1, int(request.GET.get("attempt", "1")))
    except ValueError:
        return 1


@require_GET
def person_detail(request, source, person_id, name):
    """Render a provider-backed person or author profile page."""
    del name  # URL slug is cosmetic; person_id is canonical.
    source_dispatch = {
        Sources.TMDB.value: {
            "fetcher": tmdb.person,
            "entries_key": "filmography",
            "tracked_media_types": (
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
            ),
            "source_url": lambda person_id_value: (
                f"https://www.themoviedb.org/person/{person_id_value}"
            ),
            "is_author": False,
        },
        Sources.TVDB.value: {
            "fetcher": tvdb.person,
            "entries_key": "filmography",
            "tracked_media_types": (MediaTypes.TV.value,),
            "source_url": lambda person_id_value: (
                f"https://www.thetvdb.com/dereferrer/person/{person_id_value}"
            ),
            "is_author": False,
        },
        Sources.HARDCOVER.value: {
            "fetcher": hardcover.author_profile,
            "entries_key": "bibliography",
            "tracked_media_types": (MediaTypes.BOOK.value,),
            "source_url": lambda person_id_value: (
                f"https://hardcover.app/authors/{person_id_value}"
            ),
            "is_author": True,
        },
        Sources.OPENLIBRARY.value: {
            "fetcher": openlibrary.author_profile,
            "entries_key": "bibliography",
            "tracked_media_types": (MediaTypes.BOOK.value,),
            "source_url": lambda person_id_value: (
                f"https://openlibrary.org/authors/{person_id_value}"
            ),
            "is_author": True,
        },
        Sources.COMICVINE.value: {
            "fetcher": comicvine.person_profile,
            "entries_key": "bibliography",
            "tracked_media_types": (MediaTypes.COMIC.value,),
            "source_url": lambda person_id_value: (
                f"https://comicvine.gamespot.com/person/4040-{person_id_value}/"
            ),
            "is_author": True,
        },
        Sources.MANGAUPDATES.value: {
            "fetcher": mangaupdates.author_profile,
            "entries_key": "bibliography",
            "tracked_media_types": (MediaTypes.MANGA.value,),
            "source_url": lambda person_id_value: (
                f"https://www.mangaupdates.com/authors.html?id={person_id_value}"
            ),
            "is_author": True,
        },
        Sources.MANGABAKA.value: {
            "fetcher": mangabaka.author_profile,
            "entries_key": "bibliography",
            "tracked_media_types": (MediaTypes.MANGA.value,),
            # MangaBaka has no author pages (/author and /staff 404), so the
            # search for the credited name is the closest real destination.
            "source_url": lambda person_id_value: (
                "https://mangabaka.org/search?q=" + quote(person_id_value)
            ),
            "is_author": True,
        },
    }
    source_config = source_dispatch.get(source)
    if not source_config:
        return HttpResponseBadRequest("Person pages are not available for this source.")

    talent_poll = _talent_poll_attempt(request, source_config)
    if talent_poll is not None:
        _totals, pending = statistics_cache.get_ready_person_talent_totals(
            request.user, source, str(person_id)
        )
        if pending:
            # Cache reads only: no provider call, no library scan.
            if talent_poll >= PERSON_TALENT_POLL_LIMIT:
                return HttpResponse("")
            return render(
                request,
                "app/components/person_talent_poller.html",
                {"person_detail_url": request.path, "attempt": talent_poll + 1},
            )

    # A viewer is waiting: a rate-limited provider fails fast instead of
    # holding the request through long retry waits.
    with services.interactive_request_scope():
        person_metadata = source_config["fetcher"](person_id) or {}
    person = credits.upsert_person_profile(source, person_id, person_metadata)

    person_id_str = str(person_id)
    is_author = source_config["is_author"]
    person_data = {
        "source": source,
        "person_id": person_id_str,
        "name": person_metadata.get("name")
        or (person.name if person else "Unknown Person"),
        "image": person_metadata.get("image")
        or (person.image if person else settings.IMG_NONE),
        "biography": person_metadata.get("biography")
        or (person.biography if person else ""),
        "known_for_department": person_metadata.get("known_for_department")
        or (person.known_for_department if person else ("Author" if is_author else "")),
        "birth_date": person_metadata.get("birth_date")
        or (person.birth_date.isoformat() if person and person.birth_date else None),
        "death_date": person_metadata.get("death_date")
        or (person.death_date.isoformat() if person and person.death_date else None),
        "place_of_birth": person_metadata.get("place_of_birth")
        or (person.place_of_birth if person else ""),
    }

    media_types_for_source = source_config["tracked_media_types"]
    raw_entries = person_metadata.get(source_config["entries_key"], [])
    filmography = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            continue
        media_id_value = raw_entry.get("media_id")
        if media_id_value is None:
            continue
        media_type = raw_entry.get("media_type")
        if media_type is None and len(media_types_for_source) == 1:
            media_type = media_types_for_source[0]
        if media_type not in media_types_for_source:
            continue
        filmography.append(
            {
                **raw_entry,
                "media_id": str(media_id_value),
                "media_type": media_type,
                "source": raw_entry.get("source") or source,
                "title": raw_entry.get("title") or "Unknown Title",
                "image": raw_entry.get("image") or settings.IMG_NONE,
                "year": raw_entry.get("year"),
                "role": raw_entry.get("role") or "",
                "department": raw_entry.get("department") or "",
                "credit_type": raw_entry.get("credit_type")
                or ("author" if is_author else ""),
                "sort_order": raw_entry.get("sort_order", index),
            },
        )

    if is_author and not filmography:
        fallback_items = (
            Item.objects.filter(
                source=source,
                media_type__in=media_types_for_source,
                person_credits__role_type=CreditRoleType.AUTHOR.value,
                person_credits__person__source=source,
                person_credits__person__source_person_id=person_id_str,
            )
            .order_by("title")
            .distinct()
        )
        for index, item in enumerate(fallback_items):
            filmography.append(
                {
                    "media_id": str(item.media_id),
                    "source": source,
                    "media_type": item.media_type,
                    "title": item.title,
                    "image": item.image or settings.IMG_NONE,
                    "year": None,
                    "role": "Author",
                    "department": "",
                    "credit_type": "author",
                    "sort_order": index,
                },
            )

    seen_media: dict[tuple, dict] = {}
    for entry in filmography:
        media_key = (entry.get("media_type"), str(entry.get("media_id")))
        if media_key in seen_media:
            dept = entry.get("department", "")
            if dept and dept not in seen_media[media_key]["all_departments"]:
                seen_media[media_key]["all_departments"].append(dept)
        else:
            entry["all_departments"] = (
                [entry["department"]] if entry.get("department") else []
            )
            seen_media[media_key] = entry
    filmography = list(seen_media.values())

    tracked_item_map = {}
    if filmography:
        tracked_filters = Q()
        for media_type in media_types_for_source:
            media_ids_for_type = {
                entry["media_id"]
                for entry in filmography
                if entry.get("media_type") == media_type
            }
            if media_ids_for_type:
                tracked_filters |= Q(
                    media_type=media_type,
                    media_id__in=media_ids_for_type,
                )
        if tracked_filters:
            tracked_items = Item.objects.filter(source=source).filter(tracked_filters)
            tracked_item_map = {
                (item.media_type, str(item.media_id)): item for item in tracked_items
            }

    credited_tracked_items_by_key = {}
    if request.user.is_authenticated and is_author:
        for model, media_type in (
            (Book, MediaTypes.BOOK.value),
            (Comic, MediaTypes.COMIC.value),
            (Manga, MediaTypes.MANGA.value),
        ):
            tracked_reads = (
                model.objects.filter(
                    user=request.user,
                    item__media_type=media_type,
                    item__person_credits__role_type=CreditRoleType.AUTHOR.value,
                    item__person_credits__person__source=source,
                    item__person_credits__person__source_person_id=person_id_str,
                )
                .filter(
                    Q(start_date__isnull=False)
                    | Q(end_date__isnull=False)
                    | Q(status=Status.COMPLETED.value)
                )
                .select_related("item")
                .distinct()
            )
            for tracked_read in tracked_reads:
                item = tracked_read.item
                media_key = (item.media_type, str(item.media_id))
                if media_key in credited_tracked_items_by_key:
                    continue
                credited_tracked_items_by_key[media_key] = item

    if credited_tracked_items_by_key:
        tracked_item_map.update(credited_tracked_items_by_key)

    watched_media_keys = set()
    watched_person_minutes_by_media_key = {}
    person_talent_totals = None
    talent_pending = False
    if request.user.is_authenticated and not is_author:
        # Stored totals only. Building them scans every play in the library,
        # which is background work (build_person_talent_context_task).
        person_talent_totals, talent_pending = (
            statistics_cache.get_ready_person_talent_totals(
                request.user,
                source,
                person_id_str,
            )
        )
        watched_person_minutes_by_media_key = (
            person_talent_totals.get("minutes_by_media_key", {})
            if person_talent_totals
            else {}
        )

    if credited_tracked_items_by_key:
        watched_media_keys.update(credited_tracked_items_by_key.keys())

    if request.user.is_authenticated and filmography:
        watched_movie_media_ids = {
            entry["media_id"]
            for entry in filmography
            if entry.get("media_type") == MediaTypes.MOVIE.value
        }
        watched_tv_media_ids = {
            entry["media_id"]
            for entry in filmography
            if entry.get("media_type") == MediaTypes.TV.value
        }
        watched_book_media_ids = {
            entry["media_id"]
            for entry in filmography
            if entry.get("media_type") == MediaTypes.BOOK.value
        }
        watched_comic_media_ids = {
            entry["media_id"]
            for entry in filmography
            if entry.get("media_type") == MediaTypes.COMIC.value
        }
        watched_manga_media_ids = {
            entry["media_id"]
            for entry in filmography
            if entry.get("media_type") == MediaTypes.MANGA.value
        }

        if watched_movie_media_ids:
            watched_movies = Movie.objects.filter(
                user=request.user,
                item__source=source,
                item__media_type=MediaTypes.MOVIE.value,
                item__media_id__in=watched_movie_media_ids,
            ).filter(Q(end_date__isnull=False) | Q(status=Status.COMPLETED.value))
            watched_media_keys.update(
                (media_type, str(media_id))
                for media_type, media_id in watched_movies.values_list(
                    "item__media_type",
                    "item__media_id",
                ).distinct()
            )

        if watched_tv_media_ids:
            watched_tv = Episode.all_objects.filter(
                related_season__user=request.user,
                related_season__related_tv__item__source=source,
                related_season__related_tv__item__media_type=MediaTypes.TV.value,
                related_season__related_tv__item__media_id__in=watched_tv_media_ids,
            ).filter(Q(end_date__isnull=False) | Q(status=Status.COMPLETED.value))
            watched_media_keys.update(
                (media_type, str(media_id))
                for media_type, media_id in watched_tv.values_list(
                    "related_season__related_tv__item__media_type",
                    "related_season__related_tv__item__media_id",
                ).distinct()
            )
            completed_tv = TV.objects.filter(
                user=request.user,
                item__source=source,
                item__media_type=MediaTypes.TV.value,
                item__media_id__in=watched_tv_media_ids,
                status=Status.COMPLETED.value,
            )
            watched_media_keys.update(
                (media_type, str(media_id))
                for media_type, media_id in completed_tv.values_list(
                    "item__media_type",
                    "item__media_id",
                ).distinct()
            )

        if watched_book_media_ids:
            watched_books = Book.objects.filter(
                user=request.user,
                item__source=source,
                item__media_type=MediaTypes.BOOK.value,
                item__media_id__in=watched_book_media_ids,
            ).filter(
                Q(start_date__isnull=False)
                | Q(end_date__isnull=False)
                | Q(status=Status.COMPLETED.value)
            )
            watched_media_keys.update(
                (media_type, str(media_id))
                for media_type, media_id in watched_books.values_list(
                    "item__media_type",
                    "item__media_id",
                ).distinct()
            )

        if watched_comic_media_ids:
            watched_comics = Comic.objects.filter(
                user=request.user,
                item__source=source,
                item__media_type=MediaTypes.COMIC.value,
                item__media_id__in=watched_comic_media_ids,
            ).filter(
                Q(start_date__isnull=False)
                | Q(end_date__isnull=False)
                | Q(status=Status.COMPLETED.value)
            )
            watched_media_keys.update(
                (media_type, str(media_id))
                for media_type, media_id in watched_comics.values_list(
                    "item__media_type",
                    "item__media_id",
                ).distinct()
            )

        if watched_manga_media_ids:
            watched_manga = Manga.objects.filter(
                user=request.user,
                item__source=source,
                item__media_type=MediaTypes.MANGA.value,
                item__media_id__in=watched_manga_media_ids,
            ).filter(
                Q(start_date__isnull=False)
                | Q(end_date__isnull=False)
                | Q(status=Status.COMPLETED.value)
            )
            watched_media_keys.update(
                (media_type, str(media_id))
                for media_type, media_id in watched_manga.values_list(
                    "item__media_type",
                    "item__media_id",
                ).distinct()
            )

    for entry in filmography:
        media_key = (entry.get("media_type"), str(entry.get("media_id")))
        entry["tracked_item"] = tracked_item_map.get(media_key)
        entry["is_watched"] = media_key in watched_media_keys

    watched_filmography = []
    if watched_media_keys:
        seen_watched_media = set()
        for entry in filmography:
            media_key = (entry.get("media_type"), str(entry.get("media_id")))
            if media_key in watched_media_keys and media_key not in seen_watched_media:
                watched_entry = dict(entry)
                watched_minutes = watched_person_minutes_by_media_key.get(media_key, 0)
                if watched_minutes > 0:
                    watched_entry["watched_person_runtime_display"] = (
                        helpers.minutes_to_hhmm(watched_minutes)
                    )
                watched_filmography.append(watched_entry)
                seen_watched_media.add(media_key)

        if is_author and credited_tracked_items_by_key:
            for media_key, tracked_item in credited_tracked_items_by_key.items():
                if media_key in seen_watched_media:
                    continue
                watched_filmography.append(
                    {
                        "media_id": str(tracked_item.media_id),
                        "source": tracked_item.source,
                        "media_type": tracked_item.media_type,
                        "title": tracked_item.title,
                        "image": tracked_item.image or settings.IMG_NONE,
                        "year": (
                            tracked_item.release_datetime.year
                            if tracked_item.release_datetime
                            else None
                        ),
                        "role": "Author",
                        "department": "",
                        "credit_type": "author",
                        "sort_order": len(watched_filmography),
                        "tracked_item": tracked_item,
                        "is_watched": True,
                    },
                )
                seen_watched_media.add(media_key)

    watched_movie_count = sum(
        1
        for media_type, _ in watched_media_keys
        if media_type == MediaTypes.MOVIE.value
    )
    watched_show_count = sum(
        1 for media_type, _ in watched_media_keys if media_type == MediaTypes.TV.value
    )
    watched_book_count = sum(
        1
        for media_type, _ in watched_media_keys
        if media_type
        in (
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MANGA.value,
        )
    )

    # Collect filter options from the full unfiltered filmography.
    all_departments = sorted(
        {
            dept
            for e in filmography
            for dept in e.get(
                "all_departments", ([e["department"]] if e.get("department") else [])
            )
        }
    )
    all_filmography_years = sorted(
        {e["year"] for e in filmography if e.get("year")}, reverse=True
    )

    filter_department = request.GET.get("department", "")
    filter_year = request.GET.get("year", "")
    filter_genre = request.GET.get("genre", "")
    filter_implied_genre = request.GET.get("implied_genre", "")
    filter_source = request.GET.get("source", "")
    filter_rating = request.GET.get("rating", "all")
    filter_collection = request.GET.get("collection", "all")
    # Use the same sort key names as MediaSortChoices for consistency with the
    # rest of the app. "release_date" desc is the default provider ordering.
    person_valid_sorts = {
        MediaSortChoices.RELEASE_DATE.value,
        MediaSortChoices.TITLE.value,
        MediaSortChoices.CRITIC_RATING.value,
        MediaSortChoices.POPULARITY.value,
        "votes",
    }
    sort_by = request.GET.get("sort", MediaSortChoices.RELEASE_DATE.value)
    if sort_by not in person_valid_sorts:
        sort_by = MediaSortChoices.RELEASE_DATE.value
    sort_dir = request.GET.get("direction", "")
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "asc" if sort_by == MediaSortChoices.TITLE.value else "desc"

    def _apply_common_filters(entries):
        """Build the filters that apply to every filmography entry."""
        result = entries
        if filter_department:
            result = [
                e
                for e in result
                if filter_department in e.get("all_departments", [e.get("department")])
            ]
        if filter_year:
            result = [e for e in result if str(e.get("year") or "") == filter_year]
        return result

    def _apply_watched_filters(entries):
        """Build the filters that need tracked_item data, for watched entries only."""
        result = entries
        if filter_genre:
            result = [
                e
                for e in result
                if filter_genre
                in (getattr(e.get("tracked_item"), "genres", None) or [])
            ]
        if filter_implied_genre:
            result = [
                e
                for e in result
                if filter_implied_genre
                in (getattr(e.get("tracked_item"), "implied_genres", None) or [])
            ]
        if filter_source:
            result = [
                e
                for e in result
                if getattr(e.get("tracked_item"), "source", None) == filter_source
            ]
        if filter_rating == "rated":
            result = [
                e
                for e in result
                if getattr(e.get("tracked_item"), "score", None) is not None
            ]
        elif filter_rating == "not_rated":
            result = [
                e
                for e in result
                if getattr(e.get("tracked_item"), "score", None) is None
            ]
        if filter_collection == "collected":
            result = [
                e
                for e in result
                if getattr(e.get("tracked_item"), "in_collection", False)
            ]
        elif filter_collection == "not_collected":
            result = [
                e
                for e in result
                if not getattr(e.get("tracked_item"), "in_collection", False)
            ]
        return result

    def _sort_with_nulls_last(entries, key_fn, reverse):
        """Sort entries by key_fn, always placing None-valued entries at the end."""
        has_value = [e for e in entries if key_fn(e) is not None]
        no_value = [e for e in entries if key_fn(e) is None]
        return sorted(has_value, key=key_fn, reverse=reverse) + no_value

    def _apply_sort(entries):
        rev = sort_dir == "desc"
        if sort_by == MediaSortChoices.TITLE.value:
            return sorted(
                entries, key=lambda e: e.get("title", "").lower(), reverse=rev
            )
        if sort_by == MediaSortChoices.CRITIC_RATING.value:
            return _sort_with_nulls_last(entries, lambda e: e.get("vote_average"), rev)
        if sort_by == MediaSortChoices.POPULARITY.value:
            return _sort_with_nulls_last(entries, lambda e: e.get("popularity"), rev)
        if sort_by == "votes":

            def _vote_count_key(e):
                vc = e.get("vote_count")
                if vc is None:
                    item = e.get("tracked_item")
                    if item is not None:
                        vc = item.provider_rating_count
                return vc

            return _sort_with_nulls_last(entries, _vote_count_key, rev)
        # release_date: sort on the entries' own dates. TMDB happens to return
        # newest-first, but the author providers (MangaBaka, Hardcover,
        # OpenLibrary) return search-relevance order and carry only `year`.
        def _date_key(e):
            for field in ("release_date", "first_air_date", "year"):
                if e.get(field):
                    return str(e[field])
            return None

        return _sort_with_nulls_last(entries, _date_key, rev)

    # Collect tracked items from the unfiltered watched list for filter option building.
    watched_items_for_filter_data = [
        e["tracked_item"] for e in watched_filmography if e.get("tracked_item")
    ]

    filmography = _apply_sort(_apply_common_filters(filmography))
    watched_filmography = _apply_sort(
        _apply_common_filters(_apply_watched_filters(watched_filmography))
    )

    from django.urls import reverse

    history_filter_url = (
        f"{reverse('history')}?person_source={source}&person_id={person_id}"
    )
    source_url = source_config["source_url"](person_id_str)

    tracked_plays_count = None
    tracked_hours_count = None
    if request.user.is_authenticated:
        if is_author:
            tracked_plays_count = len(credited_tracked_items_by_key)
        else:
            tracked_plays_count = 0
            if person_talent_totals:
                tracked_plays_count = person_talent_totals.get("plays", 0)
                tracked_hours_count = person_talent_totals.get("watched_time")

    person_sort_choices = (
        [
            (MediaSortChoices.RELEASE_DATE.value, MediaSortChoices.RELEASE_DATE.label),
            (MediaSortChoices.TITLE.value, MediaSortChoices.TITLE.label),
        ]
        if is_author
        else [
            (MediaSortChoices.RELEASE_DATE.value, MediaSortChoices.RELEASE_DATE.label),
            (MediaSortChoices.TITLE.value, MediaSortChoices.TITLE.label),
            (
                MediaSortChoices.CRITIC_RATING.value,
                MediaSortChoices.CRITIC_RATING.label,
            ),
            (MediaSortChoices.POPULARITY.value, MediaSortChoices.POPULARITY.label),
            ("votes", "Votes"),
        ]
    )

    # Build filter options from tracked (watched) items using the shared function so
    # any new filter dimension added there automatically appears here too.
    filter_data = build_filter_data_from_items(watched_items_for_filter_data)

    # Supplement with filmography-level data not available from tracked items.
    filter_data["departments"] = all_departments if not is_author else []
    filter_data["tags"] = []
    filter_data["show_progress"] = False

    # Years: merge watched-item years with years from the full unfiltered filmography
    # so the year filter covers films not yet tracked by the user.
    watched_years = {
        int(y["value"]) for y in filter_data["years"] if y["value"].isdigit()
    }
    combined_years = sorted(watched_years | set(all_filmography_years), reverse=True)
    filter_data["years"] = [{"value": str(y), "label": str(y)} for y in combined_years]

    attach_card_media(request.user, [*watched_filmography, *filmography], source)

    context = {
        "user": request.user,
        "person": person_data,
        "is_author": is_author,
        "watched_filmography": watched_filmography,
        "watched_movie_count": watched_movie_count,
        "watched_show_count": watched_show_count,
        "watched_book_count": watched_book_count,
        "filmography": filmography,
        "history_filter_url": history_filter_url,
        "tracked_plays_count": tracked_plays_count,
        "tracked_hours_count": tracked_hours_count,
        "source": source,
        "source_url": source_url,
        # Filter/sort state (named to match media_list convention)
        "current_sort": sort_by,
        "current_direction": sort_dir,
        "current_department": filter_department,
        "current_year": filter_year,
        "current_genre": filter_genre,
        "current_implied_genre": filter_implied_genre,
        "current_source": filter_source,
        "current_rating": filter_rating,
        "current_collection": filter_collection,
        "sort_choices": person_sort_choices,
        "status_choices": [],
        "filter_data": filter_data,
        "supports_critic_rating_sort": not is_author,
        "person_detail_url": request.path,
        "talent_pending": talent_pending,
    }
    if request.GET.get("format") == "json":
        return JsonResponse(_people_person_contract(context))
    if talent_poll is not None:
        return render(request, "app/components/person_talent_ready.html", context)
    if request.headers.get("HX-Request"):
        return render(
            request, "app/components/person_filmography_fragment.html", context
        )
    return render(request, "app/person_detail.html", context)


def studio_detail(request, source, studio_id, name):
    """Render a provider-backed studio/company profile page."""
    del name  # URL slug is cosmetic; studio_id is canonical.

    studio = get_object_or_404(
        Studio,
        source=source,
        source_studio_id=str(studio_id),
    )

    studio_profile = (
        igdb.company_profile(studio_id) if source == Sources.IGDB.value else None
    )

    local_titles = []
    studio_credits = studio.item_credits.select_related("item").order_by(
        "sort_order",
        "item__title",
    )
    for index, studio_credit in enumerate(studio_credits):
        item = studio_credit.item
        if not item:
            continue
        local_titles.append(
            {
                "media_id": str(item.media_id),
                "source": item.source,
                "media_type": item.media_type,
                "title": item.title,
                "image": item.image or settings.IMG_NONE,
                "year": item.release_datetime.year if item.release_datetime else None,
                "role": "",
                "department": "",
                "credit_type": item.media_type,
                "sort_order": (
                    studio_credit.sort_order
                    if studio_credit.sort_order is not None
                    else index
                ),
                "tracked_item": item,
            },
        )

    credited_titles = []
    if studio_profile:
        credited_titles = [
            dict(entry)
            for entry in studio_profile.get("games") or []
            if isinstance(entry, dict)
        ]

    if credited_titles:
        existing_keys = {
            (entry.get("media_type"), str(entry.get("media_id")))
            for entry in credited_titles
        }
        for entry in local_titles:
            media_key = (entry.get("media_type"), str(entry.get("media_id")))
            if media_key not in existing_keys:
                credited_titles.append(entry)
    else:
        credited_titles = local_titles

    if credited_titles:
        game_ids = {
            str(entry.get("media_id"))
            for entry in credited_titles
            if entry.get("media_id") is not None
        }
        tracked_items = Item.objects.filter(
            source=source,
            media_id__in=game_ids,
        )
        tracked_item_map = {
            (item.media_type, str(item.media_id)): item for item in tracked_items
        }
        for entry in credited_titles:
            media_key = (entry.get("media_type"), str(entry.get("media_id")))
            entry["tracked_item"] = tracked_item_map.get(media_key)

        credited_titles.sort(
            key=lambda row: (
                row.get("year") is None,
                -(row.get("year") or 0),
                row.get("title", "").lower(),
            ),
        )
        for index, entry in enumerate(credited_titles):
            entry["sort_order"] = index

    studio_description = "Studio profile generated from local credits."
    studio_source_url = ""
    studio_founded = None
    studio_developed_count = None
    studio_published_count = None
    if studio_profile:
        studio_description = studio_profile.get("description") or studio_description
        studio_source_url = studio_profile.get("source_url") or ""
        studio_details = studio_profile.get("details") or {}
        studio_founded = studio_details.get("founded")
        studio_developed_count = studio_details.get("developed_count")
        studio_published_count = studio_details.get("published_count")

    attach_card_media(request.user, credited_titles, source)

    media_types_present = {entry.get("media_type") for entry in credited_titles}
    credited_media_type = (
        media_types_present.pop() if len(media_types_present) == 1 else None
    )

    context = {
        "user": request.user,
        "studio": studio,
        "source": source,
        "credited_titles": credited_titles,
        "credited_media_type": credited_media_type,
        "studio_description": studio_description,
        "studio_source_url": studio_source_url,
        "studio_founded": studio_founded,
        "studio_developed_count": studio_developed_count,
        "studio_published_count": studio_published_count,
        "studio_games_count": len(credited_titles),
        "IMG_NONE": settings.IMG_NONE,
    }
    if request.GET.get("format") == "json":
        return JsonResponse(_people_studio_contract(context))
    return render(request, "app/studio_detail.html", context)
