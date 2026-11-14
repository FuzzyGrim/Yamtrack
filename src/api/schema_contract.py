import ast
import re
from collections.abc import Iterable
from functools import wraps
from typing import Any, NamedTuple

from floppy_mcp.http_manifest import MCP_HTTP_MANIFEST

from api.contract_serializers import DetailErrorSerializer, InfoResponseSerializer

SCHEMA_REGENERATION_COMMAND = (
    "SECRET=test-only uv run --no-sync python src/manage.py spectacular "
    "--custom-settings api.schema_contract.STATIC_SPECTACULAR_SETTINGS "
    "--fail-on-warn --validate --file src/api/contracts/openapi.yaml"
)

VERIFIED_OPERATIONS = frozenset(
    {(route.path, route.method.upper()) for route in MCP_HTTP_MANIFEST}
    | {
        ("/api/v1/info/", "GET"),
        ("/apis/listenbrainz/1/submit-listens/", "POST"),
        ("/apis/listenbrainz/1/user/{username}/listens/", "GET"),
        ("/apis/listenbrainz/1/stats/user/{username}/artists/", "GET"),
        ("/apis/listenbrainz/1/stats/user/{username}/release-groups/", "GET"),
        ("/apis/listenbrainz/1/stats/user/{username}/recordings/", "GET"),
        ("/apis/listenbrainz/1/stats/user/{username}/genre-activity/", "GET"),
        ("/apis/listenbrainz/1/validate-token/", "GET"),
        ("/api/v1/sync/bootstrap/", "POST"),
        ("/api/v1/sync/snapshot/", "GET"),
        ("/api/v1/sync/changes/", "GET"),
        ("/api/v1/sync/progress-changes/", "GET"),
        ("/api/v1/media/{media_type}/{source}/{media_id}/detail/", "GET"),
        ("/api/v1/media/{media_type}/{source}/{media_id}/detail/gallery/", "GET"),
    }
)

INTEGRATION_GUIDE = """\
Verified consumer subset covering MCP HTTP operations, public grounding \
information, and ListenBrainz-compatible authentication.

## Start here

- This document is the reviewed contract: `/api/openapi.yaml` (YAML). Browsable \
index: `/api/docs/`.
- `/api/schema/` is the full, unreviewed diagnostic schema (about 200 KB). \
It is YAML by default; request JSON with `?format=json` or \
`Accept: application/json`, or YAML with `?format=yaml`. Other `format` \
values fall back to Accept negotiation.
- Authenticate with `Authorization: Bearer <token>` or `X-API-Key: <token>`.

## Vocabulary

- `media_type`: the kind of media (`movie`, `tv`, `game`, ...). Path segment.
- `source`: the provider that owns `media_id` (`tmdb`, `igdb`, `manual`, ...). \
It is called `source` everywhere in this API, including search; there is no \
`provider` field on the wire.
- `media_id`: the provider's id for the item, always a string in paths.
- Statuses are written as an integer code (0 Planning, 1 In progress, \
2 Paused, 3 Completed, 4 Dropped) or its case-insensitive label. Comic issues \
also accept `null` or an empty string for no status.
- `start_date` and `end_date` take an ISO 8601 date or date-time. A date \
alone means midnight, and a value without an offset is read in the server's \
active timezone, so send an offset (`2026-03-01T20:00:00Z`) to be exact.

## Golden path: add a play to a game

1. `GET /api/v1/search/{media_type}/?search=...` (optionally `&source=...`) \
and take `source` and `media_id` from the chosen result.
2. `GET /api/v1/media/{media_type}/{source}/{media_id}/state/`. `tracked: false` \
with `consumptions: []` means the item is real but you have not tracked it. \
Each entry in `consumptions` has its own `consumption_id`.
3. `POST /api/v1/media/{media_type}/` with `source` and `media_id` appends a \
new consumption; it never overwrites an existing one. Omitted `status` means \
Planning.
4. `PATCH /api/v1/media/{media_type}/{source}/{media_id}/history/{consumption_id}/` \
changes exactly one consumption. Re-read `state` to verify.

## Detail projection

`GET /api/v1/media/{media_type}/{source}/{media_id}/detail/` returns serializable
title, artwork, facts, synopsis, sections and viewer tracking capabilities for
shared web and native detail surfaces, including score/tag chips, links,
providers, cast/crew, related/recommendation cards, seasons, paginated episodes,
book formats, game lengths, game YouTube activity, viewer notes, recent
history and collection details when those builders return data. It contains no
server-rendered HTML. Anonymous callers receive public, read-only metadata;
`?public_view=1` suppresses viewer state and actions. Returned action
capabilities are authoritative; child watch/rating actions are limited to
signed-in, non-public TV detail views.
The `can_request_seerr` capability reports whether a private TMDB movie or TV
detail has a configured Seerr connection; it does not contact Seerr or expose
connection credentials. Clients fetch the current request state on demand.
The companion `GET /api/v1/media/{media_type}/{source}/{media_id}/detail/gallery/`
resolves the deferred title logo, backdrop, gallery photos and trailer.

## Errors

Error bodies keep their `detail` text and HTTP status. Where recovery differs \
they add a stable `code`:

| `code` | Status | Meaning |
| --- | --- | --- |
| `MEDIA_NOT_FOUND` | 404 | No such item (the provider does not know the id, \
or a manual item does not exist). Do not retry. |
| `MEDIA_NOT_TRACKED` | 404 | The item may exist but you have not tracked it; \
track it first. |
| `CONSUMPTION_NOT_FOUND` | 404 | `consumption_id` is not one of this item's \
entries; re-read `state`. |
| `INVALID_STATUS` | 400 | `status` is not a code 0-4 or a known label (comic issues also allow no status). |
| `PROVIDER_UNAVAILABLE` | 503 | The metadata provider failed; \
`retryable: true`. |
| `DATABASE_BUSY` | 503 | The database was locked; retry GET requests after \
`Retry-After`. |

Responses without a `code` (for example other 400 validation errors) are \
described by `detail` only.
"""

