from pathlib import Path
from unittest.mock import patch

from defusedxml import ElementTree
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import BoardGame, MediaTypes, Status
from integrations.imports import bgg

mock_path = Path(__file__).resolve().parent / "mock_data"


@patch("integrations.imports.bgg.time.sleep")
@patch("app.providers.services.api_request")
class ImportBGGUpdate(TestCase):
    """Test BGG importer update and retry behavior."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def test_import_retries_when_collection_is_queued(
        self,
        mock_api_request,
        mock_sleep,
    ):
        """Test that BGG import retries when collection is still queued."""
        with Path(mock_path / "import_bgg_queued.xml").open() as file:
            queued_xml = ElementTree.fromstring(file.read())

        with Path(mock_path / "import_bgg.xml").open() as file:
            collection_xml = ElementTree.fromstring(file.read())

        mock_api_request.side_effect = [queued_xml, collection_xml]

        imported_counts, _ = bgg.importer("someuser", self.user, "new")

        self.assertEqual(mock_api_request.call_count, 2)
        mock_sleep.assert_called_once_with(15)

        self.assertEqual(imported_counts[MediaTypes.BOARDGAME.value], 3)

        catan = BoardGame.objects.get(user=self.user, item__title="Catan")
        self.assertEqual(catan.status, Status.IN_PROGRESS.value)
        self.assertEqual(catan.progress, 5)
