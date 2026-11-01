from dataclasses import dataclass, field


@dataclass(frozen=True)
class EndpointCase:
    """Represents a single API endpoint."""

    method: str
    url_name: str
    args: tuple = field(default_factory=tuple)
    payload: dict | None = None
    is_public: bool = False


def get_endpoint_cases() -> list[EndpointCase]:
    """Return a list of every endpoint and supported method."""
    return [
        EndpointCase("get", "api_calendar"),
        EndpointCase("post", "api_update_calendar"),
        EndpointCase(
            "get",
            "api_media_changes_history_detail",
            args=("movie", "1"),
        ),
        EndpointCase(
            "delete",
            "api_media_changes_history_detail",
            args=("movie", "1"),
        ),
        EndpointCase("get", "api_health", is_public=True),
        EndpointCase("get", "api_info", is_public=True),
        EndpointCase("get", "api_lists"),
        EndpointCase("post", "api_lists", payload={"name": "New list"}),
        EndpointCase("get", "api_list_detail", args=(1,)),
        EndpointCase(
            "patch",
            "api_list_detail",
            args=(1,),
            payload={"name": "Renamed"},
        ),
        EndpointCase("delete", "api_list_detail", args=(1,)),
        EndpointCase("get", "api_list_add_item", args=(1,)),
        EndpointCase(
            "post",
            "api_list_add_item",
            args=(1,),
            payload={"item_id": "movie/tmdb/1"},
        ),
        EndpointCase("get", "api_list_remove_item", args=(1, 1)),
        EndpointCase("delete", "api_list_remove_item", args=(1, 1)),
        EndpointCase("get", "api_media_list"),
        EndpointCase("get", "api_media_type_list", args=("movie",)),
        EndpointCase(
            "post",
            "api_media_type_list",
            args=("movie",),
            payload={"source": "manual", "title": "Manual movie"},
        ),
        EndpointCase("get", "api_media_detail", args=("movie", "tmdb", 1)),
        EndpointCase(
            "patch",
            "api_media_detail",
            args=("movie", "tmdb", 1),
            payload={"notes": "n"},
        ),
        EndpointCase("delete", "api_media_detail", args=("movie", "tmdb", 1)),
        EndpointCase(
            "get",
            "api_media_changes_history",
            args=("movie", "tmdb", 1),
        ),
        EndpointCase(
            "get",
            "api_media_consumption_history",
            args=("movie", "tmdb", 1),
        ),
        EndpointCase(
            "post",
            "api_media_consumption_history",
            args=("movie", "tmdb", 1),
            payload={"progress": 1},
        ),
        EndpointCase(
            "get",
            "api_media_consumption_entry_detail",
            args=("movie", "tmdb", 1, 1),
        ),
        EndpointCase(
            "patch",
            "api_media_consumption_entry_detail",
            args=("movie", "tmdb", 1, 1),
            payload={"notes": "entry"},
        ),
        EndpointCase(
            "delete",
            "api_media_consumption_entry_detail",
            args=("movie", "tmdb", 1, 1),
        ),
        EndpointCase("get", "api_media_lists", args=("movie", "tmdb", 1)),
        EndpointCase(
            "put",
            "api_media_list_detail",
            args=("movie", "tmdb", 1, 1),
            payload={},
        ),
        EndpointCase("delete", "api_media_list_detail", args=("movie", "tmdb", 1, 1)),
        EndpointCase(
            "get",
            "api_media_recommendations",
            args=("movie", "tmdb", 1),
        ),
        EndpointCase("get", "api_media_seasons", args=("tv", "tmdb", 1)),
        EndpointCase("post", "api_media_sync", args=("movie", "tmdb", 1)),
        EndpointCase(
            "get",
            "api_media_season_detail",
            args=("tv", "tmdb", 1, 1),
        ),
        EndpointCase(
            "patch",
            "api_media_season_detail",
            args=("tv", "tmdb", 1, 1),
            payload={"notes": "season"},
        ),
        EndpointCase(
            "delete",
            "api_media_season_detail",
            args=("tv", "tmdb", 1, 1),
        ),
        EndpointCase(
            "get",
            "api_media_season_changes_history",
            args=("tv", "tmdb", 1, 1),
        ),
        EndpointCase(
            "get",
            "api_media_season_episodes",
            args=("tv", "tmdb", 1, 1),
        ),
        EndpointCase(
            "get",
            "api_media_season_consumption_history",
            args=("tv", "tmdb", 1, 1),
        ),
        EndpointCase(
            "post",
            "api_media_season_consumption_history",
            args=("tv", "tmdb", 1, 1),
            payload={"progress": 1},
        ),
        EndpointCase(
            "get",
            "api_media_season_consumption_entry_detail",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase(
            "patch",
            "api_media_season_consumption_entry_detail",
            args=("tv", "tmdb", 1, 1, 1),
            payload={"notes": "season entry"},
        ),
        EndpointCase(
            "delete",
            "api_media_season_consumption_entry_detail",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase("get", "api_media_season_lists", args=("tv", "tmdb", 1, 1)),
        EndpointCase(
            "put",
            "api_media_season_list_detail",
            args=("tv", "tmdb", 1, 1, 1),
            payload={},
        ),
        EndpointCase(
            "delete",
            "api_media_season_list_detail",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase("post", "api_media_season_sync", args=("tv", "tmdb", 1, 1)),
        EndpointCase(
            "get",
            "api_media_episode_detail",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase(
            "patch",
            "api_media_episode_detail",
            args=("tv", "tmdb", 1, 1, 1),
            payload={"notes": "episode"},
        ),
        EndpointCase(
            "delete",
            "api_media_episode_detail",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase(
            "get",
            "api_media_episode_changes_history",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase(
            "get",
            "api_media_episode_consumption_history",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase(
            "post",
            "api_media_episode_consumption_history",
            args=("tv", "tmdb", 1, 1, 1),
            payload={"progress": 1},
        ),
        EndpointCase(
            "get",
            "api_media_episode_consumption_entry_detail",
            args=("tv", "tmdb", 1, 1, 1, 1),
        ),
        EndpointCase(
            "patch",
            "api_media_episode_consumption_entry_detail",
            args=("tv", "tmdb", 1, 1, 1, 1),
            payload={"notes": "episode entry"},
        ),
        EndpointCase(
            "delete",
            "api_media_episode_consumption_entry_detail",
            args=("tv", "tmdb", 1, 1, 1, 1),
        ),
        EndpointCase(
            "get",
            "api_media_episode_lists",
            args=("tv", "tmdb", 1, 1, 1),
        ),
        EndpointCase(
            "put",
            "api_media_episode_list_detail",
            args=("tv", "tmdb", 1, 1, 1, 1),
            payload={},
        ),
        EndpointCase(
            "delete",
            "api_media_episode_list_detail",
            args=("tv", "tmdb", 1, 1, 1, 1),
        ),
        EndpointCase("post", "api_media_episode_sync", args=("tv", "tmdb", 1, 1, 1)),
        EndpointCase("get", "api_search_provider", args=("movie",)),
        EndpointCase("get", "api_statistics"),
    ]
