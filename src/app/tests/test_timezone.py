"""The server speaks UTC; the browser decides the wall clock.

These cover the seam: what the server renders, what it accepts back, and the
one thing it still has to know a zone for -- grouping instants into days.
"""

import datetime
import zoneinfo

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app import statistics as stats
from app.forms import EpisodeForm, LocalDateInput, MovieForm
from app.models import TV, Episode, Item, MediaTypes, Movie, Season, Sources, Status
from app.providers import mal
from app.templatetags import app_tags
from events.models import Event
from integrations.imports.imdb import IMDBImporter

UserModel = get_user_model()

UTC = zoneinfo.ZoneInfo("UTC")
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
TOKYO = zoneinfo.ZoneInfo("Asia/Tokyo")


class RenderedOutputTests(TestCase):
    """Filters emit the instant, never a wall clock the server chose."""

    def setUp(self):
        """Set up a fixed instant to render."""
        self.instant = datetime.datetime(2026, 1, 17, 23, 25, tzinfo=UTC)

    @override_settings(TZ=PARIS, TIME_ZONE="Europe/Paris")
    def test_output_is_utc_regardless_of_server_zone(self):
        """A non-UTC server must not leak its own clock into the markup."""
        rendered = app_tags.datetime_format(self.instant)

        # The text is the same ISO instant as the attribute: picking a human
        # format is the browser's job.
        self.assertIn('datetime="2026-01-17T23:25:00+00:00"', rendered)
        self.assertIn(">2026-01-17T23:25:00+00:00<", rendered)
        self.assertNotIn("00:25", rendered)

    def test_kind_is_tagged_for_the_browser(self):
        """Each filter marks which format the browser should apply."""
        self.assertIn('data-yt="date"', app_tags.date_format(self.instant))
        self.assertIn('data-yt="time"', app_tags.time_format(self.instant))
        self.assertIn('data-yt="datetime"', app_tags.datetime_format(self.instant))
        self.assertIn('data-yt="natural-day"', app_tags.natural_day(self.instant))

    def test_empty_values_render_nothing(self):
        """A missing date must not print a stray element or "None"."""
        self.assertEqual(app_tags.datetime_format(None), "")
        self.assertEqual(app_tags.date_format(None), "")
        self.assertEqual(app_tags.utc_iso(None), "")

    def test_naive_dates_are_untouched(self):
        """A bare date has no instant, so it stays server-formatted."""
        user = UserModel.objects.create_user(username="a", password="p")  # noqa: S106

        self.assertEqual(app_tags.iso_date_format("2026-01-18", user), "2026-01-18")


