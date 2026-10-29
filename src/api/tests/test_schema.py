import json
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator

from api.helpers import (
    MEDIA_STATUS_MAP,
    MEDIA_TYPE_COMPLETE_VALID_LIST,
    VALID_SOURCES,
)
from app.models import Sources, Status

from .schema_validation import get_schema_errors, iter_response_examples


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

    def test_response_examples_match_schema(self):
        """Every documented response example should match its response schema."""
        examples = list(iter_response_examples())
        self.assertTrue(examples)
        for label, content_schema, value in examples:
            with self.subTest(example=label):
                self.assertEqual(get_schema_errors(content_schema, value), [])

    def test_openapi_file_is_up_to_date(self):
        """The committed openapi.yaml should match the generated schema."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            generated_path = Path(tmp_dir) / "openapi.yaml"
            call_command(
                "generate_openapi", file=str(generated_path), stdout=StringIO()
            )
            generated = generated_path.read_text()

        committed = (settings.BASE_DIR.parent / "openapi.yaml").read_text()
        self.assertEqual(
            committed,
            generated,
            "openapi.yaml is outdated, run `python manage.py generate_openapi`",
        )
