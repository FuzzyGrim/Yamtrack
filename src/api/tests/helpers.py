from django.conf import settings


def check_calendar_event_structure(test_case, item):
    """Assert that the given item follows the calendar event structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("item", item)
    check_item_structure(test_case, item["item"])
    test_case.assertIn("item_id", item)
    check_item_id_structure(test_case, item["item_id"])
    test_case.assertIn("parent_id", item)
    check_item_id_structure(test_case, item["parent_id"]) if item[
        "parent_id"
    ] else test_case.assertIsNone(item["parent_id"])
    test_case.assertIn("content_number", item)
    test_case.assertIn("datetime", item)
    test_case.assertIn("notification_sent", item)


def check_changes_history_change_structure(test_case, item):
    """Assert that the given item follows a single changes-history change shape."""
    test_case.assertIn("field", item)
    test_case.assertIn("old_value", item)
    test_case.assertIn("new_value", item)


def check_changes_history_entry_structure(test_case, item):
    """Assert that the given item follows the changes-history entry structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("item_id", item)
    if item["item_id"] is not None:
        check_item_id_structure(test_case, item["item_id"])
    test_case.assertIn("timestamp", item)
    test_case.assertIn("changes", item)
    test_case.assertIsInstance(item["changes"], list)
    for change in item["changes"]:
        check_changes_history_change_structure(test_case, change)


def check_changes_history_record_structure(test_case, item):
    """Assert the given item follows the expected changes-history record structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("item_id", item)
    test_case.assertIn("timestamp", item)
    test_case.assertIn("changes", item)
    for change in item["changes"]:
        test_case.assertIn("field", change)
        test_case.assertIn("old_value", change)
        test_case.assertIn("new_value", change)


def check_complete_lists_structure(test_case, item, *, include_items=False):
    """Assert that the given item follows the complete lists structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("name", item)
    test_case.assertIn("description", item)
    test_case.assertIn("image", item)
    test_case.assertIn("owner", item)
    test_case.assertIn("collaborators", item)
    test_case.assertIn("items_count", item)
    test_case.assertIn("latest_update", item)
    if include_items:
        test_case.assertIn("items", item)
        test_case.assertIn("pagination", item["items"])
        check_pagination_structure(test_case, item["items"]["pagination"])
        test_case.assertIn("results", item["items"])
        for list_item in item["items"]["results"]:
            check_media_structure(test_case, list_item)