STATIC_SPECTACULAR_SETTINGS = {
    "TITLE": "Floppy verified MCP and grounding API",
    "DESCRIPTION": INTEGRATION_GUIDE,
    "VERSION": "1.0.0",
    "COMPONENT_SPLIT_PATCH": False,
    "SERVERS": [{"url": "../"}],
    "PREPROCESSING_HOOKS": ["api.schema_contract.filter_verified_endpoints"],
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "api.schema_contract.label_verified_schema",
    ],
}

_OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}
_ARRAY_SCHEMA = {"type": "array", "items": _OBJECT_SCHEMA}
_TRACKED_MEDIA_REF = {"$ref": "#/components/schemas/TrackedMediaResponse"}
_TRACKED_MEDIA_ENVELOPE_REF = {
    "$ref": "#/components/schemas/TrackedMediaEnvelope"
}
_TAG_SCHEMA = {
    "type": "object",
    "required": ["id", "name", "created_at"],
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "item_count": {"type": "integer"},
    },
}
_TAG_RESULTS_SCHEMA = {
    "type": "object",
    "required": ["results"],
    "properties": {"results": {"type": "array", "items": _TAG_SCHEMA}},
}
_PAGINATED_SCHEMA = {
    "type": "object",
    "required": ["pagination", "results"],
    "properties": {
        "pagination": {
            "type": "object",
            "required": ["total", "limit", "offset", "next", "previous"],
            "properties": {
                "total": {"type": "integer"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "next": {"type": "string", "nullable": True},
                "previous": {"type": "string", "nullable": True},
            },
        },
        "results": _ARRAY_SCHEMA,
    },
}
_LIST_SCHEMA = {
    "type": "object",
    "required": [
        "id",
        "name",
        "description",
        "image",
        "owner",
        "collaborators",
        "viewers",
        "items_count",
        "latest_update",
        "visibility",
        "is_public",
        "is_unlisted",
        "public_slug",
        "allow_recommendations",
    ],
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "image": {"type": "string", "nullable": True},
        "owner": {"type": "object", "additionalProperties": True},
        "collaborators": _ARRAY_SCHEMA,
        "viewers": _ARRAY_SCHEMA,
        "items_count": {"type": "integer"},
        "latest_update": {
            "type": "string",
            "format": "date-time",
            "nullable": True,
        },
        "visibility": {"type": "string", "enum": ["public", "unlisted", "private"]},
        "is_public": {"type": "boolean"},
        "is_unlisted": {"type": "boolean"},
        "public_slug": {"type": "string", "nullable": True},
        "allow_recommendations": {"type": "boolean"},
        "is_smart": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "include_notes": {"type": "boolean"},
        "completed_count": {"type": "integer"},
        "completion_percent": {"type": "integer", "nullable": True},
        "media_type_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "label": {"type": "string"},
                    "count": {"type": "integer"},
                    "percent": {"type": "integer"},
                    "color": {"type": "string"},
                },
            },
        },
        "items": {"oneOf": [_ARRAY_SCHEMA, _PAGINATED_SCHEMA]},
    },
}
_LIST_PAGINATED_SCHEMA = {
    **_PAGINATED_SCHEMA,
    "properties": {
        **_PAGINATED_SCHEMA["properties"],
        "results": {"type": "array", "items": _LIST_SCHEMA},
    },
}
_MINIMIZED_LIST_ARRAY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["list_id", "list_item_id"],
        "properties": {
            "list_id": {"type": "integer"},
            "list_item_id": {"type": "integer", "nullable": True},
        },
    },
}

