"""OpenAPI response examples shared by the API views."""

from drf_spectacular.utils import OpenApiExample

NO_IMAGE = "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg"


def _example(name, value):
    """Build an example whose summary is its name."""
    return OpenApiExample(name, summary=name, value=value)


def _page(results):
    """Wrap the results in the first page of a paginated response."""
    return {
        "pagination": {
            "total": len(results),
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": results,
    }


def _untracked_media(item, item_id, parent_id):
    """Build a media that the user doesn't track."""
    return {
        "id": None,
        "consumption_id": None,
        "item": item,
        "item_id": item_id,
        "parent_id": parent_id,
        "tracked": False,
        "created_at": None,
        "score": None,
        "status": None,
        "progress": None,
        "progressed_at": None,
        "start_date": None,
        "end_date": None,
        "notes": None,
        "lists": [],
    }


LIST_ITEM = {"list_id": 2, "list_item_id": 28}

CONSUMPTION = {
    "consumption_id": 312,
    "created": "2026-03-19T10:31:55.747255Z",
    "score": 10.0,
    "progress": 26,
    "progressed_at": "2026-03-19T10:41:00Z",
    "status": 1,
    "start_date": "2026-03-19T10:31:00Z",
    "end_date": "2026-03-19T10:41:00Z",
    "notes": "Watched with friends",
}

TV_MEDIA = {
    "id": 1,
    "consumption_id": 1,
    "item": {
        "media_id": "100088",
        "source": "tmdb",
        "media_type": "tv",
        "title": "The Last of Us",
        "image": "https://image.tmdb.org/t/p/original//dmo6TYuuJgaYinXBPjrgG9mB5od.jpg",
        "season_number": None,
        "episode_number": None,
    },
    "item_id": "tv/tmdb/100088",
    "parent_id": None,
    "tracked": True,
    "created_at": "2026-01-15T15:33:03.209328Z",
    "score": None,
    "status": 0,
    "progress": 0,
    "progressed_at": None,
    "start_date": None,
    "end_date": None,
    "notes": "",
    "lists": [{"list_id": 1, "list_item_id": 10}],
}

COMIC_MEDIA = {
    "id": 2902,
    "consumption_id": 1,
    "item": {
        "media_id": "1",
        "source": "manual",
        "media_type": "comic",
        "title": "Comic 1",
        "image": NO_IMAGE,
        "season_number": None,
        "episode_number": None,
    },
    "item_id": "comic/manual/1",
    "parent_id": None,
    "tracked": True,
    "created_at": "2026-03-23T21:16:16.978287Z",
    "score": None,
    "status": 3,
    "progress": 0,
    "progressed_at": "2026-03-23T21:16:16.965721Z",
    "start_date": None,
    "end_date": None,
    "notes": "",
    "lists": [{"list_id": 1, "list_item_id": 16}],
}

CUSTOM_LIST = {
    "id": 1,
    "name": "Favorites",
    "description": "My favorite media",
    "image": NO_IMAGE,
    "owner": {"id": 1, "username": "admin"},
    "collaborators": [],
    "items_count": 1,
    "latest_update": "2026-03-23T21:17:32.705709Z",
}

EVENTS_EXAMPLE = _example(
    "Events",
    _page(
        [
            {
                "id": 5086,
                "item": {
                    "media_id": "208569",
                    "source": "tmdb",
                    "media_type": "episode",
                    "title": "Will Trent",
                    "image": "https://image.tmdb.org/t/p/w500/qG5O46gUxxYGImld03tl2zLhvrg.jpg",
                    "season_number": 4,
                    "episode_number": 13,
                },
                "item_id": "tv/tmdb/208569/4/13",
                "parent_id": "tv/tmdb/208569/4",
                "content_number": 13,
                "datetime": "2026-04-01T00:00:00Z",
                "notification_sent": False,
            },
        ],
    ),
)

TASK_QUEUED_EXAMPLE = _example("Task queued", {"detail": "Task queued"})

HEALTH_EXAMPLE = _example(
    "Healthy API",
    {
        "status": "ok",
        "timestamp": "2026-11-04T16:43:24.430297Z",
        "checks": {
            "Cache(alias='default')": {"status": "ok", "error": None},
            "Database(alias='default')": {"status": "ok", "error": None},
            "Ping(app=<Celery yamtrack at 0x000000000000>)": {
                "status": "ok",
                "error": None,
            },
            "Redis(host='localhost', port=6379)": {"status": "ok", "error": None},
        },
    },
)

INFO_EXAMPLE = _example(
    "Info",
    {
        "version": "dev",
        "debug": True,
        "frontend_url": "http://localhost:8000",
        "language": "en-us",
        "timezone": "UTC",
        "admin_enabled": True,
        "track_time": True,
    },
)

LISTS_EXAMPLE = _example("Lists", _page([CUSTOM_LIST]))

LIST_EXAMPLE = _example("List", {**CUSTOM_LIST, "items": _page([])})

LIST_PATCH_EXAMPLE = _example("Updated list", CUSTOM_LIST)

CREATED_LIST_EXAMPLE = _example(
    "Created list",
    CUSTOM_LIST,
)

LIST_ITEMS_EXAMPLE = _example("List items", _page([COMIC_MEDIA]))

MEDIA_LIST_EXAMPLE = _example("Media list", _page([TV_MEDIA]))

TRACKED_MEDIA_EXAMPLE = _example(
    "Tracked media",
    {
        "id": 3599,
        "consumption_id": 322,
        "item": {
            "media_id": "12345",
            "source": "tmdb",
            "media_type": "tv",
            "title": "Van der Valk",
            "image": "https://image.tmdb.org/t/p/w500/7cSyT7qrAVHOdiaUqcZAdj4Jny1.jpg",
            "season_number": None,
            "episode_number": None,
        },
        "item_id": "tv/tmdb/12345",
        "parent_id": None,
        "tracked": True,
        "created_at": "2026-05-11T17:20:38.252187Z",
        "score": None,
        "status": 0,
        "progress": 0,
        "progressed_at": None,
        "start_date": None,
        "end_date": None,
        "notes": "",
        "lists": [],
    },
)

MEDIA_DETAILS_EXAMPLE = _example(
    "Media details",
    {
        "id": 3599,
        "media_id": "12345",
        "source": "tmdb",
        "source_url": "https://www.themoviedb.org/tv/12345",
        "media_type": "tv",
        "title": "Van der Valk",
        "max_progress": 32,
        "image": "https://image.tmdb.org/t/p/w500/7cSyT7qrAVHOdiaUqcZAdj4Jny1.jpg",
        "synopsis": "Van der Valk is a British television...",
        "genres": ["Drama"],
        "score": 6.2,
        "score_count": 10,
        "details": {
            "format": "TV",
            "first_air_date": "1972-09-13",
            "last_air_date": "1992-02-19",
            "status": "Ended",
            "seasons": 5,
            "episodes": 32,
            "runtime": "50m",
            "studios": None,
            "country": None,
            "languages": ["English"],
            "tvdb_id": 78290,
            "last_episode_season": 5,
            "next_episode_season": None,
        },
        "related": {
            "seasons": [
                _untracked_media(
                    {
                        "media_id": "12345",
                        "source": "tmdb",
                        "media_type": "season",
                        "title": "Season 1",
                        "image": NO_IMAGE,
                        "season_number": 1,
                        "episode_number": None,
                    },
                    "tv/tmdb/12345/1",
                    "tv/tmdb/12345",
                ),
            ],
        },
        "external_links": {
            "IMDb": "https://www.imdb.com/title/tt0068152/",
            "TVDB": "https://www.thetvdb.com/dereferrer/series/78290",
        },
        "time_to_beat": None,
        "item_id": "tv/tmdb/12345",
        "parent_id": None,
        "tracked": True,
        "consumptions_number": 1,
        "consumptions": [
            {
                "consumption_id": 322,
                "created": "2026-05-11T17:20:38.252187Z",
                "score": None,
                "progress": 0,
                "progressed_at": None,
                "status": 0,
                "start_date": None,
                "end_date": None,
                "notes": "",
            },
        ],
        "lists": [],
    },
)

SEASON_DETAILS_EXAMPLE = _example(
    "Season details",
    {
        "id": 313,
        "media_id": "105248",
        "source": "tmdb",
        "source_url": "https://www.themoviedb.org/tv/105248/season/1",
        "media_type": "season",
        "title": "Cyberpunk: Edgerunners",
        "max_progress": 10,
        "image": "https://image.tmdb.org/t/p/w500/6MMjX9T0L7eoRfZFTnzC6WXYZLK.jpg",
        "synopsis": "In a dystopia riddled with corruption and cybernetic implants...",
        "genres": ["Animation", "Action & Adventure", "Drama", "Sci-Fi & Fantasy"],
        "score": 8.3,
        "score_count": 688,
        "details": {
            "first_air_date": "2022-09-12",
            "last_air_date": "2022-09-13",
            "episodes": 10,
            "runtime": "26m",
            "total_runtime": "4h 25m",
            "tvdb_id": 384541,
        },
        "related": {
            "episodes": [
                _untracked_media(
                    {
                        "media_id": "105248",
                        "source": "tmdb",
                        "media_type": "episode",
                        "title": "Let You Down",
                        "image": "https://image.tmdb.org/t/p/original/egBHU73t79tMg2qrqj3aJof1ibS.jpg",
                        "season_number": 1,
                        "episode_number": 1,
                    },
                    "tv/tmdb/105248/1/1",
                    "tv/tmdb/105248/1",
                ),
            ],
        },
        "external_links": {
            "IMDb": "https://www.imdb.com/title/tt12590266/",
            "TVDB": "https://www.thetvdb.com/dereferrer/series/384541",
        },
        "time_to_beat": None,
        "item_id": "tv/tmdb/105248/1",
        "parent_id": "tv/tmdb/105248",
        "tracked": True,
        "consumptions_number": 1,
        "consumptions": [
            {
                "consumption_id": 3,
                "created": "2026-01-15T15:33:03.302349Z",
                "score": None,
                "progress": 1,
                "progressed_at": "2026-01-15T20:13:00Z",
                "status": 1,
                "start_date": "2025-09-17T09:40:00Z",
                "end_date": "2026-01-15T20:13:00Z",
                "notes": "",
            },
        ],
        "lists": [{"list_id": 1, "list_item_id": 1}],
    },
)

EPISODE_DETAILS_EXAMPLE = _example(
    "Episode details",
    {
        "id": 3612,
        "media_id": "12345",
        "source": "tmdb",
        "source_url": "https://www.themoviedb.org/tv/12345/season/1/episode/1",
        "media_type": "episode",
        "title": "Destination Unknown",
        "max_progress": 1,
        "image": "https://image.tmdb.org/t/p/original/9xX4Ycm7vN0Qb4JtCjGfC2iC2nH.jpg",
        "synopsis": "Van der Valk investigates a disappearance...",
        "genres": ["Drama"],
        "score": 7.0,
        "score_count": 3,
        "details": {
            "air_date": "1972-09-13",
            "episode_number": 1,
            "season_number": 1,
            "runtime": 50,
            "episode_type": "standard",
            "crew": [],
            "guest_stars": [],
        },
        "related": {},
        "item_id": "tv/tmdb/12345/1/1",
        "parent_id": "tv/tmdb/12345/1",
        "tracked": True,
        "consumptions_number": 1,
        "consumptions": [
            {
                "consumption_id": 322,
                "created": None,
                "score": None,
                "progress": 1,
                "progressed_at": None,
                "status": 3,
                "start_date": None,
                "end_date": "2026-05-11T17:20:38Z",
                "notes": "",
            },
        ],
        "lists": [],
    },
)

SEASONS_EXAMPLE = _example(
    "Seasons",
    _page(
        [
            {
                **_untracked_media(
                    {
                        "media_id": "32868",
                        "source": "tmdb",
                        "media_type": "season",
                        "title": "Nikita",
                        "image": "https://image.tmdb.org/t/p/w500/t1X9TyxfB9qlqML7EF8IGqQuOIP.jpg",
                        "season_number": 1,
                        "episode_number": None,
                    },
                    "tv/tmdb/32868/1",
                    "tv/tmdb/32868",
                ),
                "id": 2892,
                "lists": [{"list_id": 1, "list_item_id": 14}],
            },
        ],
    ),
)

SEASON_EPISODES_EXAMPLE = _example(
    "Season episodes",
    _page(
        [
            _untracked_media(
                {
                    "media_id": "32868",
                    "source": "tmdb",
                    "media_type": "episode",
                    "title": "Pilot",
                    "image": "https://image.tmdb.org/t/p/original/tmuqJdVHsbInNFKvfCfVxYt9iMW.jpg",
                    "season_number": 1,
                    "episode_number": 1,
                },
                "tv/tmdb/32868/1/1",
                "tv/tmdb/32868/1",
            ),
        ],
    ),
)

RECOMMENDATIONS_EXAMPLE = _example(
    "Recommendations",
    {
        "source": "tmdb",
        "media_type": "tv",
        "image": "https://image.tmdb.org/t/p/w500/pFao5i4giBsGl7QQBXi7oMUPzCn.jpg",
        "media_id": "167",
        "title": "La Femme Nikita",
    },
)

CONSUMPTION_HISTORY_EXAMPLE = _example("Consumption history", _page([CONSUMPTION]))

CONSUMPTION_EXAMPLE = _example("Consumption", CONSUMPTION)

MEDIA_LISTS_EXAMPLE = _example("Media lists", _page([LIST_ITEM]))

ADDED_TO_LIST_EXAMPLE = _example("Added to the list", LIST_ITEM)

METADATA_SYNCED_EXAMPLE = _example(
    "Metadata synced",
    {"detail": "Metadata synced successfully."},
)

CHANGES_HISTORY_RECORD_EXAMPLE = _example(
    "Changes history record",
    {
        "id": 312,
        "item_id": "tv/tmdb/245703",
        "timestamp": "2026-01-18T15:21:02.920479Z",
        "changes": [{"field": "status", "old_value": 3, "new_value": 1}],
    },
)

MEDIA_CHANGES_HISTORY_EXAMPLE = _example(
    "Media changes history",
    _page(
        [
            CHANGES_HISTORY_RECORD_EXAMPLE.value,
            {
                "id": 150,
                "item_id": "tv/tmdb/245703",
                "timestamp": "2025-09-17T09:41:00Z",
                "changes": [
                    {"field": "score", "old_value": None, "new_value": 9.0},
                    {"field": "status", "old_value": None, "new_value": 3},
                    {"field": "notes", "old_value": None, "new_value": ""},
                ],
            },
        ],
    ),
)

SEASON_CHANGES_HISTORY_EXAMPLE = _example(
    "Season changes history",
    _page(
        [
            {
                "id": 143,
                "item_id": "tv/tmdb/245703/1",
                "timestamp": "2026-01-18T15:15:10.443874Z",
                "changes": [
                    {"field": "notes", "old_value": "", "new_value": "Great season"},
                    {"field": "score", "old_value": 9.0, "new_value": 8.0},
                ],
            },
            {
                "id": 9,
                "item_id": "tv/tmdb/245703/1",
                "timestamp": "2025-09-17T09:41:00Z",
                "changes": [
                    {"field": "status", "old_value": None, "new_value": 3},
                    {"field": "notes", "old_value": None, "new_value": ""},
                ],
            },
        ],
    ),
)

EPISODE_CHANGES_HISTORY_EXAMPLE = _example(
    "Episode changes history",
    _page(
        [
            {
                "id": 1226,
                "item_id": "tv/tmdb/245703/1/1",
                "timestamp": "2026-01-18T15:21:02.851911Z",
                "changes": [
                    {
                        "field": "end_date",
                        "old_value": None,
                        "new_value": "2026-01-18T15:15:00Z",
                    },
                ],
            },
        ],
    ),
)

WATCH_PROVIDERS_EXAMPLE = _example(
    "Watch providers",
    {
        "region": "US",
        "link": "https://www.themoviedb.org/movie/603-the-matrix/watch?locale=US",
        "no_charge": [
            {
                "provider_id": 8,
                "provider_name": "Netflix",
                "image": "https://image.tmdb.org/t/p/w500/pbpMk2JmcoNnQwx5JGpXngfoWtp.jpg",
                "display_priority": 0,
            },
        ],
        "paid": [
            {
                "provider_id": 2,
                "provider_name": "Apple TV",
                "image": "https://image.tmdb.org/t/p/w500/9ghgSC0MA082EL6HLCW3GalykFD.jpg",
                "display_priority": 4,
            },
        ],
    },
)

CAST_EXAMPLE = _example(
    "Cast",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "person_id": 6384,
                "name": "Keanu Reeves",
                "character": "Thomas A. Anderson / Neo",
                "image": "https://image.tmdb.org/t/p/w500/4D0PpNI0kmP58hgrwGC3wCjxhnm.jpg",
                "order": 0,
                "episode_count": None,
            },
        ],
    },
)

