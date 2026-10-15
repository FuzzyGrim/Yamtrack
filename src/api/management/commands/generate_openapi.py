from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    """Generate the OpenAPI schema file from DRF endpoints."""

    help = "Generate openapi.yaml using drf-spectacular."

    def add_arguments(self, parser):
        """Add optional CLI arguments."""
        parser.add_argument(
            "--file",
            default=None,
            help="Output path for the schema file. Defaults to repository openapi.yaml.",  # noqa: E501
        )
        parser.add_argument(
            "--validate",
            action="store_true",
            help="Validate generated schema during export.",
        )

    def handle(self, *args, **options):  # noqa: ARG002
        """Generate OpenAPI file using spectacular command."""
        if options["file"]:
            output_path = Path(options["file"]).expanduser().resolve()
        else:
            output_path = settings.BASE_DIR.parent / "openapi.yaml"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        call_command(
            "spectacular",
            file=str(output_path),
            validate=options["validate"],
            color=True,
        )

        self.stdout.write(
            self.style.SUCCESS(f"OpenAPI schema generated: {output_path}"),
        )
