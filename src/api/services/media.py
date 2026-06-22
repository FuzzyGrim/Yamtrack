import hashlib
from copy import deepcopy

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count

from api.serializers.common import (
    cast_from_metadata,
    crew_from_metadata,
    custom_poster_url_for_user,
    details_for_api,
    episodes_from_metadata,
    find_item,
    media_summary_from_provider,
    related_sections_from_payload,
    seasons_from_metadata,
    synopsis_from_payload,
)
from app import config
from app.models import CustomPosterPreference, Item, MediaTypes, Sources
from app.providers import services as provider_services
from app.utils.color import build_accent_palette, compute_and_store_poster_accent

SEARCH_TTL = 60 * 60 * 6
DETAIL_TTL = 60 * 60 * 24
DETAIL_CACHE_VERSION = "v3"


def default_source_for(media_type):
    """Return the configured default source value for a media type."""
    return config.get_default_source_name(media_type).value


def search_media(*, media_type, query, page=1, source=None, request=None, user=None):
    """Search provider metadata with a versioned cache key."""
    source = source or default_source_for(media_type)
    query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:24]
    cache_key = (
        f"api:v1:search:{media_type}:{source}:{query_hash}:"
        f"p{page}:u{getattr(settings, 'TMDB_LANG', 'en')}:nsfw{settings.TMDB_NSFW}"
    )
    data = cache.get(cache_key)
    if data is None:
        data = provider_services.search(media_type, query, page, source)
        cache.set(cache_key, data, SEARCH_TTL)

    raw_results = data.get("results") or data.get("items") or []
    return [
        media_summary_from_provider(
            item,
            media_type=item.get("media_type", media_type),
            source=item.get("source", source),
            request=request,
            user=user,
        )
        for item in raw_results
    ]


def media_detail(*, source, media_type, media_id, request=None, user=None, season_number=None, episode_number=None):
    """Fetch provider metadata and normalize it for the API."""
    cache_key = (
        f"api:{DETAIL_CACHE_VERSION}:detail:{source}:{media_type}:{media_id}:"
        f"s{season_number}:e{episode_number}:u{getattr(settings, 'TMDB_LANG', 'en')}"
    )
    metadata = cache.get(cache_key)
    if metadata is None:
        season_numbers = [season_number] if season_number is not None else None
        metadata = provider_services.get_media_metadata(
            media_type,
            media_id,
            source,
            season_numbers,
            episode_number,
        )
        cache.set(cache_key, metadata, DETAIL_TTL)

    summary = media_summary_from_provider(
        {
            **metadata,
            "media_id": media_id,
            "media_type": media_type,
            "source": source,
            "season_number": season_number,
            "episode_number": episode_number,
        },
        media_type,
        source,
        request=request,
        user=user,
    )
    synopsis = synopsis_from_payload(metadata) or summary.get("overview")
    if synopsis:
        summary["overview"] = synopsis
    ref = summary["ref"]
    if summary.get("poster_accent_color") is None:
        summary["poster_accent_color"] = poster_accent_color(metadata, ref)
    return {
        **summary,
        "overview": synopsis,
        "synopsis": synopsis,
        "backdrop_url": backdrop_url(metadata),
        "details": details_for_api(metadata),
        "cast": cast_from_metadata(metadata, request=request),
        "crew": crew_from_metadata(metadata, request=request),
        "seasons": seasons_from_metadata(metadata, request=request) if media_type == MediaTypes.TV.value else [],
        "episodes": episodes_from_metadata(enrich_episodes(metadata, source, user), request=request)
        if media_type == MediaTypes.SEASON.value
        else [],
        "custom_poster_url": custom_poster_url_for_user(user, ref, request=request),
        "related": metadata.get("related", {}),
        "related_sections": related_sections_from_payload(
            metadata.get("related", {}),
            media_type=media_type,
            source=source,
            request=request,
            user=user,
        ),
        "providers": watch_providers_for_user(metadata, user),
        "community": community_stats(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=season_number,
        ),
        "external_ratings": external_ratings(
            metadata=metadata,
            source=source,
            media_type=media_type,
            media_id=media_id,
        ),
    }