CREW_EXAMPLE = _example(
    "Crew",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "person_id": 9340,
                "name": "Lana Wachowski",
                "department": "Directing",
                "jobs": ["Director"],
                "image": "https://image.tmdb.org/t/p/w500/2wfpFCe3nT4d3RnmL6kSHnYWJ6j.jpg",
                "episode_count": None,
            },
        ],
    },
)

CREATORS_EXAMPLE = _example(
    "Creators",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "id": "40439",
                "name": "Scott Snyder",
                "roles": [],
                "image": None,
                "issue_count": 52,
                "source_url": "https://comicvine.gamespot.com/scott-snyder/4040-40439/",
            },
        ],
    },
)

CHARACTERS_EXAMPLE = _example(
    "Characters",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "id": "1699",
                "name": "Batman",
                "roles": [],
                "image": None,
                "issue_count": 52,
                "source_url": "https://comicvine.gamespot.com/batman/4005-1699/",
            },
        ],
    },
)

COMPANIES_EXAMPLE = _example(
    "Companies",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "company_id": 908,
                "name": "CD Projekt RED",
                "logo": "https://images.igdb.com/igdb/image/upload/t_logo_med/cl6np.png",
                "roles": ["developer", "publisher"],
            },
        ],
    },
)

IMAGES_EXAMPLE = _example(
    "Images",
    {
        "posters": [
            {
                "image": "https://image.tmdb.org/t/p/w500/p96dm7sCMn4VYAStA6siNz30G1r.jpg",
                "original": "https://image.tmdb.org/t/p/original/p96dm7sCMn4VYAStA6siNz30G1r.jpg",
                "width": 2000,
                "height": 3000,
                "aspect_ratio": 0.667,
                "language": "en",
                "source": "tmdb",
                "vote_average": 5.8,
                "vote_count": 20,
            },
        ],
        "backdrops": [
            {
                "image": "https://image.tmdb.org/t/p/w500/ncEsesgOJDNrTUED89hYbA117wo.jpg",
                "original": "https://image.tmdb.org/t/p/original/ncEsesgOJDNrTUED89hYbA117wo.jpg",
                "width": 3840,
                "height": 2160,
                "aspect_ratio": 1.778,
                "language": None,
                "source": "tmdb",
                "vote_average": 5.6,
                "vote_count": 12,
            },
        ],
        "screenshots": [],
        "logos": [],
    },
)

