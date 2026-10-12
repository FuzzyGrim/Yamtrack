import logging

import requests
from django.core.cache import cache

from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://graphql.anilist.co"
provider = "anilist"

# AniList returns at most 25 items per page, cap the pages fetched per media
max_pages = 8

# Voice actor languages supported by AniList
VOICE_ACTOR_LANGUAGES = [
    "japanese",
    "english",
    "korean",
    "italian",
    "spanish",
    "portuguese",
    "french",
    "german",
    "hebrew",
    "hungarian",
]

credits_query = """
query ($idMal: Int, $type: MediaType, $page: Int, $language: StaffLanguage) {
  Media(idMal: $idMal, type: $type) {
    characters(sort: [ROLE, RELEVANCE, ID], page: $page, perPage: 25) {
      pageInfo {
        hasNextPage
      }
      edges {
        role
        node {
          id
          name {
            full
          }
          image {
            large
          }
          siteUrl
        }
        voiceActors(language: $language, sort: [RELEVANCE, ID]) {
          id
          name {
            full
          }
          image {
            large
          }
          siteUrl
        }
      }
    }
    staff(sort: [RELEVANCE, ID], page: $page, perPage: 25) {
      pageInfo {
        hasNextPage
      }
      edges {
        role
        node {
          id
          name {
            full
          }
          image {
            large
          }
          siteUrl
        }
      }
    }
  }
}
"""


def media_credits(media_type, mal_id, language="japanese"):
    """Return the characters and staff of an anime or manga, by MyAnimeList ID.

    Characters include their voice actors in the given language.
    """
    cache_key = f"anilist_{media_type}_{mal_id}_{language}_credits"
    data = cache.get(cache_key)

    if data is None:
        anilist_type = "ANIME" if media_type == MediaTypes.ANIME.value else "MANGA"
        characters = []
        staff = {}

        for page in range(1, max_pages + 1):
            variables = {
                "idMal": int(mal_id),
                "type": anilist_type,
                "page": page,
                "language": language.upper(),
            }
            try:
                response = services.api_request(
                    "ANILIST",
                    "POST",
                    base_url,
                    params={"query": credits_query, "variables": variables},
                )
            except requests.exceptions.HTTPError as error:
                status_code = getattr(error.response, "status_code", None)
                if status_code == requests.codes.not_found:
                    services.raise_not_found_error(
                        Sources.MAL.value,
                        mal_id,
                        media_type,
                    )
                raise services.ProviderAPIError(provider, error) from error

            media = (response.get("data") or {}).get("Media")
            if not media:
                services.raise_not_found_error(Sources.MAL.value, mal_id, media_type)

            characters.extend(
                process_character(edge) for edge in media["characters"]["edges"]
            )
            for edge in media["staff"]["edges"]:
                process_staff(staff, edge)

            if not (
                media["characters"]["pageInfo"]["hasNextPage"]
                or media["staff"]["pageInfo"]["hasNextPage"]
            ):
                break

        data = {"characters": characters, "staff": list(staff.values())}
        cache.set(cache_key, data)

    return data


def process_character(edge):
    """Process a character edge with its voice actors."""
    node = edge["node"]
    return {
        "id": node["id"],
        "name": node["name"]["full"],
        "role": (edge.get("role") or "").title() or None,
        "image": (node.get("image") or {}).get("large"),
        "source_url": node.get("siteUrl"),
        "voice_actors": [
            {
                "id": actor["id"],
                "name": actor["name"]["full"],
                "image": (actor.get("image") or {}).get("large"),
                "source_url": actor.get("siteUrl"),
            }
            for actor in edge.get("voiceActors") or []
        ],
    }


def process_staff(staff, edge):
    """Add a staff edge, merging the roles of people credited more than once."""
    node = edge["node"]
    entry = staff.setdefault(
        node["id"],
        {
            "id": node["id"],
            "name": node["name"]["full"],
            "roles": [],
            "image": (node.get("image") or {}).get("large"),
            "source_url": node.get("siteUrl"),
        },
    )
    role = edge.get("role")
    if role and role not in entry["roles"]:
        entry["roles"].append(role)