_STATIC_OPERATION_OVERRIDES = {
    ("/api/v1/info/", "GET"): {
        "operation_id": "getPublicInfo",
        "responses": {200: InfoResponseSerializer},
    },
    ("/api/v1/lists/", "GET"): {"responses": {200: _LIST_PAGINATED_SCHEMA}},
    ("/api/v1/lists/", "POST"): {"responses": {201: _LIST_SCHEMA}},
    ("/api/v1/lists/{list_id}/", "PATCH"): {"responses": {200: _LIST_SCHEMA}},
    ("/api/v1/lists/{list_id}/", "DELETE"): {"responses": {204: None}},
    ("/api/v1/lists/{list_id}/items/", "GET"): {
        "responses": {200: _TRACKED_MEDIA_ENVELOPE_REF}
    },
    ("/api/v1/media/{media_type}/{source}/{media_id}/lists/{list_id}/", "PUT"): {
        "responses": {200: _MINIMIZED_LIST_ARRAY_SCHEMA}
    },
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/lists/{list_id}/",
        "DELETE",
    ): {"responses": {204: None}},
    ("/api/v1/media/{media_type}/{source}/{media_id}/tags/", "GET"): {
        "responses": {200: _TAG_RESULTS_SCHEMA}
    },
    ("/api/v1/media/{media_type}/{source}/{media_id}/tags/", "PUT"): {
        "responses": {200: _TAG_RESULTS_SCHEMA}
    },
    ("/api/v1/history/", "GET"): {"responses": {200: _PAGINATED_SCHEMA}},
    ("/api/v1/tags/", "GET"): {"responses": {200: _TAG_RESULTS_SCHEMA}},
    ("/api/v1/tags/", "POST"): {"responses": {201: _TAG_SCHEMA}},
    ("/api/v1/tags/{tag_id}/", "PATCH"): {"responses": {200: _TAG_SCHEMA}},
    ("/api/v1/tags/{tag_id}/", "DELETE"): {"responses": {204: None}},
    ("/api/v1/tasks/{task_id}/", "GET"): {
        "responses": {
            200: {
                "type": "object",
                "required": ["task_id", "status"],
                "properties": {
                    "task_id": {"type": "string"},
                    "task_name": {"type": "string", "nullable": True},
                    "status": {"type": "string"},
                    "date_created": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                    },
                    "date_done": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                    },
                    "result": {"type": "string", "nullable": True},
                },
            }
        }
    },
    ("/api/v1/discover/", "GET"): {
        "responses": {
            200: {
                "type": "object",
                "required": ["media_type", "show_more", "rows"],
                "properties": {
                    "media_type": {"type": "string"},
                    "show_more": {"type": "boolean"},
                    "rows": _ARRAY_SCHEMA,
                },
            }
        }
    },
    ("/api/v1/home/", "GET"): {
        "responses": {
            200: {
                "type": "object",
                "required": ["groups"],
                "properties": {"groups": _ARRAY_SCHEMA},
            }
        }
    },
    ("/api/v1/user/preferences/", "GET"): {
        "responses": {
            200: {
                "type": "object",
                "required": ["preferences", "choices"],
                "properties": {
                    "preferences": _OBJECT_SCHEMA,
                    "choices": _OBJECT_SCHEMA,
                },
            }
        }
    },
    ("/api/v1/user/preferences/", "PATCH"): {
        "responses": {
            200: {
                "type": "object",
                "required": ["preferences", "updated_fields"],
                "properties": {
                    "preferences": _OBJECT_SCHEMA,
                    "updated_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            }
        }
    },
    ("/api/v1/imports/{service}/", "POST"): {
        "responses": {
            202: {
                "type": "object",
                "required": ["task_id"],
                "properties": {"task_id": {"type": "string"}},
            }
        }
    },
    ("/api/v1/statistics/overview/", "GET"): {
        "responses": {
            200: {
                "type": "object",
                "required": ["range", "statistics"],
                "properties": {
                    "range": _OBJECT_SCHEMA,
                    "statistics": _OBJECT_SCHEMA,
                },
            }
        }
    },
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/progress/",
        "POST",
    ): {"responses": {200: _TRACKED_MEDIA_REF}},
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/progress/",
        "POST",
    ): {"responses": {200: _TRACKED_MEDIA_REF}},
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
        "episodes/{episode_number}/watch/",
        "POST",
    ): {
        "responses": {
            201: _TRACKED_MEDIA_REF,
            404: DetailErrorSerializer,
        }
    },
    ("/api/v1/music/songs/plays/", "POST"): {
        "responses": {201: _TRACKED_MEDIA_REF}
    },
    ("/api/v1/podcasts/episodes/plays/", "POST"): {
        "responses": {200: _TRACKED_MEDIA_REF, 201: _TRACKED_MEDIA_REF}
    },
}