COVER_EXAMPLE = _example(
    "Cover",
    {
        "image": "https://image.tmdb.org/t/p/original/p96dm7sCMn4VYAStA6siNz30G1r.jpg",
        "custom_image": "https://image.tmdb.org/t/p/original/p96dm7sCMn4VYAStA6siNz30G1r.jpg",
    },
)

SEARCH_PEOPLE_EXAMPLE = _example(
    "Search people",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "person_id": 6384,
                "source": "tmdb",
                "name": "Keanu Reeves",
                "image": "https://image.tmdb.org/t/p/w500/4D0PpNI0kmP58hgrwGC3wCjxhnm.jpg",
                "known_for_department": "Acting",
                "known_for": [
                    {
                        "source": "tmdb",
                        "media_type": "movie",
                        "media_id": "603",
                        "title": "The Matrix",
                        "image": "https://image.tmdb.org/t/p/w500/p96dm7sCMn4VYAStA6siNz30G1r.jpg",
                    },
                ],
            },
        ],
    },
)

PERSON_EXAMPLE = _example(
    "Person",
    {
        "person_id": 6384,
        "source": "tmdb",
        "source_url": "https://www.themoviedb.org/person/6384",
        "name": "Keanu Reeves",
        "image": "https://image.tmdb.org/t/p/w500/4D0PpNI0kmP58hgrwGC3wCjxhnm.jpg",
        "biography": "Keanu Charles Reeves is a Canadian actor.",
        "birthday": "1964-09-02",
        "deathday": None,
        "place_of_birth": "Beirut, Lebanon",
        "known_for_department": "Acting",
        "also_known_as": ["Keanu Charles Reeves"],
        "homepage": None,
        "external_links": {
            "IMDb": "https://www.imdb.com/name/nm0000206/",
            "Wikidata": "https://www.wikidata.org/wiki/Q43416",
        },
    },
)