def poster_options(*, source, media_type, media_id, request=None, user=None):
    """Return selectable posters for TMDB movie/TV media."""
    if source != Sources.TMDB.value or media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        raise ValueError("Poster customization is only available for TMDB movies and TV shows.")

    item = _poster_item(source=source, media_type=media_type, media_id=media_id)
    from app.providers import tmdb

    current = CustomPosterPreference.objects.filter(user=user, item=item).first()
    selected_url = current.custom_image_url if current else item.image
    original = {
        "url": absolute_poster_url(request, item.image),
        "thumbnail_url": absolute_poster_url(request, item.image),
        "width": 0,
        "height": 0,
        "aspect_ratio": 0.667,
        "vote_average": 0,
        "vote_count": 0,
        "language": None,
        "is_original": True,
        "is_selected": item.image == selected_url,
    }

    posters = [original]
    for poster in tmdb.get_poster_images(media_id, media_type):
        if poster["url"] == item.image:
            continue
        posters.append(
            {
                **poster,
                "language": poster.get("language"),
                "is_original": False,
                "is_selected": poster["url"] == selected_url,
            },
        )
    return {"posters": posters}


def save_poster_preference(*, source, media_type, media_id, poster_url, user):
    """Save a user's poster preference and update the stored item image/accent."""
    if source != Sources.TMDB.value or media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        raise ValueError("Poster customization is only available for TMDB movies and TV shows.")
    if not poster_url:
        raise ValueError("poster_url is required.")

    item = _poster_item(source=source, media_type=media_type, media_id=media_id)
    accent = compute_and_store_poster_accent(item, poster_url=poster_url, force=True)
    palette = build_accent_palette(accent)
    CustomPosterPreference.objects.update_or_create(
        user=user,
        item=item,
        defaults={"custom_image_url": poster_url},
    )
    item.image = poster_url
    item.poster_accent_color = palette["accent"]
    item.save(update_fields=["image", "poster_accent_color"])
    return {
        "poster_url": poster_url,
        "custom_poster_url": poster_url,
        "poster_accent_color": palette["accent"],
    }


def _poster_item(*, source, media_type, media_id):
    item = Item.objects.filter(source=source, media_type=media_type, media_id=media_id).first()
    if item is not None:
        return item
    metadata = provider_services.get_media_metadata(media_type, media_id, source)
    return Item.objects.create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        title=metadata.get("title") or metadata.get("name") or media_id,
        image=metadata.get("image") or settings.IMG_NONE,
    )


def absolute_poster_url(request, url):
    """Return an absolute poster URL without substituting the global placeholder."""
    if not url:
        return None
    if str(url).startswith(("http://", "https://")):
        return url
    return request.build_absolute_uri(url) if request is not None else url


def backdrop_url(metadata):
    """Return an absolute backdrop URL when available."""
    value = metadata.get("backdrop") or metadata.get("backdrop_url") or metadata.get("backdrop_path")
    if isinstance(value, str) and value.startswith("/"):
        return f"https://image.tmdb.org/t/p/original{value}"
    return value


def poster_accent_color(metadata, ref):
    """Compute an accent only when there is no stored Item color."""
    item = find_item(ref)
    if item is not None and item.poster_accent_color:
        return item.poster_accent_color
    return None


def watch_providers_for_user(metadata, user):
    """Return provider availability, region-filtered for authenticated users."""
    providers = metadata.get("providers")
    if not providers:
        return providers
    region = getattr(user, "watch_provider_region", None) if user and user.is_authenticated else None
    if not region or region == "UNSET":
        return providers
    from app.providers import tmdb

    return tmdb.filter_providers(deepcopy(providers), region)