_STATIC_OPERATION_IDS = {
    ("/api/v1/media/{media_type}/{source}/{media_id}/", "DELETE"): "deleteMedia",
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/",
        "DELETE",
    ): "deleteMediaSeason",
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
        "{episode_number}/",
        "DELETE",
    ): "deleteMediaEpisode",
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/progress/",
        "POST",
    ): "updateMediaProgress",
    (
        "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/progress/",
        "POST",
    ): "updateMediaSeasonProgress",
}


def _annotate_static_callback(endpoint):
    """Clone one callback so static-only overrides cannot alter `/api/schema/`."""
    from drf_spectacular.utils import extend_schema

    path, path_regex, method, callback = endpoint
    overrides = dict(_STATIC_OPERATION_OVERRIDES.get((path, method), {}))
    if operation_id := _STATIC_OPERATION_IDS.get((path, method)):
        overrides["operation_id"] = operation_id
    if not overrides:
        return endpoint
    if method not in {"GET", "DELETE"} and "request" not in overrides:
        overrides["request"] = {"application/json": _OBJECT_SCHEMA}

    original = getattr(callback.cls, method.lower())

    @wraps(original)
    def annotated(*args, **kwargs):
        return original(*args, **kwargs)

    if hasattr(annotated, "kwargs"):
        annotated.kwargs = dict(annotated.kwargs)
    scoped_method = extend_schema(**overrides)(annotated)
    scoped_class = type(
        f"Static{callback.cls.__name__}",
        (callback.cls,),
        {"__module__": callback.cls.__module__, method.lower(): scoped_method},
    )
    return path, path_regex, method, scoped_class.as_view(**callback.initkwargs)


def filter_verified_endpoints(endpoints):
    """Keep only the manifest-owned and explicit grounding operations."""
    return [
        _annotate_static_callback(endpoint)
        for endpoint in endpoints
        if (endpoint[0], endpoint[2].upper()) in VERIFIED_OPERATIONS
    ]


def label_verified_schema(result, **_kwargs):
    """Make the committed artifact's deliberately limited scope machine-readable."""
    result["x-floppy-scope"] = "verified-mcp-and-grounding-subset"
    return result