PERSON_IMAGES_EXAMPLE = _example(
    "Person images",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "image": "https://image.tmdb.org/t/p/w500/4D0PpNI0kmP58hgrwGC3wCjxhnm.jpg",
                "original": "https://image.tmdb.org/t/p/original/4D0PpNI0kmP58hgrwGC3wCjxhnm.jpg",
                "width": 1000,
                "height": 1500,
                "aspect_ratio": 0.667,
                "vote_average": 5.522,
                "vote_count": 4,
            },
        ],
    },
)

KNOWN_FOR_EXAMPLE = _example(
    "Known for",
    {
        "pagination": {
            "total": 1,
            "limit": 20,
            "offset": 0,
            "next": None,
            "previous": None,
        },
        "results": [
            {
                "source": "tmdb",
                "media_type": "movie",
                "media_id": "603",
                "title": "The Matrix",
                "image": "https://image.tmdb.org/t/p/w500/p96dm7sCMn4VYAStA6siNz30G1r.jpg",
                "character": "Thomas A. Anderson / Neo",
                "departments": ["Acting"],
                "jobs": [],
                "release_date": "1999-03-31",
                "episode_count": None,
            },
        ],
    },
)

TRACKED_SEASON_EXAMPLE = _example(
    "Tracked season",
    {
        "id": 3600,
        "consumption_id": 323,
        "item": {
            "media_id": "12345",
            "source": "tmdb",
            "media_type": "season",
            "title": "Van der Valk",
            "image": "https://image.tmdb.org/t/p/w500/7cSyT7qrAVHOdiaUqcZAdj4Jny1.jpg",
            "season_number": 1,
            "episode_number": None,
        },
        "item_id": "season/tmdb/12345/1",
        "parent_id": "tv/tmdb/12345",
        "tracked": True,
        "created_at": "2026-05-11T17:20:38.252187Z",
        "score": None,
        "status": 0,
        "progress": 0,
        "progressed_at": None,
        "start_date": None,
        "end_date": None,
        "notes": "",
        "lists": [],
    },
)

TRACKED_EPISODE_EXAMPLE = _example(
    "Tracked episode",
    {
        "id": 3601,
        "consumption_id": 324,
        "item": {
            "media_id": "12345",
            "source": "tmdb",
            "media_type": "episode",
            "title": "Van der Valk",
            "image": "https://image.tmdb.org/t/p/w500/7cSyT7qrAVHOdiaUqcZAdj4Jny1.jpg",
            "season_number": 1,
            "episode_number": 1,
        },
        "item_id": "episode/tmdb/12345/1/1",
        "parent_id": "season/tmdb/12345/1",
        "tracked": True,
        "created_at": "2026-05-11T17:20:38.252187Z",
        "score": None,
        "status": 3,
        "progress": 1,
        "progressed_at": "2026-05-11T00:00:00Z",
        "start_date": "2026-05-11T17:20:38.252187Z",
        "end_date": "2026-05-11T00:00:00Z",
        "notes": None,
        "lists": [],
    },
)