def enrich_episodes(metadata, source, user):
    """Apply provider episode formatting when tracking rows exist."""
    if not metadata.get("episodes"):
        return metadata
    from app.models import BasicMedia
    from app.providers import manual, tmdb

    episodes_in_db = []
    if user and user.is_authenticated:
        current = BasicMedia.objects.filter_media(
            user,
            metadata.get("media_id"),
            MediaTypes.SEASON.value,
            source,
            metadata.get("season_number"),
        ).first()
        episodes_in_db = current.episodes.all() if current else []

    payload = dict(metadata)
    if source == "manual":
        payload["episodes"] = manual.process_episodes(metadata, episodes_in_db)
    elif source == "tmdb":
        payload["episodes"] = tmdb.process_episodes(metadata, episodes_in_db)
    return payload


def external_ratings(*, metadata, source, media_type, media_id):
    """Normalize provider and third-party ratings for media detail."""
    ratings = []
    score = metadata.get("score")
    if score is not None:
        ratings.append(
            {
                "source": source_label(source),
                "value": str(score),
                "vote_count": metadata.get("score_count"),
                "max_value": max_rating_value(source),
            },
        )

    if source == "tmdb" and media_type in {MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value}:
        from app.providers import mdblist

        mdblist_type = MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
        for rating_source, rating in (mdblist.get_media_ratings(media_id, mdblist_type) or {}).items():
            value = rating.get("value") or rating.get("score")
            if value is None:
                continue
            ratings.append(
                {
                    "source": source_label(rating_source),
                    "value": str(value),
                    "vote_count": rating.get("votes"),
                    "max_value": max_rating_value(rating_source),
                },
            )

    return ratings


def source_label(source):
    """Return user-facing source names."""
    return {
        "igdb": "IGDB",
        "imdb": "IMDb",
        "letterboxd": "Letterboxd",
        "mal": "MAL",
        "mangaupdates": "MangaUpdates",
        "openlibrary": "OpenLibrary",
        "hardcover": "Hardcover",
        "tmdb": "TMDB",
        "tomatoes": "Rotten Tomatoes",
    }.get(source, source.title())


def max_rating_value(source):
    """Return display max for known rating scales."""
    return {
        "hardcover": "5",
        "letterboxd": "5",
        "openlibrary": "5",
        "tomatoes": "100%",
    }.get(source, "10")


def tv_seasons(*, source, media_id, request=None, user=None):
    """Return season summaries for a TV show."""
    detail = media_detail(
        source=source,
        media_type=MediaTypes.TV.value,
        media_id=media_id,
        request=request,
        user=user,
    )
    return {"seasons": detail.get("seasons", [])}


def season_detail(*, source, media_id, season_number, request=None, user=None):
    """Return normalized season detail."""
    return media_detail(
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
        request=request,
        user=user,
    )


def season_episodes(*, source, media_id, season_number, request=None, user=None):
    """Return episode summaries for a season."""
    detail = season_detail(
        source=source,
        media_id=media_id,
        season_number=season_number,
        request=request,
        user=user,
    )
    return {"episodes": detail.get("episodes", [])}


def community_stats(*, source, media_type, media_id, season_number=None):
    """Return current community aggregates for a media identity."""
    from app.models import DiaryEntry, Item
    from social.models import ContentLike

    item = Item.objects.filter(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
    ).first()
    if item is None:
        return {
            "average_rating": None,
            "rating_count": 0,
            "diary_count": 0,
            "review_count": 0,
            "liked_count": 0,
            "rating_distribution": [],
        }
    entries = DiaryEntry.objects.filter(item=item).exclude(visibility="private")
    rating_values = [entry.rating for entry in entries if entry.rating is not None]
    average = round(sum(rating_values) / len(rating_values), 2) if rating_values else None
    distribution = [
        {"rating": str(bucket["rating"]), "count": bucket["count"]}
        for bucket in entries.exclude(rating__isnull=True)
        .values("rating")
        .annotate(count=Count("id"))
        .order_by("rating")
    ]
    return {
        "average_rating": str(average) if average is not None else None,
        "rating_count": len(rating_values),
        "diary_count": entries.count(),
        "review_count": entries.exclude(review="").count(),
        "liked_count": ContentLike.objects.filter(
            target_type=ContentLike.DIARY_ENTRY,
            target_id__in=entries.values("id"),
        ).count(),
        "rating_distribution": distribution,
    }