def check_complete_media_structure(test_case, item):
    """Assert that the given item follows the complete media structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("media_id", item)
    test_case.assertIn("source", item)
    test_case.assertIn("source_url", item)
    test_case.assertIn("media_type", item)
    test_case.assertIn("title", item)
    test_case.assertIn("image", item)
    test_case.assertIn("synopsis", item)
    test_case.assertIn("genres", item)
    test_case.assertIn("score", item)
    test_case.assertIn("score_count", item)
    test_case.assertIn("details", item)
    check_details_structure(test_case, item["media_type"], item["details"])
    test_case.assertIn("related", item)
    check_related_structure(test_case, item["media_type"], item["related"])
    test_case.assertIn("item_id", item)
    check_item_id_structure(test_case, item["item_id"])
    test_case.assertIn("parent_id", item)
    check_item_id_structure(test_case, item["parent_id"]) if item[
        "parent_id"
    ] else test_case.assertIsNone(item["parent_id"])
    test_case.assertIn("tracked", item)
    test_case.assertIn("consumptions_number", item)
    test_case.assertIn("consumptions", item)
    for consumption in item["consumptions"]:
        check_consumption_structure(test_case, consumption)
    test_case.assertIn("lists", item)
    for lst in item["lists"]:
        check_minimized_lists_structure(test_case, lst)


def check_consumption_structure(test_case, item):
    """Assert that the given item follows the expected consumption structure."""
    test_case.assertIn("consumption_id", item)
    test_case.assertIn("created", item)
    test_case.assertIn("score", item)
    test_case.assertIn("progress", item)
    test_case.assertIn("status", item)
    test_case.assertIn("start_date", item)
    test_case.assertIn("end_date", item)
    test_case.assertIn("notes", item)


def check_crew_member_structure(test_case, item):
    """Assert that the given item follows the expected crew member structure."""
    test_case.assertIn("job", item)
    test_case.assertIn("department", item)
    test_case.assertIn("credit_id", item)
    test_case.assertIn("adult", item)
    test_case.assertIn("gender", item)
    test_case.assertIn("id", item)
    test_case.assertIn("known_for_department", item)
    test_case.assertIn("name", item)
    test_case.assertIn("original_name", item)
    test_case.assertIn("popularity", item)
    test_case.assertIn("profile_path", item)


def check_details_structure(test_case, media_type, details):
    """Assert that the given details dict follows the expected structure."""
    functions = {
        "anime": _check_anime_details_structure,
        "board_game": _check_board_game_details_structure,
        "book": _check_book_details_structure,
        "comic": _check_comic_details_structure,
        "episode": _check_episode_details_structure,
        "game": _check_game_details_structure,
        "manga": _check_manga_details_structure,
        "movie": _check_movie_details_structure,
        "season": _check_season_details_structure,
        "tv": _check_tv_details_structure,
    }
    if media_type in functions:
        functions[media_type](test_case, details)


def check_guest_star_structure(test_case, item):
    """Assert that the given item follows the expected guest star structure."""
    test_case.assertIn("character", item)
    test_case.assertIn("credit_id", item)
    test_case.assertIn("order", item)
    test_case.assertIn("adult", item)
    test_case.assertIn("gender", item)
    test_case.assertIn("id", item)
    test_case.assertIn("known_for_department", item)
    test_case.assertIn("name", item)
    test_case.assertIn("original_name", item)
    test_case.assertIn("popularity", item)
    test_case.assertIn("profile_path", item)


def check_health_structure(test_case, item):
    """Assert that the given item follows the expected health endpoint structure."""
    test_case.assertIn("status", item)
    test_case.assertIn(item["status"], ["ok", "unavailable"])
    test_case.assertIn("timestamp", item)
    test_case.assertIn("checks", item)
    for check in item["checks"].values():
        test_case.assertIn("status", check)
        test_case.assertIn(check["status"], ["ok", "error"])
        test_case.assertIn("error", check)


def check_info_structure(test_case, item):
    """Assert that the given item follows the expected info endpoint structure."""
    test_case.assertIn("version", item)
    test_case.assertEqual(item["version"], settings.VERSION)
    test_case.assertIn("debug", item)
    test_case.assertEqual(item["debug"], settings.DEBUG)
    test_case.assertIn("frontend_url", item)
    test_case.assertIn("language", item)
    test_case.assertEqual(item["language"], settings.LANGUAGE_CODE)
    test_case.assertIn("timezone", item)
    test_case.assertEqual(item["timezone"], settings.TIME_ZONE)
    test_case.assertIn("admin_enabled", item)
    test_case.assertEqual(item["admin_enabled"], settings.ADMIN_ENABLED)
    test_case.assertIn("track_time", item)
    test_case.assertEqual(item["track_time"], settings.TRACK_TIME)


def check_item_id_structure(test_case, item):
    """Assert that the given item ID follows the expected structure."""
    test_case.assertIsInstance(item, str)
    parts = item.split("/")
    test_case.assertIn(len(parts), [3, 4, 5])


def check_item_structure(test_case, item):
    """Assert that the given item follows the expected media item structure."""
    test_case.assertIn("media_id", item)
    test_case.assertIn("source", item)
    test_case.assertIn("media_type", item)
    test_case.assertIn("title", item)
    test_case.assertIn("image", item)
    test_case.assertIn("season_number", item)
    test_case.assertIn("episode_number", item)


def check_media_structure(test_case, item):
    """Assert that the given item follows the expected media structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("consumption_id", item)
    test_case.assertIn("item", item)
    check_item_structure(test_case, item["item"])
    test_case.assertIn("item_id", item)
    check_item_id_structure(test_case, item["item_id"])
    test_case.assertIn("parent_id", item)
    check_item_id_structure(test_case, item["parent_id"]) if item[
        "parent_id"
    ] else test_case.assertIsNone(item["parent_id"])
    test_case.assertIn("tracked", item)
    test_case.assertIn("created_at", item)
    test_case.assertIn("score", item)
    test_case.assertIn("status", item)
    test_case.assertIn("progress", item)
    test_case.assertIn("progressed_at", item)
    test_case.assertIn("start_date", item)
    test_case.assertIn("end_date", item)
    test_case.assertIn("notes", item)
    test_case.assertIn("lists", item)
    for lst in item["lists"]:
        check_minimized_lists_structure(test_case, lst)