type SchemaFinding = tuple[str, str]

_SOURCE_FINDING = re.compile(
    r"(?:^|[/\\])src[/\\](?P<module>.+?)\.py(?::\d+)?: "
    r"(?:Error|Warning) \[(?P<breadcrumbs>[^]]+)\]: (?P<detail>.*)$"
)
_OPERATION_ID_COLLISION = re.compile(
    r'operationId "(?P<operation_id>[^"]+)" has collisions '
    r"(?P<members>\[[^]]*\])"
)
_UNRESOLVED_AUTHENTICATOR = re.compile(
    r"could not resolve authenticator <class '(?P<authenticator>[^']+)'>"
)
_COLLISION_MEMBER_SIZE = 2


EXPECTED_SCHEMA_ERRORS: frozenset[SchemaFinding] = frozenset(
    {
        ("api.fork_views.CollectionEntryView", "serializer-unresolved"),
        ("api.fork_views.CollectionView", "serializer-unresolved"),
        ("api.fork_views.MediaProgressView", "serializer-unresolved"),
        ("api.fork_views.TaskStatusView", "serializer-unresolved"),
        ("api.fork_views_discover.CollectionSeasonView", "serializer-unresolved"),
        ("api.fork_views_discover.CollectionStatusView", "serializer-unresolved"),
        ("api.fork_views_discover.DiscoverHiddenView", "serializer-unresolved"),
        ("api.fork_views_discover.DiscoverRefreshView", "serializer-unresolved"),
        ("api.fork_views_discover.DiscoverRowsView", "serializer-unresolved"),
        ("api.fork_views_discover.HomeArtworkView", "serializer-unresolved"),
        ("api.fork_views_discover.HomeRowDirectionView", "serializer-unresolved"),
        ("api.fork_views_discover.HomeRowView", "serializer-unresolved"),
        ("api.fork_views_discover.HomeView", "serializer-unresolved"),
        ("api.fork_views_integrations.ExportCsvView", "serializer-unresolved"),
        ("api.fork_views_integrations.ExportTemplateView", "serializer-unresolved"),
        ("api.fork_views_integrations.ImportDispatchView", "serializer-unresolved"),
        ("api.fork_views_lists.ListActivityView", "serializer-unresolved"),
        ("api.fork_views_lists.ListCollaboratorsView", "serializer-unresolved"),
        (
            "api.fork_views_lists.ListRecommendationDecisionView",
            "serializer-unresolved",
        ),
        ("api.fork_views_lists.ListRecommendationsView", "serializer-unresolved"),
        ("api.fork_views_lists.ListSmartRulesView", "serializer-unresolved"),
        ("api.fork_views_lists.ListSmartSyncView", "serializer-unresolved"),
        ("api.fork_views_metadata.ItemImageView", "serializer-unresolved"),
        ("api.fork_views_metadata.ItemMetadataView", "serializer-unresolved"),
        ("api.fork_views_metadata.MediaEpisodeScoreView", "serializer-unresolved"),
        (
            "api.fork_views_metadata.MediaProviderPreferenceView",
            "serializer-unresolved",
        ),
        ("api.fork_views_music.MusicAlbumDetailView", "serializer-unresolved"),
        ("api.fork_views_music.MusicAlbumPlaysView", "serializer-unresolved"),
        ("api.fork_views_music.MusicAlbumTracksView", "serializer-unresolved"),
        ("api.fork_views_music.MusicAlbumsView", "serializer-unresolved"),
        ("api.fork_views_music.MusicArtistDetailView", "serializer-unresolved"),
        ("api.fork_views_music.MusicArtistPlaysView", "serializer-unresolved"),
        ("api.fork_views_music.MusicArtistSyncView", "serializer-unresolved"),
        ("api.fork_views_music.MusicArtistsView", "serializer-unresolved"),
        ("api.fork_views_music.MusicBulkPlaysView", "serializer-unresolved"),
        ("api.fork_views_music.MusicSongPlayView", "serializer-unresolved"),
        ("api.fork_views_music.MusicTrackScoreView", "serializer-unresolved"),
        ("api.fork_views_podcast.PodcastEpisodePlayView", "serializer-unresolved"),
        ("api.fork_views_podcast.PodcastLookupView", "serializer-unresolved"),
        (
            "api.fork_views_podcast.PodcastMarkAllPlayedView",
            "serializer-unresolved",
        ),
        ("api.fork_views_podcast.PodcastShowDetailView", "serializer-unresolved"),
        (
            "api.fork_views_podcast.PodcastShowEpisodesView",
            "serializer-unresolved",
        ),
        ("api.fork_views_podcast.PodcastShowsView", "serializer-unresolved"),
        (
            "api.fork_views_statistics.StatisticsRefreshView",
            "serializer-unresolved",
        ),
        (
            "api.fork_views_watched_state.SyncConflictResolveView",
            "serializer-unresolved",
        ),
        ("api.fork_views_watched_state.SyncConflictsView", "serializer-unresolved"),
        ("api.fork_views_watched_state.SyncConnectionsView", "serializer-unresolved"),
        ("api.fork_views_watched_state.WatchedStateView", "serializer-unresolved"),
        ("api.fork_views_tracking.HistoryRecordView", "serializer-unresolved"),
        ("api.fork_views_tracking.HistoryView", "serializer-unresolved"),
        ("api.fork_views_tracking.MediaEpisodeBulkView", "serializer-unresolved"),
        ("api.fork_views_tracking.MediaEpisodeDropView", "serializer-unresolved"),
        ("api.fork_views_tracking.MediaEpisodeWatchView", "serializer-unresolved"),
        ("api.fork_views_tracking.MediaMovieWatchView", "serializer-unresolved"),
        ("api.fork_views_tracking.MediaTagsView", "serializer-unresolved"),
        ("api.fork_views_tracking.TagDetailView", "serializer-unresolved"),
        ("api.fork_views_tracking.TagsView", "serializer-unresolved"),
        (
            "api.fork_views_users.UserNotificationExclusionsView",
            "serializer-unresolved",
        ),
        (
            "api.fork_views_users.UserNotificationTestView",
            "serializer-unresolved",
        ),
        ("api.fork_views_users.UserNotificationsView", "serializer-unresolved"),
        ("api.fork_views_users.UserPreferencesView", "serializer-unresolved"),
        ("api.fork_views_users.UserSidebarView", "serializer-unresolved"),
        ("api.fork_views_users.UserTokenRegenerateView", "serializer-unresolved"),
        ("api.views.CalendarUpdateView", "serializer-unresolved"),
        ("api.views.CalendarView", "serializer-unresolved"),
        ("api.views.HealthView", "serializer-unresolved"),
        ("api.views.InfoView", "serializer-unresolved"),
        ("api.views.ListDetailView", "serializer-unresolved"),
        ("api.views.ListItemView", "serializer-unresolved"),
        ("api.views.ListsView", "serializer-unresolved"),
        ("api.views.MediaChangesHistoryView", "serializer-unresolved"),
        ("api.views.MediaEpisodeListDetailView", "serializer-unresolved"),
        ("api.views.MediaEpisodeListsView", "serializer-unresolved"),
        ("api.views.MediaEpisodeSyncView", "serializer-unresolved"),
        ("api.views.MediaListDetailView", "serializer-unresolved"),
        ("api.views.MediaListsView", "serializer-unresolved"),
        ("api.views.MediaSeasonChangesHistoryView", "serializer-unresolved"),
        ("api.views.MediaSeasonListDetailView", "serializer-unresolved"),
        ("api.views.MediaSeasonListsView", "serializer-unresolved"),
        ("api.views.MediaSeasonSyncView", "serializer-unresolved"),
        ("api.views.MediaSeasonsView", "serializer-unresolved"),
        ("api.views.MediaSyncView", "serializer-unresolved"),
        (
            "api.views.MediaTypeChangesHistoryDetailView",
            "serializer-unresolved",
        ),
        ("api.views.StatisticsView", "serializer-unresolved"),
    }
)