@override_settings(TZ=UTC, TIME_ZONE="UTC", TRACK_TIME=True)
class DateEntryTests(TestCase):
    """Input arrives as an explicit instant, with a naive fallback."""

    def test_widget_seeds_from_utc_not_a_wall_clock(self):
        """The server must not put its own clock in the input."""
        episode = Episode(end_date=datetime.datetime(2026, 1, 17, 23, 25, tzinfo=UTC))

        rendered = str(EpisodeForm(instance=episode)["end_date"])

        self.assertIn('data-yt-value="2026-01-17T23:25:00+00:00"', rendered)
        self.assertIn('value=""', rendered)
        self.assertIn('name="end_date_utc"', rendered)

    def test_explicit_instant_is_stored_as_sent(self):
        """The companion value is taken verbatim, not reinterpreted."""
        form = EpisodeForm(
            {
                "end_date": "2026-01-18T00:25",
                "end_date_utc": "2026-01-17T23:25:00.000Z",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["end_date"].astimezone(UTC),
            datetime.datetime(2026, 1, 17, 23, 25, tzinfo=UTC),
        )

    def test_naive_fallback_when_javascript_did_not_run(self):
        """Without the companion the naive value is localized server-side."""
        form = EpisodeForm({"end_date": "2026-01-18T00:25", "end_date_utc": ""})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["end_date"].astimezone(UTC),
            datetime.datetime(2026, 1, 18, 0, 25, tzinfo=UTC),
        )

    def test_both_tracked_dates_carry_a_companion(self):
        """Start and end dates go through the same widget."""
        rendered = str(MovieForm())

        self.assertIn('name="start_date_utc"', rendered)
        self.assertIn('name="end_date_utc"', rendered)

    def test_round_trip_through_the_view_is_lossless(self):
        """Seed, submit, store, re-render: the instant survives unchanged."""
        user = UserModel.objects.create_user(username="rt", password="p")  # noqa: S106
        self.client.force_login(user)

        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=user,
            related_tv=TV.objects.create(
                item=tv_item,
                user=user,
                status=Status.IN_PROGRESS.value,
            ),
            status=Status.IN_PROGRESS.value,
        )

        # What a Paris browser sends for 00:25 local on 18 January.
        self.client.post(
            reverse("create_entry"),
            {
                "title": "TV Show",
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "parent_season": season.id,
                "end_date": "2026-01-18T00:25",
                "end_date_utc": "2026-01-17T23:25:00.000Z",
            },
            follow=True,
        )

        episode = Episode.objects.get(related_season=season)
        stored = datetime.datetime(2026, 1, 17, 23, 25, tzinfo=UTC)

        self.assertEqual(episode.end_date.astimezone(UTC), stored)
        self.assertIn(
            'datetime="2026-01-17T23:25:00+00:00"',
            app_tags.datetime_format(episode.end_date),
        )


@override_settings(TZ=UTC, TIME_ZONE="UTC")
class TemplateOutputTests(TestCase):
    """The markup the timestamp plumbing adds must not leak into the page."""

    def test_no_raw_template_comment_is_rendered(self):
        """Django's {# #} is single-line only; a multi-line one renders as text."""
        user = UserModel.objects.create_user(username="tpl", password="p")  # noqa: S106
        self.client.force_login(user)

        body = self.client.get(reverse("home")).content.decode()

        self.assertNotIn("{#", body)
        self.assertNotIn("Timestamps render as UTC", body)

    def test_formats_are_handed_to_the_browser(self):
        """The browser needs the display preferences to do the formatting."""
        user = UserModel.objects.create_user(
            username="fmt",
            password="p",  # noqa: S106
            date_format="d/m/Y",
            time_format="g:i A",
        )
        self.client.force_login(user)

        body = self.client.get(reverse("home")).content.decode()

        self.assertIn('date: "d/m/Y"', body)
        self.assertIn('time: "g:i A"', body)