def check_minimized_lists_structure(test_case, lists):
    """Assert that the given list follows the expected minimized structure."""
    test_case.assertIn("list_id", lists)
    test_case.assertIn("list_item_id", lists)


def check_pagination_structure(test_case, item, *, total=None, limit=None, offset=None):
    """Assert that the given item follows the expected pagination structure."""
    test_case.assertIn("total", item)
    if total is not None:
        test_case.assertEqual(item["total"], total)
    test_case.assertIn("limit", item)
    if limit is not None:
        test_case.assertEqual(item["limit"], limit)
    test_case.assertIn("offset", item)
    if offset is not None:
        test_case.assertEqual(item["offset"], offset)
    test_case.assertIn("next", item)
    test_case.assertIn("previous", item)


def check_related_structure(test_case, media_type, related):
    """Assert that the given related dict follows the expected structure."""
    functions = {
        "anime": _check_anime_related_structure,
        "board_game": _check_board_game_related_structure,
        "book": _check_book_related_structure,
        "comic": _check_comic_related_structure,
        "episode": _check_episode_related_structure,
        "game": _check_game_related_structure,
        "manga": _check_manga_related_structure,
        "movie": _check_movie_related_structure,
        "season": _check_season_related_structure,
        "tv": _check_tv_related_structure,
    }
    if media_type in functions:
        functions[media_type](test_case, related)


def check_statistics_structure(test_case, item):
    """Assert that the given item follows the expected statistics structure."""
    test_case.assertIn("start_date", item)
    test_case.assertIn("end_date", item)
    test_case.assertIn("media_count", item)
    test_case.assertIn("activity_data", item)
    test_case.assertIn("media_type_distribution", item)
    test_case.assertIn("score_distribution", item)
    test_case.assertIn("top_rated", item)
    test_case.assertIn("status_distribution", item)
    test_case.assertIn("status_pie_chart_data", item)
    test_case.assertIn("timeline", item)
    test_case.assertIsInstance(item["media_count"], dict)
    test_case.assertIn("total", item["media_count"])


def _check_anime_details_structure(test_case, details):
    """Assert that the given anime details dict follows the expected structure."""


def _check_anime_related_structure(test_case, related):
    """Assert that the given anime related dict follows the expected structure."""


def _check_board_game_details_structure(test_case, details):
    """Assert that the given board game details dict follows the expected structure."""
    test_case.assertIn("year", details)
    test_case.assertIn("players", details)
    test_case.assertIn("playtime", details)
    test_case.assertIn("min_age", details)
    test_case.assertIn("designers", details)
    test_case.assertIn("publishers", details)


def _check_board_game_related_structure(test_case, related):
    """Assert that the given board game related dict follows the expected structure."""


def _check_book_details_structure(test_case, details):
    """Assert that the given book details dict follows the expected structure."""
    test_case.assertIn("format", details)
    test_case.assertIn("number_of_pages", details)
    test_case.assertIn("publish_date", details)
    test_case.assertIn("author", details)
    test_case.assertIn("publisher", details)
    test_case.assertIn("isbn", details)


def _check_book_related_structure(test_case, related):
    """Assert that the given book related dict follows the expected structure."""


def _check_comic_details_structure(test_case, details):
    """Assert that the given comic details dict follows the expected structure."""
    test_case.assertIn("start_date", details)
    test_case.assertIn("publisher", details)
    test_case.assertIn("issues_count", details)
    test_case.assertIn("last_issue_name", details)
    test_case.assertIn("last_issue_number", details)
    test_case.assertIn("people", details)
    test_case.assertIn("last_updated", details)
    test_case.assertIn("last_issue_id", details)


def _check_comic_related_structure(test_case, related):
    """Assert that the given comic related dict follows the expected structure."""
    test_case.assertIn("from_the_same_publisher", related)
    for item in related["from_the_same_publisher"]:
        test_case.assertIn("media_id", item)
        test_case.assertIn("source", item)
        test_case.assertIn("media_type", item)
        test_case.assertIn("title", item)
        test_case.assertIn("image", item)


def _check_episode_details_structure(test_case, details):
    """Assert that the given episode details dict follows the expected structure."""
    test_case.assertIn("air_date", details)
    test_case.assertIn("episode_number", details)
    test_case.assertIn("season_number", details)
    test_case.assertIn("runtime", details)
    test_case.assertIn("episode_type", details)
    test_case.assertIn("crew", details)
    for crew_member in details["crew"]:
        check_crew_member_structure(test_case, crew_member)
    test_case.assertIn("guest_stars", details)
    for guest_star in details["guest_stars"]:
        check_guest_star_structure(test_case, guest_star)


