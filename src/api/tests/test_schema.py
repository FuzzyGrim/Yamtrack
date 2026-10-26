import json

from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator

from api.helpers import (
    MEDIA_STATUS_MAP,
    MEDIA_TYPE_COMPLETE_VALID_LIST,
    VALID_SOURCES,
)
from app.models import Sources, Status


class SchemaTests(SimpleTestCase):
    """Validate the OpenAPI schema and the API enums."""

    def test_media_status_map_covers_every_status(self):
        """Every model status should have a distinct API value."""
        self.assertEqual(set(MEDIA_STATUS_MAP), set(Status.values))
        self.assertEqual(len(set(MEDIA_STATUS_MAP.values())), len(Status.values))

    def test_valid_sources_cover_every_media_type(self):
        """Every media type should accept its provider sources and manual."""
        self.assertEqual(set(VALID_SOURCES), set(MEDIA_TYPE_COMPLETE_VALID_LIST))
        for media_type, sources in VALID_SOURCES.items():
            with self.subTest(media_type=media_type):
                self.assertIn(Sources.MANUAL.value, sources)
                self.assertGreater(len(sources), 1)

    def test_schema_has_no_ambiguous_one_of(self):
        """Unions without a discriminator should use anyOf, not oneOf."""
        schema = SchemaGenerator().get_schema(request=None, public=True)

        def find_one_of(node, path):
            if isinstance(node, dict):
                if "oneOf" in node and "discriminator" not in node:
                    yield path
                for key, child in node.items():
                    yield from find_one_of(child, f"{path}/{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    yield from find_one_of(child, f"{path}/{index}")

        json.dumps(schema, default=str)
        self.assertEqual(list(find_one_of(schema, "")), [])
