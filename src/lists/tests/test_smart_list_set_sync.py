"""Smart-list upkeep for many changed items evaluates each list once.

``sync_smart_lists_for_items_task`` used to check every changed item against
every smart list one at a time. It now asks each list which of the changed
items match in one set query. These tests hold the set version to the
per-item version's result (``sync_smart_lists_for_item`` is kept as the
reference) and pin how many queries a bulk change costs.
"""

import logging
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app.models import (
    TV,
    Book,
    CollectionEntry,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from lists import smart_rules
from lists import tasks as list_tasks
from lists.models import CustomList, CustomListItem
from lists.smart_rules import sync_smart_lists_for_item

STATUSES = (Status.COMPLETED.value, Status.IN_PROGRESS.value, Status.PLANNING.value)

# 500 changed items across 5 smart lists. The per-item path issued about
# 10,000 queries for this fixture; the set path issues 43.
SET_SYNC_QUERY_CEILING = 60


def setUpModule():
    """Silence per-item log noise for this module only."""
    logging.disable(logging.WARNING)


def tearDownModule():
    """Restore logging for other modules."""
    logging.disable(logging.NOTSET)


def _build_library(owner, unit):
    """Create ``5 * unit`` varied items for ``owner`` and return them.

    Movies (tracked with mixed status and score), books with authors, TMDB
    and TVDB shows where some TMDB rows are verified aliases of a TVDB row,
    collection-only movies, and untracked items.
    """
    items = []

    def make(index, media_type, source=Sources.TMDB.value, **fields):
        item = Item.objects.create(
            title=f"{media_type} {index}",
            media_id=str(10_000 + index),
            media_type=media_type,
            source=source,
            image="https://example.com/i.jpg",
            **fields,
        )
        items.append(item)
        return item

    for index in range(2 * unit):
        item = make(index, MediaTypes.MOVIE.value)
        Movie.objects.create(
            item=item,
            user=owner,
            status=STATUSES[index % 3],
            score=Decimal(index % 10) if index % 4 else None,
        )
        if index % 5 == 0:
            CollectionEntry.objects.create(user=owner, item=item)

    for index in range(2 * unit, 3 * unit):
        author = "Ann Writer" if index % 2 else "Bo Other"
        item = make(index, MediaTypes.BOOK.value, source=Sources.OPENLIBRARY.value, authors=[author])
        Book.objects.create(
            item=item,
            user=owner,
            status=STATUSES[index % 3],
            score=Decimal(8) if index % 3 == 0 else None,
        )

    for index in range(3 * unit, 4 * unit):
        tvdb = index % 5 == 0
        fields = {}
        if not tvdb and index % 5 == 1:
            # Verified alias of the TVDB show created one index earlier.
            fields["provider_external_ids"] = {"tvdb_id": str(10_000 + index - 1)}
        item = make(
            index,
            MediaTypes.TV.value,
            source=Sources.TVDB.value if tvdb else Sources.TMDB.value,
            **fields,
        )
        # Completed shows fan out to the provider; keep the fixture offline.
        TV.objects.create(item=item, user=owner, status=STATUSES[1 + index % 2])

    for index in range(4 * unit, 4 * unit + unit // 2):
        item = make(index, MediaTypes.MOVIE.value)
        CollectionEntry.objects.create(user=owner, item=item)

    for index in range(4 * unit + unit // 2, 5 * unit):
        make(index, MediaTypes.MOVIE.value)

    return items


def _make_lists(owner, items, unit):
    """Create the five smart lists plus the manual list one of them links.

    The manual list holds a tracked show, collection-only movies and
    untracked items.
    """
    manual = CustomList.objects.create(name="Manual", owner=owner)
    picked = items[3 * unit : 3 * unit + 1] + items[4 * unit + unit // 4 : 4 * unit + 3 * unit // 4]
    for item in picked:
        CustomListItem.objects.create(custom_list=manual, item=item, added_by=owner)

    def smart(name, media_types, **filters):
        return CustomList.objects.create(
            name=name,
            owner=owner,
            is_smart=True,
            smart_media_types=media_types,
            smart_filters={"status": "all", "rating": "all", "collection": "all", **filters},
        )

    return [
        smart("Completed movies", [MediaTypes.MOVIE.value], status=[Status.COMPLETED.value]),
        smart("Ann's books", [MediaTypes.BOOK.value], author="Ann Writer"),
        smart("Shows", [MediaTypes.TV.value]),
        smart("Rated or picked", [], rating_min="7", list=[str(manual.id)]),
        smart("Collected", [], collection="collected"),
    ]


def _memberships(lists):
    return set(
        CustomListItem.objects.filter(custom_list__in=lists).values_list(
            "custom_list_id",
            "item_id",
            "added_by_id",
        ),
    )


def _per_item_sync(owner, items):
    for item in items:
        sync_smart_lists_for_item(owner=owner, item=item)


class SetSyncFixtureMixin:
    """One owner, ``5 * UNIT`` changed items and five smart lists of mixed rules."""

    UNIT = 20

    @classmethod
    def setUpTestData(cls):
        """Build the library before the lists so tracker saves do not sync."""
        cls.owner = get_user_model().objects.create_user(username="owner", password="x")
        cls.items = _build_library(cls.owner, cls.UNIT)
        cls.smart_lists = _make_lists(cls.owner, cls.items, cls.UNIT)
        cls.item_ids = [item.id for item in cls.items]

    def _reset(self):
        CustomListItem.objects.filter(custom_list__in=self.smart_lists).delete()

    def _reference(self, seed=None):
        """Return the memberships the per-item path produces from ``seed``."""
        self._reset()
        if seed:
            seed()
        _per_item_sync(self.owner, self.items)
        expected = _memberships(self.smart_lists)
        self._reset()
        if seed:
            seed()
        return expected


class SmartListSetSyncEquivalenceTests(SetSyncFixtureMixin, TestCase):
    """The set path leaves exactly the rows the per-item path leaves."""

    def _assert_equivalent(self, seed=None):
        expected = self._reference(seed)
        list_tasks.sync_smart_lists_for_items_task(self.owner.id, self.item_ids)
        self.assertEqual(_memberships(self.smart_lists), expected)
        return expected

    def test_every_list_has_members_and_non_members(self):
        """Each rule type in the fixture both admits and rejects items."""
        expected = self._reference()
        for custom_list in self.smart_lists:
            members = {item_id for list_id, item_id, _ in expected if list_id == custom_list.id}
            self.assertTrue(members, custom_list.name)
            self.assertLess(len(members), len(self.items), custom_list.name)

    def test_from_empty_lists(self):
        """Starting with no members, both paths add the same rows."""
        self._assert_equivalent()

    def test_from_correct_lists(self):
        """Starting from correct membership, nothing changes."""
        expected = self._reference()

        def seed():
            CustomListItem.objects.bulk_create(
                [
                    CustomListItem(custom_list_id=list_id, item_id=item_id, added_by_id=added_by)
                    for list_id, item_id, added_by in expected
                ],
            )

        self.assertEqual(self._assert_equivalent(seed), expected)

    def test_from_wrong_and_missing_members(self):
        """Wrong members are removed and missing ones added, as before."""
        expected = self._reference()
        by_list = {}
        for list_id, item_id, _ in expected:
            by_list.setdefault(list_id, set()).add(item_id)

        def seed():
            rows = []
            for custom_list in self.smart_lists:
                members = by_list.get(custom_list.id, set())
                kept = sorted(members)[::2]  # every other correct member is missing
                wrong = [item.id for item in self.items if item.id not in members][:25]
                rows += [
                    CustomListItem(custom_list=custom_list, item_id=item_id, added_by=self.owner)
                    for item_id in kept + wrong
                ]
            CustomListItem.objects.bulk_create(rows)

        self._assert_equivalent(seed)

    def test_subset_of_items_only_touches_those_items(self):
        """Rows for items outside the changed set are left alone."""
        expected = self._reference()
        outsider = next(item_id for _, item_id, _ in expected)
        stale = self.items[-1]  # untracked and not in the manual list: matches nothing

        def seed():
            CustomListItem.objects.create(
                custom_list=self.smart_lists[0],
                item=stale,
                added_by=self.owner,
            )

        changed = [item for item in self.items if item.id not in {outsider, stale.id}]
        smart_rules.sync_smart_lists_for_items(self.owner, [])
        seed()
        smart_rules.sync_smart_lists_for_items(self.owner, changed)
        rows = _memberships(self.smart_lists)
        self.assertIn((self.smart_lists[0].id, stale.id, self.owner.id), rows)
        self.assertFalse(any(item_id == outsider for _, item_id, _ in rows))
        self.assertEqual(
            {row for row in rows if row[1] not in {outsider, stale.id}},
            {row for row in expected if row[1] not in {outsider, stale.id}},
        )

    def test_untracked_items_resolve_as_before(self):
        """Items the owner stopped tracking leave the lists in both paths."""

        def seed():
            Movie.objects.filter(user=self.owner, item__in=self.items[:60]).delete()
            CustomListItem.objects.bulk_create(
                [
                    CustomListItem(custom_list=custom_list, item=item, added_by=self.owner)
                    for custom_list in self.smart_lists
                    for item in self.items[:60]
                ],
                ignore_conflicts=True,
            )

        self._assert_equivalent(seed)

    def test_failing_list_does_not_stop_the_others(self):
        """A list whose rules raise is skipped; the rest still sync."""
        expected = self._reference()
        broken = self.smart_lists[1]
        real = smart_rules.normalize_list_rules

        def flaky(custom_list):
            if custom_list.id == broken.id:
                raise ValueError("bad rules")
            return real(custom_list)

        with (
            patch.object(smart_rules, "normalize_list_rules", side_effect=flaky),
            self.assertLogs("lists.smart_rules", level="ERROR") as logs,
        ):
            list_tasks.sync_smart_lists_for_items_task(self.owner.id, self.item_ids)

        self.assertIn(f"list_id={broken.id}", logs.output[0])
        self.assertEqual(
            _memberships(self.smart_lists),
            {row for row in expected if row[0] != broken.id},
        )

    def test_collaborator_lists_are_untouched(self):
        """Only the owner's smart lists are synced, as before."""
        other = get_user_model().objects.create_user(username="other", password="x")
        theirs = CustomList.objects.create(
            name="Theirs",
            owner=other,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all", "rating": "all", "collection": "all"},
        )
        theirs.collaborators.add(self.owner)
        CustomListItem.objects.create(custom_list=theirs, item=self.items[0], added_by=other)

        list_tasks.sync_smart_lists_for_items_task(self.owner.id, self.item_ids)

        self.assertEqual(
            list(theirs.items.values_list("id", flat=True)),
            [self.items[0].id],
        )

    def test_return_shape(self):
        """The set function reports what it checked, added and removed."""
        expected = self._reference()
        result = smart_rules.sync_smart_lists_for_items(self.owner, self.items)
        self.assertEqual(
            result,
            {"checked": len(self.smart_lists), "added": len(expected), "removed": 0},
        )
        self.assertEqual(
            smart_rules.sync_smart_lists_for_items(self.owner, self.items),
            {"checked": len(self.smart_lists), "added": 0, "removed": 0},
        )


class SmartListSetSyncQueryCeilingTests(SetSyncFixtureMixin, TestCase):
    """A bulk change costs a handful of queries per list, not one per item."""

    UNIT = 100  # 500 items

    def test_query_ceiling_for_500_items_and_5_lists(self):
        """The whole task stays under the ceiling the per-item path far exceeds."""
        with CaptureQueriesContext(connection) as queries:
            list_tasks.sync_smart_lists_for_items_task(self.owner.id, self.item_ids)
        self.assertLessEqual(len(queries), SET_SYNC_QUERY_CEILING)