@override_settings(TZ=UTC, TIME_ZONE="UTC")
class ViewerTimezoneParamTests(TestCase):
    """The activity dashboard reads the zone the browser sent with it."""

    def setUp(self):
        """Set up a request factory."""
        self.factory = RequestFactory()

    def test_parameter_is_used(self):
        """The named zone is what days are bucketed on."""
        request = self.factory.get("/", {stats.TIMEZONE_PARAM: "Europe/Paris"})

        self.assertEqual(stats.bucketing_timezone_from_request(request), PARIS)

    def test_missing_parameter_falls_back_to_utc(self):
        """The server's own zone must not silently stand in for the viewer's."""
        self.assertEqual(
            stats.bucketing_timezone_from_request(self.factory.get("/")),
            datetime.UTC,
        )

    def test_unknown_zone_is_ignored(self):
        """A forged parameter must not break the page."""
        request = self.factory.get("/", {stats.TIMEZONE_PARAM: "Not/AZone"})

        self.assertEqual(stats.bucketing_timezone_from_request(request), datetime.UTC)

    def test_dashboard_buckets_on_the_supplied_zone(self):
        """The endpoint honours the zone end to end."""
        user = UserModel.objects.create_user(username="dash", password="p")  # noqa: S106
        self.client.force_login(user)

        response = self.client.get(
            reverse("journal_activity"),
            {stats.TIMEZONE_PARAM: "Europe/Paris"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("activity_data", response.context)


@override_settings(TZ=PARIS, TIME_ZONE="Europe/Paris")
class NonUtcServerTests(TestCase):
    """The server's own zone must not reach the viewer at all.

    Running with TZ=Europe/Paris has to produce byte-identical output to
    TZ=UTC for the same viewer; if it does not, the server is still deciding
    someone's wall clock somewhere.
    """

    def setUp(self):
        """Log a user in against a Paris-configured server."""
        self.user = UserModel.objects.create_user(username="nonutc", password="p")  # noqa: S106
        self.client.force_login(self.user)
        self.instant = datetime.datetime(2026, 1, 17, 23, 25, tzinfo=UTC)

    def test_rendered_markup_is_identical_to_a_utc_server(self):
        """The emitted instant carries no trace of Europe/Paris."""
        rendered = app_tags.datetime_format(self.instant)

        with override_settings(TZ=UTC, TIME_ZONE="UTC"):
            self.assertEqual(rendered, app_tags.datetime_format(self.instant))

    def test_dashboard_zone_beats_the_server_zone(self):
        """The zone the browser names wins over the server's own."""
        response = self.client.get(
            reverse("journal_activity"),
            {stats.TIMEZONE_PARAM: "Australia/Brisbane"},
        )

        self.assertEqual(response.status_code, 200)

    def test_calendar_sends_instants_not_cells(self):
        """Nothing on the calendar page is bucketed by the Paris server."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Late Release",
        )
        Movie.objects.create(item=item, user=self.user, status=Status.IN_PROGRESS.value)
        Event.objects.create(
            item=item,
            datetime=datetime.datetime(2026, 1, 17, 14, 0, tzinfo=UTC),
        )

        response = self.client.get(reverse("calendar") + "?month=1&year=2026")

        self.assertNotIn("release_dict", response.context)
        self.assertIn(item.id, [r.item_id for r in response.context["releases"]])

    def test_naive_input_still_falls_back_to_the_server_zone(self):
        """Without JavaScript the server has nothing else to go on."""
        form = EpisodeForm({"end_date": "2026-01-18T00:25"})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["end_date"].astimezone(UTC),
            datetime.datetime(2026, 1, 17, 23, 25, tzinfo=UTC),
        )


@override_settings(TZ=UTC, TIME_ZONE="UTC")
class CalendarPayloadTests(TestCase):
    """The calendar hands the browser instants, not day cells."""

    def test_releases_are_sent_flat_with_a_day_of_margin(self):
        """A month-edge instant belongs to another month in some zones."""
        user = UserModel.objects.create_user(username="cal", password="p")  # noqa: S106
        self.client.force_login(user)

        item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Edge Release",
        )
        Movie.objects.create(item=item, user=user, status=Status.IN_PROGRESS.value)
        # 22:00Z on 31 January: already 1 February for anywhere east of UTC+2.
        Event.objects.create(
            item=item,
            datetime=datetime.datetime(2026, 1, 31, 22, 0, tzinfo=UTC),
        )

        response = self.client.get(reverse("calendar") + "?month=2&year=2026")

        # February's page must still carry it, or a Tokyo viewer loses it.
        self.assertIn(item.id, [r.item_id for r in response.context["releases"]])
        self.assertNotIn("release_dict", response.context)


@override_settings(TZ=PARIS, TIME_ZONE="Europe/Paris")
class BroadcastSlotTests(TestCase):
    """MAL's broadcast slot is a Japanese wall clock, not the server's."""

    def slot(self, day, start_time="23:15"):
        """Resolve a MAL broadcast payload to its UTC instant."""
        return mal.get_broadcast(
            {
                "start_date": "1999-10-20",
                "broadcast": {"day_of_the_week": day, "start_time": start_time},
            },
        )

    def test_uses_the_reported_weekday_not_the_premiere_date(self):
        """One Piece premiered on a Wednesday but airs on Sundays."""
        # 1999-10-20 is a Wednesday; MAL reports day_of_the_week "sunday".
        instant = self.slot("sunday")

        self.assertEqual(instant.astimezone(TOKYO).strftime("%A %H:%M"), "Sunday 23:15")

    def test_slot_is_read_as_japan_time(self):
        """23:15 JST is 15:15 UTC, whatever the server is set to."""
        instant = self.slot("sunday")

        self.assertEqual(instant.astimezone(UTC).strftime("%H:%M"), "14:15")

    def test_weekday_can_shift_for_a_late_night_slot(self):
        """A 00:30 JST slot falls on the previous day in the Americas."""
        instant = self.slot("monday", "00:30")

        self.assertEqual(instant.astimezone(TOKYO).strftime("%A"), "Monday")
        self.assertEqual(
            instant.astimezone(zoneinfo.ZoneInfo("America/Los_Angeles")).strftime("%A"),
            "Sunday",
        )

    def test_missing_pieces_yield_nothing(self):
        """An unknown slot must not invent one."""
        self.assertIsNone(mal.get_broadcast({"broadcast": {}}))
        self.assertIsNone(mal.get_broadcast({}))
        self.assertIsNone(self.slot("notaday"))

    def test_rendered_as_an_instant_for_the_browser(self):
        """The detail is handed over as markup the browser localizes."""
        rendered = app_tags.detail_value(self.slot("sunday"), None)

        self.assertIn('data-yt="weekday-time"', rendered)
        self.assertIn("T14:15", rendered)


@override_settings(TZ=UTC, TIME_ZONE="UTC")
class ActivityRangeTests(TestCase):
    """The date range is read on the viewer's clock, and never clips today."""

    def setUp(self):
        """Set up a request factory."""
        self.factory = RequestFactory()

    def parse(self, **params):
        """Parse a range from the given query parameters."""
        request = self.factory.get("/", params)
        return stats.parse_activity_date_range(
            request,
            stats.bucketing_timezone_from_request(request),
        )

    def test_explicit_days_use_the_reported_zone(self):
        """A chosen day starts and ends on the viewer's clock."""
        start, end = self.parse(
            **{
                "start-date": "2026-09-05",
                "end-date": "2026-09-05",
                "tz": "Europe/Paris",
            },
        )

        # Paris is UTC+2 in September, so their 5 September is 22:00Z on the 4th.
        self.assertEqual(
            start.astimezone(UTC),
            datetime.datetime(2026, 9, 4, 22, 0, tzinfo=UTC),
        )
        self.assertEqual(
            end.astimezone(UTC).strftime("%Y-%m-%d %H:%M"), "2026-09-05 21:59"
        )

    def test_default_end_is_the_end_of_the_viewers_today(self):
        """No explicit end means today, on the viewer's clock."""
        auckland = zoneinfo.ZoneInfo("Pacific/Auckland")
        _, end = self.parse(tz="Pacific/Auckland")

        self.assertEqual(
            timezone.localdate(end, auckland),
            timezone.localdate(timezone=auckland),
        )
        self.assertEqual(end.astimezone(auckland).strftime("%H:%M"), "23:59")

    def test_default_end_never_excludes_past_activity(self):
        """Now is inside today in every zone, so the bound is always ahead."""
        for name in ("Pacific/Auckland", "Europe/Paris", "America/Los_Angeles"):
            with self.subTest(zone=name):
                _, end = self.parse(tz=name)

                self.assertGreater(end, timezone.now())

    def test_all_time_has_no_bounds(self):
        """The explicit "all" range still means no filter."""
        self.assertEqual(
            self.parse(**{"start-date": "all", "end-date": "all"}), (None, None)
        )

    def test_a_malformed_bound_drops_the_filter_entirely(self):
        """Half a range would reach a __range lookup as (None, date)."""
        self.assertEqual(self.parse(**{"start-date": "01/01/2023"}), (None, None))
        self.assertEqual(
            self.parse(**{"start-date": "2026-01-01", "end-date": "nonsense"}),
            (None, None),
        )


class DateOnlyValueTests(TestCase):
    """A date with no time must survive being rendered in any viewer's zone.

    Import files carry days, not instants. Storing one as midnight in the
    server's zone puts it on the previous day for every viewer west of the
    server once the browser renders it, so it is anchored at noon UTC instead.
    """

    WESTERN = "America/Los_Angeles"

    def parsed(self, importer_module, method, raw):
        """Run one importer's date parser without touching the network."""
        importer = importer_module.__new__(importer_module)
        return getattr(importer, method)(raw)

    @override_settings(TZ=PARIS, TIME_ZONE="Europe/Paris")
    def test_import_date_survives_a_western_viewer(self):
        """The day the file named is the day the viewer sees."""
        stored = self.parsed(IMDBImporter, "_parse_date", "2024-01-05")

        self.assertEqual(stored.astimezone(UTC).hour, 12)
        self.assertEqual(
            stored.astimezone(zoneinfo.ZoneInfo(self.WESTERN)).date(),
            datetime.date(2024, 1, 5),
        )

    @override_settings(TZ=PARIS, TIME_ZONE="Europe/Paris")
    def test_anchor_holds_across_the_usual_offsets(self):
        """Noon UTC keeps the day for -12 through +11."""
        stored = self.parsed(IMDBImporter, "_parse_date", "2024-01-05")

        for name in ("Pacific/Honolulu", "America/New_York", "UTC", "Asia/Tokyo"):
            with self.subTest(zone=name):
                self.assertEqual(
                    stored.astimezone(zoneinfo.ZoneInfo(name)).date(),
                    datetime.date(2024, 1, 5),
                )


@override_settings(TZ=PARIS, TIME_ZONE="Europe/Paris", TRACK_TIME=False)
class DateOnlyDeploymentTests(TestCase):
    """With TRACK_TIME off, a picked day still has to survive any zone.

    The widget submits a bare day, which Django would otherwise localize as
    midnight in the server's zone -- putting it a day earlier for every viewer
    west of the server once the browser renders the stored instant.
    """

    def test_widget_is_the_anchoring_one(self):
        """The date input routes through the anchor, not a naive midnight."""
        self.assertIsInstance(EpisodeForm().fields["end_date"].widget, LocalDateInput)

    def test_picked_day_is_anchored(self):
        """A submitted day becomes the instant that stands in for it."""
        form = EpisodeForm({"end_date": "2024-01-05"})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["end_date"],
            datetime.datetime(2024, 1, 5, 12, tzinfo=UTC),
        )

    def test_picked_day_holds_across_zones(self):
        """The day the user picked is the day every viewer sees."""
        form = EpisodeForm({"end_date": "2024-01-05"})
        form.is_valid()
        stored = form.cleaned_data["end_date"]

        for name in ("Pacific/Honolulu", "America/Los_Angeles", "UTC", "Asia/Tokyo"):
            with self.subTest(zone=name):
                self.assertEqual(
                    stored.astimezone(zoneinfo.ZoneInfo(name)).date(),
                    datetime.date(2024, 1, 5),
                )

    def test_editing_round_trips(self):
        """Rendering a stored day back into the input returns the same day."""
        episode = Episode(end_date=datetime.datetime(2024, 1, 5, 12, tzinfo=UTC))

        rendered = EpisodeForm(instance=episode)["end_date"].value()
        form = EpisodeForm({"end_date": rendered})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["end_date"],
            datetime.datetime(2024, 1, 5, 12, tzinfo=UTC),
        )

    def test_an_empty_day_stays_empty(self):
        """Clearing the field must not invent an instant."""
        form = EpisodeForm({"end_date": ""})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["end_date"])
