from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from app.models import (
    TV,
    Album,
    AlbumArtist,
    AlbumTracker,
    Anime,
    Artist,
    ArtistTracker,
    Book,
    CollectionEntry,
    ComicIssue,
    DiscoverFeedback,
    DiscoverFeedbackType,
    Episode,
    Item,
    ItemTag,
    MediaTypes,
    MetadataProviderPreference,
    Movie,
    Podcast,
    PodcastEpisode,
    PodcastShow,
    PodcastShowTracker,
    Season,
    Sources,
    Status,
    Tag,
)
from app.providers import services
from app.services.metadata_resolution import MetadataResolutionResult


def _tv_with_seasons_payload(media_id, source, *, title="Test Show", episode_count=3):
    episodes = [
        {
            "episode_number": episode_number,
            "name": f"Episode {episode_number}",
            "air_date": f"2024-01-0{episode_number}",
            "runtime": 24,
        }
        for episode_number in range(1, episode_count + 1)
    ]
    return {
        "media_id": media_id,
        "source": source,
        "media_type": MediaTypes.TV.value,
        "title": title,
        "image": "https://example.com/show.jpg",
        "related": {
            "seasons": [
                {
                    "source": source,
                    "media_type": MediaTypes.SEASON.value,
                    "image": "https://example.com/season.jpg",
                    "media_id": media_id,
                    "title": title,
                    "original_title": title,
                    "localized_title": title,
                    "season_number": 1,
                    "season_title": "Season 1",
                    "first_air_date": "2024-01-01",
                    "last_air_date": None,
                    "max_progress": episode_count,
                    "episode_count": episode_count,
                    "score": None,
                    "score_count": None,
                    "details": {"episodes": episode_count},
                },
            ],
        },
        "season/1": {
            "source": source,
            "media_type": MediaTypes.SEASON.value,
            "media_id": media_id,
            "season_number": 1,
            "season_title": "Season 1",
            "title": title,
            "image": "https://example.com/season.jpg",
            "max_progress": episode_count,
            "score": None,
            "score_count": None,
            "details": {
                "first_air_date": "2024-01-01",
                "last_air_date": None,
                "episodes": episode_count,
            },
            "cast": [],
            "crew": [],
            "episodes": episodes,
            "providers": {},
        },
    }