_EXPECTED_OPERATION_ID_COLLISIONS = (
    (
        "api_v1_collection_retrieve",
        (
            ("/api/v1/collection/", "get"),
            ("/api/v1/collection/{entry_id}/", "get"),
        ),
    ),
    (
        "api_v1_lists_items_retrieve",
        (
            ("/api/v1/lists/{list_id}/items/", "get"),
            ("/api/v1/lists/{list_id}/items/{item_id}/", "get"),
        ),
    ),
    (
        "api_v1_lists_recommendations_create",
        (
            ("/api/v1/lists/{list_id}/recommendations/", "post"),
            (
                "/api/v1/lists/{list_id}/recommendations/"
                "{recommendation_id}/{decision}/",
                "post",
            ),
        ),
    ),
    (
        "api_v1_lists_retrieve",
        (
            ("/api/v1/lists/", "get"),
            ("/api/v1/lists/{list_id}/", "get"),
        ),
    ),
    (
        "api_v1_media_changes_history_retrieve",
        (
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/changes_history/",
                "get",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/"
                "{season_number}/changes_history/",
                "get",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/"
                "{season_number}/{episode_number}/changes_history/",
                "get",
            ),
        ),
    ),
    (
        "api_v1_media_destroy",
        (
            ("/api/v1/media/{media_type}/{source}/{media_id}/", "delete"),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/",
                "delete",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/"
                "{season_number}/{episode_number}/",
                "delete",
            ),
        ),
    ),
    (
        "api_v1_media_history_destroy",
        (
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/history/"
                "{consumption_id}/",
                "delete",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "history/{consumption_id}/",
                "delete",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/history/{consumption_id}/",
                "delete",
            ),
        ),
    ),
    (
        "api_v1_media_history_partial_update",
        (
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "history/{consumption_id}/",
                "patch",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/history/{consumption_id}/",
                "patch",
            ),
        ),
    ),
    (
        "api_v1_media_history_retrieve",
        (
            ("/api/v1/media/{media_type}/{source}/{media_id}/history/", "get"),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/history/"
                "{consumption_id}/",
                "get",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "history/",
                "get",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "history/{consumption_id}/",
                "get",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/history/",
                "get",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/history/{consumption_id}/",
                "get",
            ),
        ),
    ),
    (
        "api_v1_media_lists_destroy",
        (
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/lists/{list_id}/",
                "delete",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "lists/{list_id}/",
                "delete",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/lists/{list_id}/",
                "delete",
            ),
        ),
    ),
    (
        "api_v1_media_lists_retrieve",
        (
            ("/api/v1/media/{media_type}/{source}/{media_id}/lists/", "get"),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "lists/",
                "get",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/lists/",
                "get",
            ),
        ),
    ),
    (
        "api_v1_media_lists_update",
        (
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/lists/{list_id}/",
                "put",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "lists/{list_id}/",
                "put",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/lists/{list_id}/",
                "put",
            ),
        ),
    ),
    (
        "api_v1_media_partial_update",
        (
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/",
                "patch",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/"
                "{season_number}/{episode_number}/",
                "patch",
            ),
        ),
    ),
    (
        "api_v1_media_progress_create",
        (
            ("/api/v1/media/{media_type}/{source}/{media_id}/progress/", "post"),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "progress/",
                "post",
            ),
        ),
    ),
    (
        "api_v1_media_sync_create",
        (
            ("/api/v1/media/{media_type}/{source}/{media_id}/sync/", "post"),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/sync/",
                "post",
            ),
            (
                "/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/"
                "{episode_number}/sync/",
                "post",
            ),
        ),
    ),
    (
        "api_v1_music_albums_retrieve",
        (
            ("/api/v1/music/albums/", "get"),
            ("/api/v1/music/albums/{album_id}/", "get"),
        ),
    ),
    (
        "api_v1_music_artists_retrieve",
        (
            ("/api/v1/music/artists/", "get"),
            ("/api/v1/music/artists/{artist_id}/", "get"),
        ),
    ),
    (
        "api_v1_podcasts_shows_retrieve",
        (
            ("/api/v1/podcasts/shows/", "get"),
            ("/api/v1/podcasts/shows/{show_id}/", "get"),
        ),
    ),
)

