from django.core.cache import cache

import app

CACHE_KEY = "anibridge_v3_mapping_data"
URL = (
    "https://github.com/anibridge/anibridge-mappings/releases/download/v3/"
    "mappings.min.json"
)


def fetch_mapping_data():
    """Fetch anime mapping data with caching."""
    data = cache.get(CACHE_KEY)
    if data is None:
        data = app.providers.services.api_request(
            "GITHUB",
            "GET",
            URL,
        )
        cache.set(CACHE_KEY, data)
    return data


def get_mal_id_from_anidb(mapping_data, anidb_id, episode_number):
    """Find a MAL ID from AniBridge's AniDB-based anime mappings."""
    descriptors = [f"anidb:{anidb_id}:R"]
    descriptors.extend(
        descriptor
        for descriptor in mapping_data
        if descriptor.startswith(f"anidb:{anidb_id}:") and descriptor not in descriptors
    )

    for descriptor in descriptors:
        mal_id, mal_episode_number = _get_mal_mapping(
            mapping_data,
            descriptor,
            episode_number,
        )
        if mal_id:
            return mal_id, mal_episode_number
    return None, None


def get_mal_id_from_tvdb(
    mapping_data,
    tvdb_id,
    season_number,
    episode_number,
):
    """Find a MAL ID from AniBridge's TVDB-based anime mappings."""
    return _get_mal_mapping(
        mapping_data,
        f"tvdb_show:{tvdb_id}:s{season_number}",
        episode_number,
    )


def get_mal_id_from_tmdb_movie(mapping_data, tmdb_movie_id):
    """Find MAL ID from TMDB movie mapping."""
    return _get_mal_mapping(
        mapping_data,
        f"tmdb_movie:{tmdb_movie_id}",
    )


def get_mal_id_from_imdb(mapping_data, imdb_id):
    """Find MAL ID from IMDB ID mapping."""
    return _get_mal_mapping(
        mapping_data,
        f"imdb_movie:{imdb_id}",
    )


def _get_mal_mapping(mapping_data, source_descriptor, episode_number=None):
    """Return a MAL ID and optional episode number for an AniBridge descriptor."""
    targets = mapping_data.get(source_descriptor, {})
    for target_descriptor, ranges in targets.items():
        mal_id = _parse_mal_descriptor(target_descriptor)
        if mal_id is None:
            continue

        if episode_number is None:
            return mal_id

        mapped_episode_number = _map_episode_number(ranges, episode_number)
        if mapped_episode_number is not None:
            return mal_id, mapped_episode_number

    if episode_number is None:
        return None
    return None, None


def _parse_mal_descriptor(descriptor):
    """Parse MAL ID from an AniBridge target descriptor."""
    provider, media_id, *_ = descriptor.split(":")
    if provider != "mal":
        return None
    return _parse_mal_id(media_id)


def _map_episode_number(ranges, episode_number):
    """Map a source episode number through AniBridge episode ranges."""
    if not ranges:
        return episode_number

    for source_range, target_range in ranges.items():
        source_start, source_end = _parse_episode_range(source_range)
        if episode_number < source_start:
            continue
        if source_end is not None and episode_number > source_end:
            continue

        source_offset = episode_number - source_start
        return _map_target_episode_number(target_range, source_offset)

    return None


def _map_target_episode_number(target_range, source_offset):
    """Return the target episode at a zero-based source range offset."""
    target_ranges, _, ratio_text = target_range.partition("|")
    target_start, _ = _parse_episode_range(target_ranges.split(",")[0])

    if ratio_text:
        ratio = int(ratio_text)
        if ratio > 0:
            return target_start + ((source_offset + 1) * ratio) - 1
        return target_start + ((source_offset + 1) // abs(ratio))

    remaining_offset = source_offset
    for target_range_part in target_ranges.split(","):
        target_start, target_end = _parse_episode_range(target_range_part)
        if target_end is None:
            return target_start + remaining_offset

        target_length = target_end - target_start + 1
        if remaining_offset < target_length:
            return target_start + remaining_offset
        remaining_offset -= target_length

    return None


def _parse_episode_range(episode_range):
    """Parse an AniBridge episode range into start and optional end numbers."""
    if "-" not in episode_range:
        episode_number = int(episode_range)
        return episode_number, episode_number

    start, end = episode_range.split("-", 1)
    return int(start), int(end) if end else None


def _parse_mal_id(mal_id):
    """Parse MAL ID from potentially comma-separated string."""
    if isinstance(mal_id, str) and "," in mal_id:
        mal_id = mal_id.split(",")[0].strip()
    if isinstance(mal_id, str) and mal_id.isdigit():
        return int(mal_id)
    return mal_id