def _check_episode_related_structure(test_case, related):
    """Assert that the given episode related dict follows the expected structure."""


def _check_game_details_structure(test_case, details):
    """Assert that the given game details dict follows the expected structure."""
    test_case.assertIn("format", details)
    test_case.assertIn("release_date", details)
    test_case.assertIn("themes", details)
    test_case.assertIn("platforms", details)


def _check_game_related_structure(test_case, related):
    """Assert that the given game related dict follows the expected structure."""
    test_case.assertIn("parent_game", related)
    test_case.assertIn("remasters", related)
    test_case.assertIn("remakes", related)
    test_case.assertIn("expansions", related)
    test_case.assertIn("standalone_expansions", related)
    test_case.assertIn("expanded_games", related)


def _check_manga_details_structure(test_case, details):
    """Assert that the given manga details dict follows the expected structure."""


def _check_manga_related_structure(test_case, related):
    """Assert that the given manga related dict follows the expected structure."""


def _check_movie_details_structure(test_case, details):
    """Assert that the given movie details dict follows the expected structure."""
    test_case.assertIn("format", details)
    test_case.assertIn("release_date", details)
    test_case.assertIn("status", details)
    test_case.assertIn("runtime", details)
    test_case.assertIn("studios", details)
    test_case.assertIn("country", details)
    test_case.assertIn("languages", details)


def _check_movie_related_structure(test_case, related):
    """Assert that the given movie related dict follows the expected structure."""


def _check_season_details_structure(test_case, details):
    """Assert that the given season details dict follows the expected structure."""
    test_case.assertIn("first_air_date", details)
    test_case.assertIn("last_air_date", details)
    test_case.assertIn("episodes", details)
    test_case.assertIn("runtime", details)
    test_case.assertIn("total_runtime", details)
    test_case.assertIn("tvdb_id", details)


def _check_season_related_structure(test_case, related):
    """Assert that the given season related dict follows the expected structure."""
    test_case.assertIn("episodes", related)
    for episode in related["episodes"]:
        check_media_structure(test_case, episode)


def _check_tv_details_structure(test_case, details):
    """Assert that the given TV details dict follows the expected structure."""
    test_case.assertIn("format", details)
    test_case.assertIn("first_air_date", details)
    test_case.assertIn("last_air_date", details)
    test_case.assertIn("status", details)
    test_case.assertIn("seasons", details)
    test_case.assertIn("episodes", details)
    test_case.assertIn("runtime", details)
    test_case.assertIn("studios", details)
    test_case.assertIn("country", details)
    test_case.assertIn("languages", details)
    test_case.assertIn("tvdb_id", details)
    test_case.assertIn("last_episode_season", details)
    test_case.assertIn("next_episode_season", details)


def _check_tv_related_structure(test_case, related):
    """Assert that the given TV related dict follows the expected structure."""
    test_case.assertIn("seasons", related)
    for season in related["seasons"]:
        check_media_structure(test_case, season)


WATCH_PROVIDERS_RESPONSE = {
    "US": {
        "link": "https://www.themoviedb.org/movie/1/watch?locale=US",
        "flatrate": [
            {
                "provider_id": 8,
                "provider_name": "Netflix",
                "logo_path": "/netflix.jpg",
                "display_priority": 1,
            },
        ],
        "free": [
            {
                "provider_id": 73,
                "provider_name": "Tubi TV",
                "logo_path": "/tubi.jpg",
                "display_priority": 0,
            },
        ],
        "ads": [
            {
                "provider_id": 8,
                "provider_name": "Netflix",
                "logo_path": "/netflix.jpg",
                "display_priority": 1,
            },
        ],
        "buy": [
            {
                "provider_id": 2,
                "provider_name": "Apple TV",
                "logo_path": "/apple.jpg",
                "display_priority": 4,
            },
        ],
        "rent": [
            {
                "provider_id": 2,
                "provider_name": "Apple TV",
                "logo_path": "/apple.jpg",
                "display_priority": 4,
            },
        ],
    },
    "IT": {
        "flatrate": [
            {
                "provider_id": 119,
                "provider_name": "Amazon Prime Video",
                "logo_path": "/prime.jpg",
                "display_priority": 2,
            },
        ],
    },
}