EXPECTED_SCHEMA_WARNINGS: frozenset[SchemaFinding] = frozenset(
    {
        (f"operation-id.{operation_id}{members!r}", "operation-id-collision")
        for operation_id, members in _EXPECTED_OPERATION_ID_COLLISIONS
    }
)


class SchemaContract(NamedTuple):
    """Generated OpenAPI schema and its normalized findings."""

    schema: dict[str, Any]
    errors: frozenset[SchemaFinding]
    warnings: frozenset[SchemaFinding]


def normalize_schema_finding(message: str) -> SchemaFinding:
    """Reduce one rendered spectacular finding to a stable owner and category."""
    if match := _SOURCE_FINDING.search(message):
        module = match["module"].replace("/", ".").replace("\\", ".")
        breadcrumbs = match["breadcrumbs"].replace(" > ", ".")
        detail = match["detail"]
        if "unable to guess serializer" in detail:
            return f"{module}.{breadcrumbs}", "serializer-unresolved"
        if match := _UNRESOLVED_AUTHENTICATOR.search(detail):
            owner = f"{module}.{breadcrumbs}[authenticator={match['authenticator']}]"
            return owner, "authentication-unresolved"
        return f"{module}.{breadcrumbs}", "unclassified"

    if match := _OPERATION_ID_COLLISION.search(message):
        try:
            members = ast.literal_eval(match["members"])
        except (SyntaxError, ValueError):
            return f"operation-id.{match['operation_id']}", "unclassified"
        if not isinstance(members, (list, tuple)) or not all(
            isinstance(member, (list, tuple))
            and len(member) == _COLLISION_MEMBER_SIZE
            and all(isinstance(value, str) for value in member)
            for member in members
        ):
            return f"operation-id.{match['operation_id']}", "unclassified"
        canonical_members = tuple(
            sorted({(path, method.lower()) for path, method in members})
        )
        return (
            f"operation-id.{match['operation_id']}{canonical_members!r}",
            "operation-id-collision",
        )

    return "schema", "unclassified"


