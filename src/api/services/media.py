import hashlib

from django.conf import settings
from django.core.cache import cache

from api.serializers.common import media_summary_from_provider, synopsis_from_payload
from app import config
from app.models import MediaTypes
from app.providers import services as provider_services

SEARCH_TTL = 60 * 60 * 6
DETAIL_TTL = 60 * 60 * 24
DETAIL_CACHE_VERSION = "v2"


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
    return {
        **summary,
        "overview": synopsis,
        "synopsis": synopsis,
        "backdrop_url": metadata.get("backdrop") or metadata.get("backdrop_url"),
        "details": metadata.get("details", {}),
        "related": metadata.get("related", {}),
        "providers": metadata.get("providers"),
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
    seasons = detail.get("related", {}).get("seasons", [])
    return {
        "seasons": [
            {
                "season_number": season.get("season_number"),
                "title": season.get("title") or season.get("name"),
                "episode_count": season.get("episode_count") or season.get("episodes"),
                "image_url": season.get("image"),
                "release_date": season.get("first_air_date") or season.get("air_date"),
            }
            for season in seasons
        ],
    }


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
    return {
        "episodes": [
            {
                "episode_number": episode.get("episode_number"),
                "title": episode.get("title") or episode.get("name"),
                "overview": episode.get("overview"),
                "air_date": episode.get("air_date"),
                "runtime_minutes": episode.get("runtime"),
                "image_url": episode.get("image") or episode.get("still_path"),
            }
            for episode in detail.get("episodes", [])
        ],
    }


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
        }
    entries = DiaryEntry.objects.filter(item=item).exclude(visibility="private")
    rating_values = [entry.rating for entry in entries if entry.rating is not None]
    average = round(sum(rating_values) / len(rating_values), 2) if rating_values else None
    return {
        "average_rating": str(average) if average is not None else None,
        "rating_count": len(rating_values),
        "diary_count": entries.count(),
        "review_count": entries.exclude(review="").count(),
        "liked_count": ContentLike.objects.filter(
            target_type=ContentLike.DIARY_ENTRY,
            target_id__in=entries.values("id"),
        ).count(),
    }
