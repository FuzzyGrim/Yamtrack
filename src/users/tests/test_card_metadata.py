"""Parser and default profile for per-type card metadata."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from app.models.choices import MediaTypes
from users.card_metadata import (
    ABSORBED_PREFERENCE_FIELDS,
    OMIT,
    _format_episode_code,
    absorbed_preference_value,
    apply_absorbed_preference,
    default_profile,
    parse_card_metadata,
    profiles_from_legacy,
)


class CardMetadataTests(SimpleTestCase):
    """The registry round-trips a save and reproduces the movie card."""

    def test_episode_code_padding_and_missing_coordinates(self):
        for season, episode, expected in (
            (0, 3, "S00E03"), (3, 3, "S03E03"), (123, 456, "S123E456"),
            (3, None, "S03"), (None, 3, None),
        ):
            with self.subTest(season=season, episode=episode):
                self.assertEqual(_format_episode_code(season, episode), expected)

    def test_default_movie_profile_matches_today(self):
        """A movie card shows the year and progress until the user edits it."""
        profile = default_profile(MediaTypes.MOVIE.value)
        self.assertEqual(profile["fields"], ["release_year", "progress"])
        self.assertEqual(profile["display"], "hover")
        self.assertFalse(profile["options"]["rating"]["hide_zero"])

    def test_parser_drops_unknown_fields(self):
        """A stale field id cannot land in the stored profile."""
        parsed = parse_card_metadata(
            {
                "version": 1,
                "types": {
                    "movie": {
                        "display": "always",
                        "fields": ["genres", "not_a_field", "release_year"],
                        "options": {"rating": {"hide_zero": True}},
                    }
                },
            }
        )
        movie = parsed["types"]["movie"]
        self.assertEqual(movie["fields"], ["genres", "release_year"])
        self.assertEqual(movie["display"], "always")
        self.assertTrue(movie["options"]["rating"]["hide_zero"])

    def test_metadata_fields_are_offered_where_the_media_type_has_them(self):
        """A metadata-backed property follows the media type's field registry."""
        from users.card_metadata import editor_catalog

        fields_by_type = {
            entry["id"]: {field["id"] for field in entry["fields"]}
            for entry in editor_catalog()
        }
        self.assertIn("platforms", fields_by_type["game"])
        self.assertNotIn("platforms", fields_by_type["movie"])
        self.assertNotIn("platforms", fields_by_type["boardgame"])

    def test_parser_drops_a_metadata_field_the_type_does_not_have(self):
        """Platform cannot be saved onto a movie profile."""
        parsed = parse_card_metadata(
            {
                "version": 1,
                "types": {
                    "game": {"fields": ["platforms", "release_year"]},
                    "movie": {"fields": ["platforms", "release_year"]},
                },
            }
        )
        self.assertEqual(parsed["types"]["game"]["fields"], ["platforms", "release_year"])
        self.assertEqual(parsed["types"]["movie"]["fields"], ["release_year"])

    def test_platform_line_reads_the_item_platforms(self):
        """A game card shows its platforms, joined when there are several."""
        from users.card_metadata import card_lines

        user = SimpleNamespace(
            card_metadata=parse_card_metadata(
                {"version": 1, "types": {"game": {"fields": ["platforms"]}}}
            )
        )
        item = SimpleNamespace(platforms=["PC", "PlayStation 5", "Switch"])
        self.assertEqual(
            card_lines(user, "game", item, None)[0]["text"],
            "PC, PlayStation 5, Switch",
        )

    def test_platform_line_prefers_collection_platforms(self):
        from users.card_metadata import card_lines

        user = SimpleNamespace(
            card_metadata=parse_card_metadata(
                {"version": 1, "types": {"game": {"fields": ["platforms"]}}}
            )
        )
        item = SimpleNamespace(platforms=["PC", "Switch"])
        media = SimpleNamespace(card_collection_platforms=["PlayStation 5", "Switch"])
        self.assertEqual(card_lines(user, "game", item, media)[0]["text"], "PlayStation 5, Switch")
        media.card_collection_platforms = []
        self.assertEqual(card_lines(user, "game", item, media)[0]["text"], "PC, Switch")

    def test_legacy_seed_removes_progress_when_the_bar_is_off(self):
        """progress_bar false drops the progress field on every type."""
        seeded = profiles_from_legacy("always", False, True)
        movie = seeded["types"]["movie"]
        self.assertNotIn("progress", movie["fields"])
        self.assertEqual(movie["display"], "always")
        self.assertTrue(movie["options"]["rating"]["hide_zero"])

    def test_progress_bar_ignores_types_that_have_no_progress_field(self):
        """Person cards do not vote, so a fresh profile still reports the bar on."""
        user = SimpleNamespace(card_metadata={})
        apply_absorbed_preference(user, "progress_bar", True)
        self.assertTrue(absorbed_preference_value(user, "progress_bar"))

    def test_mixed_display_omits_the_legacy_name(self):
        """GET drops a name when two types disagree."""
        user = SimpleNamespace(card_metadata={})
        display_field = next(iter(ABSORBED_PREFERENCE_FIELDS))
        apply_absorbed_preference(user, display_field, "hover")
        user.card_metadata["types"]["movie"]["display"] = "always"
        self.assertIs(absorbed_preference_value(user, display_field), OMIT)