class TrackModalViewTests(TestCase):
    """Test the track modal view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.mock_get_media_metadata = patch(
            "app.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        )
        self.mock_fetch_releases = patch("app.models.Item.fetch_releases")
        self.mock_get_media_metadata.start()
        self.mock_fetch_releases.start()
        self.addCleanup(self.mock_get_media_metadata.stop)
        self.addCleanup(self.mock_fetch_releases.stop)

        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

    def assert_release_shortcut_labels(self, response, count=2):
        """The release date picker should carry the release-date suggestion label."""
        self.assertIn("Release Date", response.content.decode())

    def test_focus_notes_opens_and_focuses_the_notes_field(self):
        """focus=notes (the detail-page Notes button) opens the collapsed notes field."""
        url = reverse(
            "track_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "238",
            },
        )

        focused = self.client.get(
            url,
            {"instance_id": self.movie.id, "focus": "notes"},
            HTTP_HX_REQUEST="true",
        )
        plain = self.client.get(
            url,
            {"instance_id": self.movie.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertTrue(focused.context["notes_focus"])
        self.assertContains(focused, "notesOpen: true")
        self.assertContains(focused, "textarea[name=notes]")
        self.assertFalse(plain.context["notes_focus"])
        self.assertContains(plain, "notesOpen: false")
        self.assertNotContains(plain, "textarea[name=notes]")

    def test_focus_notes_without_instance_resolves_the_latest_entry_now(self):
        """A new entry made after the page loaded is what the Notes button edits."""
        url = reverse(
            "track_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "238",
            },
        )
        self.movie.end_date = datetime(2024, 1, 1, tzinfo=UTC)
        self.movie.save()
        newer = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2025, 6, 1, tzinfo=UTC),
        )

        response = self.client.get(url, {"focus": "notes"}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.context["current_instance"].id, newer.id)

    @patch("app.services.metadata_resolution.ItemProviderLink.objects.update_or_create")
    def test_existing_tv_modal_does_not_write_provider_links(self, upsert):
        """Opening a home card editor must not wait on optional persistence."""
        upsert.side_effect = OperationalError("database is locked")
        item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
        )
        tracked = TV.objects.create(
            user=self.user,
            item=item,
            status=Status.IN_PROGRESS.value,
        )
        with patch(
            "app.providers.services.get_media_metadata",
            return_value=_tv_with_seasons_payload("1396", Sources.TMDB.value),
        ):
            response = self.client.get(
                reverse(
                    "track_modal",
                    kwargs={
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.TV.value,
                        "media_id": item.media_id,
                    },
                ),
                {"instance_id": tracked.id, "home_row_id": "continue"},
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        upsert.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_season_set_to_no_status_still_opens_track_modal(self, mock_get_metadata):
        """Saving a season as No Status must not break reopening its modal (#1444).

        The status history tab renders every change, and the No Status change
        has a null status, which the template used to translate as a label.
        """
        mock_get_metadata.return_value = _tv_with_seasons_payload(
            "1396",
            Sources.TMDB.value,
        )["season/1"]
        tv_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )

        save_response = self.client.post(
            reverse("media_save"),
            {
                "media_id": "1396",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "instance_id": season.id,
                "status": "",
            },
        )
        self.assertLess(save_response.status_code, 400)
        season.refresh_from_db()
        self.assertIsNone(season.status)

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.SEASON.value,
                    "media_id": "1396",
                    "season_number": 1,
                },
            ),
            {"instance_id": season.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Status")
        self.assertEqual(response.context["status_changes"][0]["new"], None)

    def test_enter_key_default_button_is_not_delete(self):
        """Enter submits via the first submit button, which must not be Delete (#1795)."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        content = response.content.decode()
        form_html = content[content.index("<form") :]
        first_submit = form_html[form_html.index('type="submit"') :][:200]
        self.assertNotIn("formaction", first_submit)

    def test_track_modal_view_existing_media(self):
        """Test the track modal view for existing media."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        self.assertIn("form", response.context)
        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"], self.movie)
        self.assertEqual(response.context["return_url"], "/home")
        self.assertTrue(response.context["metadata_tab_available"])
        self.assertTrue(response.context["discover_tab_available"])
        self.assertFalse(response.context["is_hidden_from_discover"])
        content = response.content.decode()
        # Split per #1243: the start-date picker shows Start Now + Release
        # Date, the end-date picker shows only Just Finished. "Release Date"
        # also appears once per picker in the unconditional x-data config
        # (see suggestionLabel below), plus once as the start field's
        # visible quick-action label.
        self.assertIn("Start Now", content)
        self.assertIn("Just Finished", content)
        self.assertIn("Release Date", content)
        self.assertIn('"id": "release-date"', content)
        general_field_names = [
            field.name for field in response.context["general_fields"]
        ]
        self.assertEqual(general_field_names[:2], ["score", "status"])
        self.assertEqual(
            [field.name for field in response.context["metadata_fields"]],
            ["image_url"],
        )
        self.assertContains(response, "General")
        self.assertContains(response, "Metadata")
        self.assertContains(response, "Discover")
        # A tracked item gets the Cover section (upload, link, find) in place of
        # the old single Image URL box.
        self.assertContains(response, "choose an image")
        self.assertContains(response, "enter a URL")
        self.assertContains(response, "Metadata Provider")
        self.assertContains(response, "Fix match")
        self.assertContains(response, "Custom")
        self.assertContains(response, "Currently visible in Discover.")
        self.assertContains(response, 'hx-post="/discover/toggle-hidden"', html=False)
        self.assertContains(response, 'name="action"', html=False)
        self.assertContains(response, "data-date-time-picker-root", html=False)
        self.assertContains(response, '"quickActions"', html=False)
        self.assertNotContains(response, "Custom Metadata")

    def test_track_modal_exposes_shared_general_form_presentation(self):
        """The General tab carries a serialized model for the shared TrackForm."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        model = response.context["track_form_presentation"]["model"]
        self.assertEqual(model["title"], "Test Movie")
        self.assertEqual(model["submitLabel"], "Update")
        self.assertEqual(model["action"], reverse("media_save") + "?next=/home")
        self.assertEqual(
            model["deleteAction"],
            reverse("media_delete") + "?next=/home",
        )
        self.assertEqual(model["hidden"]["instance_id"], str(self.movie.id))
        self.assertEqual(
            model["hidden"]["track_form_id"],
            response.context["track_form_id"],
        )
        self.assertTrue(model["csrfToken"])
        self.assertEqual(
            [field["name"] for field in model["fields"]][:2],
            ["score", "status"],
        )
        status_field = next(
            field for field in model["fields"] if field["name"] == "status"
        )
        self.assertEqual(status_field["type"], "select")
        self.assertTrue(status_field["options"])
        self.assertEqual(model["notes"]["value"], "")
        self.assertContains(response, "data-track-form-root")
        self.assertContains(response, "data-track-form-legacy")

    def test_track_modal_serializes_remaining_tabs_for_shared_dialog(self):
        """The shared dialog receives the available tab models and actions."""
        tag = Tag.objects.create(user=self.user, name="Favourite")
        ItemTag.objects.create(tag=tag, item=self.item)

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        tabs = response.context["track_form_presentation"]["tabs"]
        by_id = {tab["id"]: tab for tab in tabs}
        self.assertEqual(
            list(by_id),
            ["general", "tags", "metadata", "collection", "discover"],
        )
        self.assertEqual(by_id["general"]["kind"], "general")
        self.assertEqual(by_id["tags"]["kind"], "tags")
        self.assertEqual(
            [tag["name"] for tag in by_id["tags"]["tags"]],
            ["Favourite"],
        )
        self.assertEqual(
            by_id["tags"]["urls"]["toggle"],
            reverse("tag_item_toggle"),
        )
        self.assertEqual(by_id["metadata"]["kind"], "server")
        self.assertEqual(by_id["collection"]["kind"], "server")
        self.assertEqual(by_id["discover"]["itemId"], self.item.id)
        self.assertFalse(by_id["discover"]["hidden"])
        self.assertEqual(
            by_id["discover"]["url"],
            reverse("discover_toggle_hidden"),
        )

    def test_track_modal_serializes_status_history_tab(self):
        """A tracked show exposes its status changes to the shared dialog."""
        tv_item = Item.objects.create(
            media_id="279977",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Finished Show",
            image="https://example.com/finished-show.jpg",
        )
        TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": tv_item.media_id,
                },
            )
            + "?return_url=/home",
        )

        tabs = response.context["track_form_presentation"]["tabs"]
        status_tab = next(tab for tab in tabs if tab["id"] == "status-history")
        self.assertEqual(status_tab["kind"], "status-history")
        self.assertTrue(status_tab["changes"])
        self.assertEqual(status_tab["changes"][0]["next"], Status.COMPLETED.value)

    def test_track_modal_presentation_offers_rewatch_on_completed_show(self):
        """The shared form keeps the repeat identity offered by the server."""
        tv_item = Item.objects.create(
            media_id="279977",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Finished Show",
            image="https://example.com/finished-show.jpg",
        )
        TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": tv_item.media_id,
                },
            )
            + "?return_url=/home",
        )

        repeat = response.context["track_form_presentation"]["model"][
            "repeatIdentity"
        ]
        self.assertEqual(repeat["rewatchAction"], "start")
        self.assertEqual(
            repeat["rewatchUrl"],
            reverse("media_rewatch") + "?action=start&next=/home",
        )
        self.assertEqual(repeat["rewatchLabel"], "Rewatch")

    def test_track_modal_renders_tags_tab_for_tracked_media(self):
        """The edit modal exposes tag management in its own tab."""
        tag = Tag.objects.create(user=self.user, name="Favourite")
        ItemTag.objects.create(tag=tag, item=self.item)

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["tags_tab_available"])
        self.assertEqual(response.context["tag_assignment_data"]["item"]["id"], self.item.id)
        self.assertContains(response, "@click=\"activeTab = 'tags'\"")
        self.assertContains(response, "Favourite")
        self.assertContains(response, 'name="tag_surface"')
        self.assertContains(response, 'value="track"')
        self.assertContains(response, "data-tag-assignment-root")
        self.assertContains(response, 'data-tag-surface="track"')
        self.assertNotContains(response, "tagsOpen = false")

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_omits_tags_tab_for_untracked_media(
        self,
        mock_get_metadata,
    ):
        """Without an item row there is nothing to attach tags to yet."""
        mock_get_metadata.return_value = {
            "media_id": "278",
            "title": "New Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
        }

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "278",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["tags_tab_available"])
        self.assertNotContains(response, "@click=\"activeTab = 'tags'\"")

    def test_session_history_row_opens_standard_modal_for_instance(self):
        """Session rows preserve the tracked instance when opening the editor."""
        request = RequestFactory().get(
            "/history/sessions?media_type=movie&media_id=238&source=tmdb",
        )
        markup = render_to_string(
            "app/components/session_history_row.html",
            {
                "entry": {
                    "item": self.item,
                    "media_type": MediaTypes.MOVIE.value,
                    "display_title": self.item.title,
                    "title": self.item.title,
                    "poster": self.item.image,
                    "entry_key": str(self.movie.id),
                    "instance_id": self.movie.id,
                },
                "user": self.user,
                "request": request,
            },
            request=request,
        )

        self.assertIn(f'"instance_id": "{self.movie.id}"', markup)
        self.assertIn('"standard_modal": "1"', markup)

    def test_track_modal_keeps_tracked_tmdb_show_deletable_when_provider_returns_404(
        self,
    ):
        """An existing show can still be deleted when TMDB has removed it."""
        item = Item.objects.create(
            media_id="279977",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Deleted Show",
            image="https://example.com/deleted-show.jpg",
        )
        tv = TV.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        not_found_response = requests.Response()
        not_found_response.status_code = requests.codes.not_found
        self.mock_get_media_metadata.side_effect = services.ProviderAPIError(
            Sources.TMDB.value,
            requests.exceptions.HTTPError(response=not_found_response),
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": item.media_id,
                },
            )
            + "?return_url=/details/tmdb/tv/279977/deleted-show",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["general_existing_instance"], tv)
        content = response.content.decode()
        delete_start = content.index('formaction="/media_delete')
        delete_button = content[delete_start : content.index("</button>", delete_start)]
        self.assertIn("bg-red-700", delete_button)
        self.assertNotIn("disabled", delete_button)

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_view_existing_episode_exposes_score(self, mock_get_metadata):
        """Episode history edits should use the standard modal with rating support."""
        mock_get_metadata.return_value = {
            "media_id": "episode-show-1",
            "media_type": MediaTypes.EPISODE.value,
            "source": Sources.TMDB.value,
            "title": "Episode Show",
            "episode_title": "Episode Two",
            "image": "http://example.com/episode.jpg",
            "details": {},
        }
        tv_item = Item.objects.create(
            media_id="episode-show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Episode Show",
            image="http://example.com/show.jpg",
        )
        tv = TV.objects.create(item=tv_item, user=self.user)
        season_item = Item.objects.create(
            media_id="episode-show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Episode Show Season 1",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
        )
        episode_item = Item.objects.create(
            media_id="episode-show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode Two",
            image="http://example.com/episode.jpg",
            season_number=1,
            episode_number=2,
        )
        episode = Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
            score=7,
        )
        CollectionEntry.objects.create(user=self.user, item=episode_item)

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.EPISODE.value,
                    "media_id": "episode-show-1",
                    "season_number": 1,
                },
            )
            + f"?instance_id={episode.id}&standard_modal=1&return_url=/history",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")
        self.assertEqual(response.context["media"], episode)
        self.assertEqual(
            [field.name for field in response.context["general_fields"]],
            ["score", "status", "start_date", "end_date", "entry_source"],
        )
        self.assertContains(response, 'name="score"', html=False)
        self.assertContains(response, 'value="7.0"', html=False)
        self.assertContains(response, 'name="notes"', html=False)
        self.assertContains(response, "Collection")
        self.assertContains(
            response,
            '<p class="text-sm tracking-wide text-[var(--color-text-muted)]">Collected</p>',
            html=True,
        )

        # The collection tab is included with `only`, which drops context
        # processor values. Without an explicit csrf_token the plain form POST
        # to /collection/add/ renders an empty token and 403s. See issue #377.
        content = response.content.decode()
        form_start = content.index(f'action="{reverse("collection_add")}')
        add_form = content[form_start : content.index("</form>", form_start)]
        self.assertRegex(add_form, r'name="csrfmiddlewaretoken" value="[^"]+"')

        # Bound to a real watch, so Delete is live and the current values can be
        # saved as a new entry in one submission.
        self.assertEqual(response.context["general_existing_instance"], episode)
        delete_start = content.index('hx-post="/media_delete')
        delete_button = content[delete_start : content.index("</button>", delete_start)]
        self.assertIn("bg-red-700", delete_button)
        self.assertNotIn("disabled", delete_button)
        self.assertIn('hx-post="/media_delete', content)
        self.assertIn('hx-include="closest form"', content)
        self.assertTrue(response.context["episode_save_as_new"])
        self.assertContains(response, "Save as new entry")
        self.assertContains(response, 'name="save_as_new_entry"', html=False)

    def _create_tracked_episode(self):
        """Create a show/season/episode chain and return the tracked episode."""
        tv_item = Item.objects.create(
            media_id="titled-show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Titled Show",
        )
        tv = TV.objects.create(item=tv_item, user=self.user)
        season_item = Item.objects.create(
            media_id="titled-show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Titled Show",
            season_number=1,
        )
        season = Season.objects.create(item=season_item, user=self.user, related_tv=tv)
        episode_item = Item.objects.create(
            media_id="titled-show-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Titled Show",
            season_number=1,
            episode_number=2,
        )
        return Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
        )

    @staticmethod
    def _episode_metadata_payload():
        """Provider payload for a single episode; ``title`` is the show title."""
        return {
            "media_id": "titled-show-1",
            "media_type": MediaTypes.EPISODE.value,
            "source": Sources.TMDB.value,
            "title": "Titled Show",
            "season_title": "Season 1",
            "episode_title": "Failure's Contagious",
            "image": "http://example.com/episode.jpg",
            "details": {},
        }

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_titles_tracked_episode_by_episode_name(
        self,
        mock_get_metadata,
    ):
        """A tracked episode modal names the episode, not just the show."""
        mock_get_metadata.return_value = self._episode_metadata_payload()
        episode = self._create_tracked_episode()

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.EPISODE.value,
                    "media_id": "titled-show-1",
                    "season_number": 1,
                },
            )
            + f"?instance_id={episode.id}&episode_number=2",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["title"], "Failure's Contagious")
        self.assertEqual(response.context["title_subtitle"], "Titled Show · S01E02")
        self.assertContains(response, "Titled Show · S01E02")

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_titles_untracked_episode_by_episode_name(
        self,
        mock_get_metadata,
    ):
        """An untracked episode used to render the bare show title. Issue #1070."""
        mock_get_metadata.return_value = self._episode_metadata_payload()

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.EPISODE.value,
                    "media_id": "titled-show-1",
                    "season_number": 1,
                },
            )
            + "?is_create=1&episode_number=2",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["title"], "Failure's Contagious")
        self.assertEqual(response.context["title_subtitle"], "Titled Show · S01E02")

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_untracked_episode_survives_episode_lookup_404(
        self,
        mock_get_metadata,
    ):
        """A season can list an episode its own endpoint 404s; the modal opens."""
        season_payload = {
            "title": "Titled Show",
            "season_title": "Season 1",
            "episodes": [{"episode_number": 2, "name": "Failure's Contagious"}],
        }

        def get_metadata(media_type, *args, **kwargs):
            if media_type == MediaTypes.EPISODE.value:
                raise services.ProviderAPIError(
                    Sources.TMDB.value,
                    SimpleNamespace(
                        response=SimpleNamespace(status_code=404, headers={}),
                    ),
                    "cached episode lookup failure",
                )
            return season_payload

        mock_get_metadata.side_effect = get_metadata

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.EPISODE.value,
                    "media_id": "titled-show-1",
                    "season_number": 1,
                },
            )
            + "?is_create=1&episode_number=2",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["title"], "Failure's Contagious")
        self.assertEqual(response.context["title_subtitle"], "Titled Show · S01E02")

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_episode_title_falls_back_to_show_and_number(
        self,
        mock_get_metadata,
    ):
        """Without an episode name the header still says which episode it is."""
        payload = self._episode_metadata_payload()
        del payload["episode_title"]
        mock_get_metadata.return_value = payload
        episode = self._create_tracked_episode()

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.EPISODE.value,
                    "media_id": "titled-show-1",
                    "season_number": 1,
                },
            )
            + f"?instance_id={episode.id}&episode_number=2",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["title"], "Titled Show S01E02")
        self.assertEqual(response.context["title_subtitle"], "")

    def test_track_modal_view_renders_release_date_shortcuts_for_existing_media(self):
        """Existing item-backed trackers should expose release-date shortcuts."""
        self.item.release_datetime = datetime(2024, 1, 15, tzinfo=UTC)
        self.item.runtime_minutes = 95
        self.item.save(update_fields=["release_datetime", "runtime_minutes"])

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("2024-01-15", content)
        self.assertIn('"suggestionRuntimeMinutes": "95"', content)
        self.assert_release_shortcut_labels(response)

    def test_track_modal_close_button_supports_split_button_wrapper(self):
        """The shared close button should work for edit/create split-button wrappers."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The shared helper resolves the wrapper's Alpine state key (trackOpen,
        # createTrackOpen, or editTrackOpen) from the surrounding x-show.
        self.assertIn('@click="closeTrackModal($el)"', content)
        self.assertNotContains(response, 'onclick="closeTrackModal(this)"', html=False)

    def test_track_modal_view_uses_stored_discover_hidden_state(self):
        """Discover tab should reflect persisted hidden feedback for the item."""
        DiscoverFeedback.objects.create(
            user=self.user,
            item=self.item,
            feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["discover_tab_available"])
        self.assertTrue(response.context["is_hidden_from_discover"])
        self.assertContains(response, "Currently hidden in Discover.")
        self.assertContains(response, 'name="action"', html=False)

    def test_artist_track_modal_uses_shared_fill_track_shell(self):
        """Music artist trackers should render through the shared modal shell."""
        artist = Artist.objects.create(name="Test Artist")
        tracker = ArtistTracker.objects.create(
            user=self.user,
            artist=artist,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse("artist_track_modal", args=[artist.id]) + "?return_url=/music",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")
        self.assertEqual(response.context["title"], artist.name)
        self.assertEqual(response.context["general_existing_instance"], tracker)
        self.assertFalse(response.context["metadata_tab_available"])
        self.assertContains(response, "General")
        self.assertNotContains(response, "Metadata")

    def test_album_track_modal_uses_shared_fill_track_shell(self):
        """Music album trackers should render through the shared modal shell."""
        artist = Artist.objects.create(name="Test Artist")
        album = Album.objects.create(title="Test Album", artist=artist)
        tracker = AlbumTracker.objects.create(
            user=self.user,
            album=album,
            status=Status.COMPLETED.value,
        )

        response = self.client.get(
            reverse("album_track_modal", args=[album.id]) + "?return_url=/music",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")
        self.assertEqual(response.context["title"], album.title)
        self.assertEqual(response.context["general_existing_instance"], tracker)
        self.assertFalse(response.context["metadata_tab_available"])
        self.assertContains(response, "General")
        self.assertNotContains(response, "Metadata")

    def test_music_tracker_modal_marks_existing_entries_for_edit(self):
        """Saved trackers carry the marker that stops End date auto-filling to now (#1377)."""
        artist = Artist.objects.create(name="Test Artist")
        album = Album.objects.create(title="Test Album", artist=artist)
        url = reverse("album_track_modal", args=[album.id]) + "?return_url=/music"

        self.assertNotContains(self.client.get(url), "data-existing-instance")

        AlbumTracker.objects.create(
            user=self.user,
            album=album,
            status=Status.COMPLETED.value,
            end_date=datetime(2020, 5, 6, 7, 8, 9, tzinfo=UTC),
        )
        self.assertContains(self.client.get(url), "data-existing-instance")

        ArtistTracker.objects.create(
            user=self.user,
            artist=artist,
            status=Status.COMPLETED.value,
        )
        response = self.client.get(
            reverse("artist_track_modal", args=[artist.id]) + "?return_url=/music",
        )
        self.assertContains(response, "data-existing-instance")

    def test_album_track_modal_renders_release_date_shortcuts(self):
        """Album trackers should expose the shared release-date shortcut."""
        artist = Artist.objects.create(name="Test Artist")
        album = Album.objects.create(
            title="Test Album",
            artist=artist,
            release_date=date(2024, 2, 3),
        )

        response = self.client.get(
            reverse("album_track_modal", args=[album.id]) + "?return_url=/music",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("2024-02-03", content)
        self.assert_release_shortcut_labels(response)

    def test_artist_save_redirects_to_canonical_music_details(self):
        """Artist saves should land on the canonical shared details page."""
        artist = Artist.objects.create(name="Saved Artist")

        response = self.client.post(
            reverse("artist_save"),
            {
                "artist_id": artist.id,
                "status": Status.IN_PROGRESS.value,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "saved-artist",
                },
            ),
        )

    def test_album_delete_redirects_to_canonical_music_details(self):
        """Album deletes should land on the canonical shared details page."""
        artist = Artist.objects.create(name="Saved Artist")
        album = Album.objects.create(title="Saved Album", artist=artist)
        AlbumTracker.objects.create(
            user=self.user,
            album=album,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.post(
            reverse("album_delete"),
            {
                "album_id": album.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "saved-artist",
                    "album_id": album.id,
                    "album_slug": "saved-album",
                },
            ),
        )

    @patch("app.services.music.sync_artist_discography")
    @patch("app.providers.musicbrainz.get_artist")
    def test_create_artist_from_search_redirects_to_canonical_music_details(
        self,
        mock_get_artist,
        mock_sync_artist_discography,
    ):
        """Artist search creates should redirect to the canonical shared details page."""
        mock_get_artist.return_value = {
            "name": "Fetched Artist",
            "sort_name": "Artist, Fetched",
            "country": "US",
            "genres": [{"name": "rock"}],
        }
        mock_sync_artist_discography.return_value = 0

        response = self.client.get(
            reverse("create_artist_from_search", args=["artist-mbid"]),
        )

        artist = Artist.objects.get(musicbrainz_id="artist-mbid")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "fetched-artist",
                },
            ),
        )

    @patch("app.providers.musicbrainz.get_release")
    def test_create_album_from_search_redirects_to_canonical_music_details(
        self,
        mock_get_release,
    ):
        """Album search creates should redirect to the canonical shared details page."""
        mock_get_release.return_value = {
            "title": "Fetched Album",
            "artist_id": "artist-mbid",
            "artist_name": "Fetched Artist",
            "release_date": "2024-01-15",
            "image": "https://example.com/album.jpg",
            "genres": ["rock"],
        }

        response = self.client.get(
            reverse("create_album_from_search", args=["release-mbid"]),
        )

        album = Album.objects.get(musicbrainz_release_id="release-mbid")
        credit = AlbumArtist.objects.get(album=album)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(credit.artist, album.artist)
        self.assertEqual(credit.position, 0)
        self.assertEqual(
            response.url,
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": album.artist.id,
                    "artist_slug": "fetched-artist",
                    "album_id": album.id,
                    "album_slug": "fetched-album",
                },
            ),
        )

    @patch("app.providers.musicbrainz.get_release")
    def test_create_album_from_search_creates_structured_artist_credits(
        self,
        mock_get_release,
    ):
        """Album search creates individual artists for multi-artist releases."""
        mock_get_release.return_value = {
            "title": "Fetched Album",
            "artist_id": "artist-one-mbid",
            "artist_name": "Artist One & Artist Two",
            "artist_credits": [
                {
                    "artist_id": "artist-one-mbid",
                    "name": "Artist One",
                    "sort_name": "One, Artist",
                    "join_phrase": " & ",
                },
                {
                    "artist_id": "artist-two-mbid",
                    "name": "Artist Two",
                    "sort_name": "Two, Artist",
                    "join_phrase": "",
                },
            ],
            "release_date": "2024-01-15",
            "image": "https://example.com/album.jpg",
            "genres": ["rock"],
        }

        response = self.client.get(
            reverse("create_album_from_search", args=["release-mbid"]),
        )

        album = Album.objects.get(musicbrainz_release_id="release-mbid")
        credits = list(album.artist_credits.select_related("artist"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(album.artist.name, "Artist One")
        self.assertEqual(
            [credit.artist.name for credit in credits],
            ["Artist One", "Artist Two"],
        )
        self.assertEqual([credit.join_phrase for credit in credits], [" & ", ""])
        self.assertTrue(
            Artist.objects.filter(musicbrainz_id="artist-two-mbid").exists(),
        )

    @patch("app.providers.musicbrainz.get_release")
    def test_create_album_from_search_existing_album_with_credits(
        self,
        mock_get_release,
    ):
        """Revisiting an already-created album should not crash on artist_credits."""
        artist = Artist.objects.create(name="Artist One", musicbrainz_id="artist-one-mbid")
        album = Album.objects.create(
            title="Fetched Album",
            musicbrainz_release_id="release-mbid",
            artist=artist,
        )
        mock_get_release.return_value = {
            "title": "Fetched Album",
            "artist_id": "artist-one-mbid",
            "artist_name": "Artist One & Artist Two",
            "artist_credits": [
                {
                    "artist_id": "artist-one-mbid",
                    "name": "Artist One",
                    "join_phrase": " & ",
                },
                {
                    "artist_id": "artist-two-mbid",
                    "name": "Artist Two",
                    "join_phrase": "",
                },
            ],
        }

        response = self.client.get(
            reverse("create_album_from_search", args=["release-mbid"]),
        )

        self.assertEqual(response.status_code, 302)
        credits = list(album.artist_credits.select_related("artist"))
        self.assertEqual(
            [credit.artist.name for credit in credits],
            ["Artist One", "Artist Two"],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_view_new_media(self, mock_get_metadata):
        """Test the track modal view for new media."""
        mock_get_metadata.return_value = {
            "media_id": "278",
            "title": "New Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "details": {
                "release_date": "2024-01-15",
                "runtime": "1h 35min",
            },
            "max_progress": 1,
        }

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "278",
                },
            )
            + "?return_url=/home&title=New+Movie",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        self.assertIn("form", response.context)
        self.assertEqual(response.context["form"].initial["media_id"], "278")
        self.assertEqual(
            response.context["form"].initial["media_type"],
            MediaTypes.MOVIE.value,
        )
        self.assertEqual(
            response.context["form"].initial["image_url"],
            "http://example.com/image.jpg",
        )
        self.assertContains(
            response,
            "Save this image from the General tab when you add or update the entry.",
        )
        content = response.content.decode()
        self.assertIn("2024-01-15", content)
        self.assertIn('"suggestionRuntimeMinutes": "95"', content)
        self.assert_release_shortcut_labels(response)
        self.assertNotContains(response, "Save Image")

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_untracked_movie_defaults_to_planning(self, mock_get_metadata):
        """An untracked movie opens as Planning, released or not (#1305)."""
        for media_id, release_date in (("901", "2099-06-01"), ("902", "2001-01-15")):
            with self.subTest(release_date=release_date):
                mock_get_metadata.return_value = {
                    "media_id": media_id,
                    "title": "New Movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/image.jpg",
                    "details": {"release_date": release_date},
                    "max_progress": 1,
                }

                response = self.client.get(
                    reverse(
                        "track_modal",
                        kwargs={
                            "source": Sources.TMDB.value,
                            "media_type": MediaTypes.MOVIE.value,
                            "media_id": media_id,
                        },
                    ),
                )

                self.assertEqual(response.status_code, 200)
                form = response.context["form"]
                self.assertEqual(form.initial["status"], Status.PLANNING.value)
                self.assertContains(
                    response,
                    '<option value="Planning" selected>',
                    html=False,
                )

    def test_track_modal_tracked_movie_keeps_its_status(self):
        """A tracked movie still shows its saved status, not the new-entry default."""
        Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"]["status"].value(),
            Status.COMPLETED.value,
        )

    def test_create_entry_form_defaults_to_planning(self):
        """The manual add-entry form pre-selects Planning."""
        response = self.client.get(reverse("create_entry"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<option value="Planning" selected>', html=False)
        self.assertNotContains(response, '<option value="Completed" selected>')

    def test_update_item_image(self):
        """Existing tracked items should allow image overrides from metadata."""
        response = self.client.post(
            reverse("update_item_image", args=[self.item.id]),
            {
                "image_url": "https://images.example.com/updated-poster.jpg",
                "return_url": "/home",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/home")

        self.item.refresh_from_db()
        self.assertEqual(
            self.item.image,
            "https://images.example.com/updated-poster.jpg",
        )

    def test_track_modal_renders_custom_metadata_form_for_manual_movie(self):
        """Manual/custom items should expose the full metadata editor."""
        manual_item = Item.objects.create(
            media_id="manual-movie-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual Movie",
            image="https://example.com/manual-movie.jpg",
        )
        Movie.objects.create(
            item=manual_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.MANUAL.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "manual-movie-1",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["metadata_tab_available"])
        self.assertTrue(response.context["can_update_metadata_provider"])
        self.assertTrue(response.context["can_edit_custom_metadata"])
        self.assertIsNotNone(response.context["manual_metadata_form"])
        self.assertEqual(
            list(response.context["manual_metadata_form"].fields.keys())[:6],
            [
                "title",
                "original_title",
                "localized_title",
                "image_url",
                "synopsis",
                "genres",
            ],
        )
        self.assertContains(response, "Custom Metadata")
        self.assertContains(response, "Metadata Provider")
        self.assertContains(response, "Display metadata is currently coming from")
        self.assertContains(response, "Custom")
        self.assertContains(response, "Release Date")
        self.assertContains(response, "Runtime")
        self.assertContains(response, "Save Metadata")
        self.assertNotContains(response, "Save Image")

    def test_track_modal_renders_custom_metadata_form_for_manual_comic_issue(self):
        """A hand-made comic issue gets the same metadata editor as its series."""
        manual_item = Item.objects.create(
            media_id="manual-issue-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Manual Issue #1",
        )
        ComicIssue.objects.create(
            item=manual_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.MANUAL.value,
                    "media_type": MediaTypes.COMIC_ISSUE.value,
                    "media_id": "manual-issue-1",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["metadata_tab_available"])
        self.assertTrue(response.context["can_edit_custom_metadata"])
        form = response.context["manual_metadata_form"]
        self.assertIsNotNone(form)
        self.assertIn("publishers", form.fields)
        self.assertNotIn("creators", form.fields)
        self.assertContains(response, "Custom Metadata")
        self.assertContains(response, "Metadata Provider")

    def test_track_modal_renders_custom_metadata_form_for_movie_using_custom_provider(
        self,
    ):
        """Tracked items should show the custom editor when Custom is selected."""
        self.item.manual_metadata = {
            "title": "Custom Display Movie",
            "original_title": "Custom Original",
            "localized_title": "Custom Localized",
            "image": "https://example.com/custom-display-movie.jpg",
            "synopsis": "A custom display synopsis.",
            "genres": ["Drama"],
            "details": {
                "release_date": "2024-02-01",
                "runtime": "2h 1min",
            },
        }
        self.item.save(update_fields=["manual_metadata"])
        MetadataProviderPreference.objects.create(
            user=self.user,
            item=self.item,
            provider=Sources.MANUAL.value,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_update_metadata_provider"])
        self.assertTrue(response.context["can_edit_custom_metadata"])
        self.assertEqual(response.context["display_provider"], Sources.MANUAL.value)
        self.assertContains(response, "Metadata Provider")
        self.assertContains(response, "Custom Metadata")
        self.assertContains(response, "Save Metadata")
        self.assertNotContains(response, "Save Image")

    def test_update_manual_item_metadata(self):
        """Manual/custom metadata edits should persist on the underlying item."""
        manual_item = Item.objects.create(
            media_id="manual-movie-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Original Manual Movie",
            image="https://example.com/original-manual-movie.jpg",
        )
        Movie.objects.create(
            item=manual_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        response = self.client.post(
            reverse("update_manual_item_metadata", args=[manual_item.id]),
            {
                "return_url": "/home",
                "metadata-title": "Updated Manual Movie",
                "metadata-original_title": "Original Language Title",
                "metadata-localized_title": "Localized Manual Movie",
                "metadata-image_url": "https://images.example.com/manual-custom-poster.jpg",
                "metadata-synopsis": "A custom movie synopsis.",
                "metadata-genres": "Drama\nThriller",
                "metadata-release_date": "2024-01-15",
                "metadata-status": "Released",
                "metadata-runtime": "2h 10min",
                "metadata-studios": "Studio One, Studio Two",
                "metadata-country": "Japan",
                "metadata-languages": "Japanese\nEnglish",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/home")

        manual_item.refresh_from_db()
        self.assertEqual(manual_item.title, "Updated Manual Movie")
        self.assertEqual(manual_item.original_title, "Original Language Title")
        self.assertEqual(manual_item.localized_title, "Localized Manual Movie")
        self.assertEqual(
            manual_item.image,
            "https://images.example.com/manual-custom-poster.jpg",
        )
        self.assertEqual(manual_item.genres, ["Drama", "Thriller"])
        self.assertEqual(manual_item.studios, ["Studio One", "Studio Two"])
        self.assertEqual(manual_item.country, "Japan")
        self.assertEqual(manual_item.languages, ["Japanese", "English"])
        self.assertEqual(manual_item.runtime, "2h 10min")
        self.assertEqual(manual_item.runtime_minutes, 130)
        self.assertEqual(
            manual_item.release_datetime.date().isoformat(),
            "2024-01-15",
        )
        self.assertEqual(
            manual_item.manual_metadata["synopsis"], "A custom movie synopsis."
        )
        self.assertEqual(
            manual_item.manual_metadata["details"]["release_date"],
            "2024-01-15",
        )

    def test_update_manual_item_metadata_for_collected_only_comic_issue(self):
        """An issue that is only in the collection can still get its metadata."""
        manual_item = Item.objects.create(
            media_id="manual-issue-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Collected Issue",
        )
        CollectionEntry.objects.create(user=self.user, item=manual_item)

        response = self.client.post(
            reverse("update_manual_item_metadata", args=[manual_item.id]),
            {
                "return_url": "/home",
                "metadata-title": "Collected Issue #2",
                "metadata-synopsis": "Found in a long box.",
                "metadata-publishers": "Nova Press",
                "metadata-release_date": "2024-03-06",
            },
        )

        self.assertEqual(response.status_code, 302)
        manual_item.refresh_from_db()
        self.assertEqual(manual_item.title, "Collected Issue #2")
        self.assertEqual(
            manual_item.manual_metadata["synopsis"], "Found in a long box."
        )
        self.assertEqual(manual_item.publishers, "Nova Press")

    def test_update_manual_item_metadata_rejects_item_not_in_library(self):
        """Neither tracking nor collecting means no edits."""
        manual_item = Item.objects.create(
            media_id="manual-issue-3",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Someone Else's Issue",
        )

        self.client.post(
            reverse("update_manual_item_metadata", args=[manual_item.id]),
            {"return_url": "/home", "metadata-title": "Hijacked"},
        )

        manual_item.refresh_from_db()
        self.assertEqual(manual_item.title, "Someone Else's Issue")

    def test_update_item_image_for_collected_only_item(self):
        """The image URL box also works for a collected-only item."""
        manual_item = Item.objects.create(
            media_id="manual-issue-4",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Collected Cover",
        )
        CollectionEntry.objects.create(user=self.user, item=manual_item)

        self.client.post(
            reverse("update_item_image", args=[manual_item.id]),
            {"return_url": "/home", "image_url": "https://images.example.com/c.jpg"},
        )

        manual_item.refresh_from_db()
        self.assertEqual(manual_item.image, "https://images.example.com/c.jpg")

    def test_update_manual_item_metadata_redirects_to_normalized_return_url(self):
        """Custom metadata saves should restore encoded list query separators."""
        MetadataProviderPreference.objects.create(
            user=self.user,
            item=self.item,
            provider=Sources.MANUAL.value,
        )

        response = self.client.post(
            reverse("update_manual_item_metadata", args=[self.item.id]),
            {
                "return_url": (
                    "/medialist/movie%3Fstatus%3DPlanning&sort%3Drelease_date"
                    "&direction%3Ddesc&layout%3Dgrid"
                ),
                "metadata-title": "Updated Test Movie",
                "metadata-original_title": "",
                "metadata-localized_title": "",
                "metadata-image_url": "https://images.example.com/updated-test-movie.jpg",
                "metadata-synopsis": "",
                "metadata-genres": "",
                "metadata-release_date": "",
                "metadata-status": "",
                "metadata-runtime": "",
                "metadata-studios": "",
                "metadata-country": "",
                "metadata-languages": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            "/medialist/movie?status=Planning&sort=release_date&direction=desc&layout=grid",
        )

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_renders_metadata_sidebar_for_anime(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        """Anime tracking modal should expose a separate metadata tab."""
        anime_item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
        )
        base_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "original_title": "Sousou no Frieren",
            "localized_title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "image": "https://example.com/frieren.jpg",
            "max_progress": 28,
            "details": {"episodes": 28},
            "related": {},
        }
        mock_get_metadata.return_value = base_metadata
        anime = Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=12,
        )
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TVDB.value,
            identity_provider=Sources.MAL.value,
            mapping_status="mapped",
            header_metadata=base_metadata,
            grouped_preview={
                "media_id": "9350138",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.ANIME.value,
                "title": "Frieren: Beyond Journey's End",
                "related": {
                    "seasons": [
                        {
                            "season_number": 1,
                            "episode_count": 28,
                            "is_mapped_target": True,
                            "mapped_episode_start": 1,
                            "mapped_episode_end": 28,
                        },
                    ],
                },
            },
            provider_media_id="9350138",
            grouped_preview_target={
                "season_number": 1,
                "season_title": "Season 1",
                "episode_start": 1,
                "episode_end": 28,
            },
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                },
            )
            + f"?instance_id={anime.id}&return_url=/details/mal/anime/52991/frieren",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")
        self.assertTrue(response.context["metadata_tab_available"])
        self.assertContains(response, "General")
        self.assertContains(response, "Metadata")
        self.assertContains(response, "Metadata Provider")
        self.assertContains(response, "Convert to Grouped Series")
        self.assertContains(response, "This MAL entry would convert to")
        self.assertContains(response, "Conversion target")

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_renders_episode_plays_tab_for_tv(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        tv_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="https://example.com/breaking-bad.jpg",
        )
        TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        tv_payload = {
            "media_id": "1396",
            "title": "Breaking Bad",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "https://example.com/breaking-bad.jpg",
            "details": {"episodes": 3},
            "related": {
                "seasons": [
                    {"season_number": 1, "season_title": "Season 1"},
                ],
            },
        }
        mock_get_metadata.side_effect = lambda media_type, *_args, **_kwargs: (
            _tv_with_seasons_payload("1396", Sources.TMDB.value, title="Breaking Bad")
            if media_type == "tv_with_seasons"
            else tv_payload
        )
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TMDB.value,
            identity_provider=Sources.TMDB.value,
            mapping_status="identity",
            header_metadata=tv_payload,
            grouped_preview=None,
            provider_media_id="1396",
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1396",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Episode Plays")
        self.assertContains(response, "Episode ordering")
        self.assertEqual(
            response.context["episode_plays_form"].initial["first_episode_number"],
            1,
        )
        self.assertEqual(
            response.context["episode_plays_form"].initial["last_episode_number"],
            3,
        )
        self.assertEqual(
            response.context["episode_plays_form"]["distribution_mode"].value(),
            "air_date",
        )
        # Split per #1243: each date/time picker's Start Now / Just Finished
        # / Release Date shortcuts are scoped to their own field via
        # quick_action_mode, on both the General tab's start/end pickers and
        # the Episode Plays bulk-range start/end pickers.
        self.assertIn("Release Date", response.content.decode())
        self.assertEqual(
            response.context["episode_plays_domain"]["seasonEpisodeMap"]["1"][0][
                "runtime_minutes"
            ],
            24,
        )
        presentation = response.context["episode_plays_presentation"]
        self.assertEqual(
            presentation["formId"],
            f"{response.context['track_form_id']}-episode-plays-form",
        )
        self.assertEqual(presentation["action"], reverse("episode_bulk_save"))
        self.assertEqual(
            presentation["values"]["first_episode_number"],
            "1",
        )
        self.assertTrue(presentation["domain"]["seasonEpisodeMap"])
        self.assertContains(response, "data-bulk-episode-form-root")
        self.assertContains(response, presentation["formId"])

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_season_track_modal_scopes_episode_plays_to_season(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        payload = _tv_with_seasons_payload("1396", Sources.TMDB.value)
        season_two = dict(payload["related"]["seasons"][0], season_number=2)
        payload["related"]["seasons"].append(season_two)
        payload["season/2"] = dict(payload["season/1"], season_number=2)
        mock_get_metadata.side_effect = lambda media_type, *_args, **_kwargs: (
            payload["season/2"] if media_type == "season" else payload
        )
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TMDB.value,
            identity_provider=Sources.TMDB.value,
            mapping_status="identity",
            header_metadata=payload,
            grouped_preview=None,
            provider_media_id="1396",
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.SEASON.value,
                    "media_id": "1396",
                    "season_number": 2,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Episode Plays")
        domain = response.context["episode_plays_domain"]
        self.assertEqual(list(domain["seasonEpisodeMap"]), ["2"])
        self.assertEqual(domain["lockedSeasonNumber"], 2)
        form = response.context["episode_plays_form"]
        self.assertEqual(form.initial["media_type"], MediaTypes.TV.value)
        self.assertEqual(form.initial["first_season_number"], 2)
        self.assertEqual(form.initial["last_season_number"], 2)

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_defaults_first_episode_to_season_one_over_specials(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        tv_payload = {
            "media_id": "1396",
            "title": "Breaking Bad",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "https://example.com/breaking-bad.jpg",
            "details": {"episodes": 3},
            "related": {
                "seasons": [
                    {"season_number": 0, "season_title": "Specials"},
                    {"season_number": 1, "season_title": "Season 1"},
                ],
            },
        }
        tv_with_seasons = {
            **tv_payload,
            "season/0": {
                "season_number": 0,
                "season_title": "Specials",
                "title": "Breaking Bad",
                "image": "https://example.com/specials.jpg",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Special 1",
                        "air_date": "2023-12-01",
                        "runtime": 24,
                    },
                ],
            },
            "season/1": {
                "season_number": 1,
                "season_title": "Season 1",
                "title": "Breaking Bad",
                "image": "https://example.com/season1.jpg",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Episode 1",
                        "air_date": "2024-01-01",
                        "runtime": 24,
                    },
                    {
                        "episode_number": 2,
                        "name": "Episode 2",
                        "air_date": "2024-01-02",
                        "runtime": 24,
                    },
                ],
            },
        }
        mock_get_metadata.side_effect = lambda media_type, *_args, **_kwargs: (
            tv_with_seasons if media_type == "tv_with_seasons" else tv_payload
        )
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TMDB.value,
            identity_provider=Sources.TMDB.value,
            mapping_status="identity",
            header_metadata=tv_payload,
            grouped_preview=None,
            provider_media_id="1396",
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1396",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["episode_plays_form"].initial["first_season_number"],
            1,
        )
        self.assertEqual(
            response.context["episode_plays_form"].initial["first_episode_number"],
            1,
        )

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_renders_episode_plays_tab_for_grouped_anime(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        anime_item = Item.objects.create(
            media_id="9350138",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Frieren: Beyond Journey's End",
            image="https://example.com/frieren.jpg",
        )
        TV.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        anime_payload = {
            "media_id": "9350138",
            "title": "Frieren: Beyond Journey's End",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.TVDB.value,
            "image": "https://example.com/frieren.jpg",
            "details": {"episodes": 3},
            "related": {
                "seasons": [
                    {"season_number": 1, "season_title": "Season 1"},
                ],
            },
            "library_media_type": MediaTypes.ANIME.value,
            "identity_media_type": MediaTypes.TV.value,
        }
        mock_get_metadata.side_effect = lambda media_type, *_args, **_kwargs: (
            _tv_with_seasons_payload(
                "9350138",
                Sources.TVDB.value,
                title="Frieren: Beyond Journey's End",
            )
            if media_type == "tv_with_seasons"
            else anime_payload
        )
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TVDB.value,
            identity_provider=Sources.TVDB.value,
            mapping_status="identity",
            header_metadata=anime_payload,
            grouped_preview=None,
            provider_media_id="9350138",
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "9350138",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Episode Plays")
        self.assertEqual(
            response.context["episode_plays_form"].initial["library_media_type"],
            MediaTypes.ANIME.value,
        )

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_renders_mapped_episode_slice_for_flat_anime(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        mock_get_metadata.return_value = {"max_progress": 24}
        anime_item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=12,
        )
        base_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "image": "https://example.com/frieren.jpg",
            "details": {"episodes": 12},
            "related": {},
        }
        grouped_preview = _tv_with_seasons_payload(
            "9350138",
            Sources.TVDB.value,
            title="Frieren: Beyond Journey's End",
            episode_count=24,
        )
        mock_get_metadata.return_value = base_metadata
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TVDB.value,
            identity_provider=Sources.MAL.value,
            mapping_status="mapped",
            header_metadata=base_metadata,
            grouped_preview=grouped_preview,
            provider_media_id="9350138",
            grouped_preview_target={
                "season_number": 1,
                "season_title": "Season 1",
                "episode_start": 13,
                "episode_end": 24,
            },
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Episode Plays")
        self.assertContains(
            response,
            "This will migrate your MAL anime entry into grouped episode "
            "tracking before logging plays.",
        )
        self.assertEqual(
            response.context["episode_plays_form"].initial["first_episode_number"],
            13,
        )
        self.assertEqual(
            response.context["episode_plays_form"].initial["last_episode_number"],
            24,
        )

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_hides_episode_plays_tab_for_unmapped_flat_anime(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        base_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "image": "https://example.com/frieren.jpg",
            "details": {"episodes": 12},
            "related": {},
        }
        mock_get_metadata.return_value = base_metadata
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.MAL.value,
            identity_provider=Sources.MAL.value,
            mapping_status="identity",
            header_metadata=base_metadata,
            grouped_preview=None,
            provider_media_id="52991",
            grouped_preview_target=None,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Episode Plays")
        self.assertFalse(response.context["episode_plays_tab_available"])


class PodcastTrackModalViewTests(TestCase):
    """Podcast-specific track modal behavior."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_podcast_track_modal_shows_delete_for_in_progress_play(self):
        """Podcast episode modal should allow deleting an in-progress play."""
        show = PodcastShow.objects.create(
            podcast_uuid="show-uuid-1",
            title="Show Title",
            image="http://example.com/show.jpg",
        )
        episode = PodcastEpisode.objects.create(
            show=show,
            episode_uuid="episode-uuid-1",
            title="Episode Title",
            duration=1577,
        )
        item = Item.objects.create(
            media_id=episode.episode_uuid,
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title=episode.title,
            image=show.image,
        )
        podcast = Podcast.objects.create(
            item=item,
            user=self.user,
            show=show,
            episode=episode,
            status=Status.IN_PROGRESS.value,
            progress=10,
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": episode.episode_uuid,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track_song.html")
        self.assertContains(response, "In-Progress Play")
        self.assertContains(
            response,
            f'name="instance_id" value="{podcast.id}"',
            html=False,
        )
        self.assertContains(response, 'name="media_type" value="podcast"', html=False)
        self.assertContains(response, "data-track-form-root")
        self.assertContains(response, "data-track-form-legacy")
        self.assertContains(response, '"variant": "song"')
        self.assertContains(response, f'"episode_uuid": "{episode.episode_uuid}"')

    def test_podcast_track_modal_can_force_standard_editor(self):
        """History cards should be able to request the full shared editor for podcast plays."""
        show = PodcastShow.objects.create(
            podcast_uuid="show-uuid-2",
            title="Show Title",
            image="http://example.com/show.jpg",
        )
        episode = PodcastEpisode.objects.create(
            show=show,
            episode_uuid="episode-uuid-2",
            title="Episode Title",
            duration=1577,
        )
        item = Item.objects.create(
            media_id=episode.episode_uuid,
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title=episode.title,
            image=show.image,
        )
        podcast = Podcast.objects.create(
            item=item,
            user=self.user,
            show=show,
            episode=episode,
            status=Status.COMPLETED.value,
            progress=1800,
            score=8,
            notes="Needs a revisit",
        )

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": episode.episode_uuid,
                },
            )
            + "?standard_modal=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")
        self.assertEqual(response.context["media"], podcast)
        self.assertContains(response, "General")
        self.assertContains(response, 'name="notes"', html=False)
        self.assertContains(response, 'name="score"', html=False)

    @patch("app.services.podcast_import.import_show_from_itunes_id")
    def test_podcast_track_modal_resolves_itunes_search_result(self, mock_import):
        """A podcast search result's iTunes id opens the show editor."""
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1532744254",
            title="Sternstunde Philosophie",
            image="http://example.com/show.jpg",
            rss_feed_url="",
        )
        mock_import.return_value = show

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": "1532744254",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")
        self.assertContains(response, "Sternstunde Philosophie")
        self.assertContains(response, 'name="show_id"', html=False)
        mock_import.assert_called_once_with("1532744254")

    def test_podcast_show_track_modal_renders_episode_plays_tab(self):
        """Podcast show modal should expose bulk episode plays instead of mark-all CTA."""
        show = PodcastShow.objects.create(
            podcast_uuid="show-uuid-2",
            title="Show Title",
            image="http://example.com/show.jpg",
        )
        PodcastShowTracker.objects.create(
            user=self.user,
            show=show,
            status=Status.IN_PROGRESS.value,
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="episode-uuid-2",
            title="Episode One",
            published=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            duration=1200,
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="episode-uuid-3",
            title="Episode Two",
            published=datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
            duration=1500,
        )

        response = self.client.get(
            reverse("podcast_show_track_modal", kwargs={"show_id": show.id})
            + "?return_url=/details/pocketcasts/podcast/show-uuid-2/show-title",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")
        self.assertContains(response, "General")
        self.assertContains(response, "Episode Plays")
        self.assertNotContains(response, "Metadata")
        self.assertNotContains(response, "Mark All Played")
        self.assertContains(response, 'name="show_id"', html=False)
        self.assertEqual(
            response.context["episode_plays_form"].initial["first_episode_number"],
            1,
        )
        self.assertEqual(
            response.context["episode_plays_form"].initial["last_episode_number"],
            2,
        )
        self.assertTrue(response.context["episode_plays_domain"]["hideSeasonSelectors"])


class EpisodeTrackButtonTests(TestCase):
    """The episode track buttons must edit an existing watch, not stack a new one."""

    def render_button(self, template_name, context):
        """Render one of the two episode track button variants."""
        request = RequestFactory().get("/details/tmdb/tv/1668/show/season/1/episode/2")
        return render_to_string(template_name, context, request=request)

    def test_hero_button_creates_without_history(self):
        """With nothing watched the hero button opens the modal in create mode."""
        markup = self.render_button(
            "app/components/detail_episode_hero_track_button.html",
            {
                "episode": {"history": []},
                "source": Sources.TMDB.value,
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 2,
            },
        )

        self.assertIn('"is_create": "1"', markup)
        self.assertNotIn("instance_id", markup)
        self.assertIn("Mark Watched", markup)

    def test_hero_button_edits_latest_watch(self):
        """With a watch on record the hero button binds it so the fields prefill."""
        markup = self.render_button(
            "app/components/detail_episode_hero_track_button.html",
            {
                "episode": {"history": [SimpleNamespace(id=42)]},
                "source": Sources.TMDB.value,
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 2,
            },
        )

        self.assertIn('"instance_id": "42"', markup)
        self.assertNotIn("is_create", markup)
        self.assertIn("Watched", markup)

    def test_row_button_edits_latest_watch(self):
        """The season-page episode row follows the same rule as the hero button."""
        markup = self.render_button(
            "app/components/detail_episode_track_button.html",
            {
                "episode": SimpleNamespace(
                    history=[SimpleNamespace(id=7)],
                    episode_number=2,
                    item=SimpleNamespace(id=99),
                ),
            },
        )

        self.assertIn('"instance_id": "7"', markup)
        self.assertNotIn("is_create", markup)


class ProxyCoverSaveTests(TestCase):
    """Books whose cover is a Floppy proxy path can still be edited (#1316)."""

    def setUp(self):
        """Log in a user tracking an audiobook."""
        self.credentials = {"username": "proxy", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def _book(self, source, image):
        item = Item.objects.create(
            media_id=f"{source}-1",
            source=source,
            media_type=MediaTypes.BOOK.value,
            title="Project Hail Mary",
            image=image,
            format="audiobook",
            runtime_minutes=960,
        )
        return Book.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=300,
        )

    def _save_status_from_modal(self, book, status):
        """Post the modal's own initial values back with a new status."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": book.item.source,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": book.item.media_id,
                },
            )
            + f"?instance_id={book.id}",
        )
        form = response.context["form"]
        self.assertNotIn("image_url", form.initial)
        data = {
            name: form.initial.get(name, field.initial) or ""
            for name, field in form.fields.items()
        }
        data.update(
            {
                "instance_id": book.id,
                "status": status,
                "start_date": "",
                "end_date": "",
            },
        )
        self.client.post(reverse("media_save"), data)
        book.refresh_from_db()
        return book

    def test_audiobookshelf_book_status_saves(self):
        """The ABS cover proxy path no longer blocks the save."""
        book = self._book(
            Sources.AUDIOBOOKSHELF.value,
            "/import/audiobookshelf/cover/MTppdGVtLTE=:sig",
        )

        book = self._save_status_from_modal(book, Status.PAUSED.value)

        self.assertEqual(book.status, Status.PAUSED.value)
        self.assertEqual(
            book.item.image,
            "/import/audiobookshelf/cover/MTppdGVtLTE=:sig",
        )

    def test_plex_book_status_saves(self):
        """Plex covers use the same kind of proxy path."""
        book = self._book(Sources.PLEX.value, "/import/plex/cover/abc:sig")

        book = self._save_status_from_modal(book, Status.DROPPED.value)

        self.assertEqual(book.status, Status.DROPPED.value)