def normalize_schema_findings(findings: Iterable[str]) -> frozenset[SchemaFinding]:
    """Normalize unique rendered finding keys, ignoring cache repeat counts."""
    return frozenset(normalize_schema_finding(message) for message in findings)


def generate_schema_contract() -> SchemaContract:
    """Generate OpenAPI in memory and return its normalized unique findings."""
    from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
    from drf_spectacular.generators import SchemaGenerator

    reset_generator_stats()
    with GENERATOR_STATS.silence():
        schema = SchemaGenerator().get_schema(public=True)
    return SchemaContract(
        schema=schema,
        errors=normalize_schema_findings(GENERATOR_STATS._error_cache),
        warnings=normalize_schema_findings(GENERATOR_STATS._warn_cache),
    )


def generate_static_schema_contract() -> SchemaContract:
    """Generate the filtered, consumer-facing contract in memory."""
    from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
    from drf_spectacular.generators import SchemaGenerator
    from drf_spectacular.settings import patched_settings

    reset_generator_stats()
    with patched_settings(STATIC_SPECTACULAR_SETTINGS), GENERATOR_STATS.silence():
        schema = SchemaGenerator().get_schema(public=True)
    return SchemaContract(
        schema=schema,
        errors=normalize_schema_findings(GENERATOR_STATS._error_cache),
        warnings=normalize_schema_findings(GENERATOR_STATS._warn_cache),
    )


def assert_schema_findings(
    errors: Iterable[SchemaFinding], warnings: Iterable[SchemaFinding]
) -> None:
    """Fail with stable additions/removals when schema findings drift."""
    differences = []
    for label, expected, actual in (
        ("errors", EXPECTED_SCHEMA_ERRORS, frozenset(errors)),
        ("warnings", EXPECTED_SCHEMA_WARNINGS, frozenset(warnings)),
    ):
        differences.extend(f"{label} + {finding!r}" for finding in sorted(actual - expected))
        differences.extend(f"{label} - {finding!r}" for finding in sorted(expected - actual))

    if differences:
        details = "\n".join(differences)
        msg = (
            f"OpenAPI schema findings changed:\n{details}\n"
            f"Regenerate with: {SCHEMA_REGENERATION_COMMAND}"
        )
        raise AssertionError(msg)