class HandRolledCardTests(SimpleTestCase):
    """Hand-rolled cards follow the profile of their own media type."""

    @staticmethod
    def _user(types):
        return SimpleNamespace(
            card_metadata=parse_card_metadata({"version": 1, "types": types})
        )

    def test_rating_visibility_uses_the_given_media_type(self):
        """A music card hides a zero score even when the context has no item."""
        from app.templatetags.app_tags import score_is_visible

        user = self._user(
            {
                "music": {
                    "fields": ["artist"],
                    "options": {"rating": {"hide_zero": True}},
                }
            }
        )
        context = {"user": user}
        self.assertFalse(score_is_visible(context, 0, "music"))
        self.assertTrue(score_is_visible(context, 0, "movie"))

    def test_artist_field_reads_structured_album_credits(self):
        """A multi-artist album with no legacy artist still shows its artists."""
        from users.card_metadata import _artist

        credits = [
            SimpleNamespace(artist=SimpleNamespace(name="A"), join_phrase=" & "),
            SimpleNamespace(artist=SimpleNamespace(name="B"), join_phrase=""),
        ]
        album = SimpleNamespace(
            artist=None,
            artist_credits=SimpleNamespace(all=lambda: credits),
        )
        self.assertEqual(_artist(album, None, None), "A & B")

    def test_subtitle_class_defers_to_per_line_visibility(self):
        """A hover line on an 'always' type must not get the card-level class."""
        from app.templatetags.app_tags import card_subtitle_class

        user = self._user(
            {
                "music": {
                    "display": "always",
                    "fields": ["artist", "release_year"],
                    "lines": [
                        {"fields": ["artist"], "display": "dormant"},
                        {"fields": ["release_year"], "display": "hover"},
                    ],
                }
            }
        )
        self.assertEqual(card_subtitle_class({"user": user}, "music"), "")
        plain = self._user({"music": {"display": "always"}})
        self.assertIn(
            "media-card-subtitle-always", card_subtitle_class({"user": plain}, "music")
        )

    def test_title_classes_only_follow_a_customised_title(self):
        """Default titles keep a card's own clamps; edited titles add classes."""
        from app.templatetags.app_tags import card_title_classes

        default = self._user({"music": {"fields": ["artist"]}})
        self.assertEqual(card_title_classes({"user": default}, "music"), "")
        edited = self._user(
            {
                "music": {
                    "fields": ["artist"],
                    "options": {"title": {"overflow": "wrap", "lines": 2}},
                }
            }
        )
        classes = card_title_classes({"user": edited}, "music")
        self.assertIn("media-card-title-wrap", classes)
        self.assertIn("media-card-title-rest-2", classes)

    def test_date_fields_show_the_entry_dates_in_the_user_format(self):
        """Date added, start and end read the entry and follow the date format."""
        from datetime import UTC, datetime

        from users.card_metadata import card_lines

        user = self._user(
            {
                "movie": {
                    "lines": [
                        {"fields": ["date_added"], "display": "dormant"},
                        {"fields": ["start_date", "end_date"], "display": "dormant"},
                    ]
                }
            }
        )
        user.date_format = "iso_8601"
        user.time_format = "24"
        entry = SimpleNamespace(
            created_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
            start_date=datetime(2026, 3, 4, 12, tzinfo=UTC),
            end_date=datetime(2026, 5, 6, 12, tzinfo=UTC),
        )
        lines = card_lines(user, "movie", None, entry)
        self.assertEqual(
            [line["text"] for line in lines],
            ["2026-01-02", "2026-03-04 · 2026-05-06"],
        )

    def test_date_fields_prefer_the_aggregated_dates_and_skip_blanks(self):
        """A grouped list row shows the whole item's dates; empty dates vanish."""
        from datetime import UTC, datetime

        from users.card_metadata import card_lines

        user = self._user(
            {"game": {"lines": [{"fields": ["start_date"], "display": "dormant"}]}}
        )
        user.date_format = "iso_8601"
        grouped = SimpleNamespace(
            start_date=datetime(2026, 3, 4, 12, tzinfo=UTC),
            aggregated_start_date=datetime(2026, 1, 1, 12, tzinfo=UTC),
        )
        self.assertEqual(card_lines(user, "game", None, grouped)[0]["text"], "2026-01-01")
        self.assertEqual(card_lines(user, "game", None, SimpleNamespace()), [])

    def test_every_non_person_type_can_pick_the_date_fields(self):
        """The picker offers the three date properties for each media type."""
        from users.card_metadata import PERSON, editor_catalog

        for entry in editor_catalog():
            ids = {field["id"] for field in entry["fields"]}
            expected = set() if entry["id"] == PERSON else {
                "date_added", "start_date", "end_date"
            }
            self.assertEqual(ids & {"date_added", "start_date", "end_date"}, expected)
