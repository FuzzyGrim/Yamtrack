from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import NotFound, ParseError, ValidationError

from api.exceptions import api_exception_handler
from api.helpers import InvalidSortError
from app.providers.services import ProviderAPIError


def provider_error(status_code):
    """Build a ProviderAPIError with the given HTTP status (None = network)."""
    response = SimpleNamespace(status_code=status_code, text="provider failure")
    error = SimpleNamespace(response=response if status_code else None)
    return ProviderAPIError("tmdb", error)


class ApiExceptionHandlerTests(SimpleTestCase):
    """Exceptions raised in the views are mapped to the API error format."""

    def handle(self, exc):
        """Run the handler with an empty view context."""
        return api_exception_handler(exc, {})

    def test_drf_exception_keeps_detail(self):
        """DRF exceptions keep their status and `detail`."""
        response = self.handle(ParseError("bad input"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "bad input")

        self.assertEqual(self.handle(NotFound()).status_code, 404)

    def test_drf_validation_error_is_reshaped(self):
        """DRF validation errors include the failing fields under `errors`."""
        response = self.handle(ValidationError({"score": ["Too high."]}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Invalid request: score.")
        self.assertEqual(response.data["errors"], {"score": ["Too high."]})

    def test_django_validation_error_returns_400(self):
        """Model validation errors are returned as 400 with their fields."""
        response = self.handle(DjangoValidationError({"end_date": ["Invalid."]}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["errors"], {"end_date": ["Invalid."]})

        response = self.handle(DjangoValidationError("Plain message."))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["errors"],
            {"non_field_errors": ["Plain message."]},
        )

    def test_provider_not_found_returns_404(self):
        """A 404 from the provider means the media doesn't exist."""
        response = self.handle(provider_error(404))
        self.assertEqual(response.status_code, 404)

    def test_provider_failure_returns_500(self):
        """Other provider failures are server errors, network ones too."""
        for status_code in (500, 429, None):
            with self.subTest(status_code=status_code):
                response = self.handle(provider_error(status_code))
                self.assertEqual(response.status_code, 500)

    def test_provider_failure_is_logged(self):
        """Provider failures are logged with the exception."""
        with self.assertLogs("api.helpers", "ERROR"):
            self.handle(provider_error(500))

    @override_settings(DEBUG=False)
    def test_provider_failure_hides_exception(self):
        """The provider message is not returned unless DEBUG is on."""
        response = self.handle(provider_error(500))
        self.assertNotIn("debug", response.data)

    @override_settings(DEBUG=True)
    def test_provider_failure_adds_debug_with_debug(self):
        """The provider message is returned under `debug` when DEBUG is on."""
        response = self.handle(provider_error(500))
        self.assertIn("debug", response.data)

    def test_invalid_sort_returns_400(self):
        """Invalid sorts are returned as bad requests."""
        response = self.handle(InvalidSortError("nope"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Invalid sorting")

    def test_integrity_error_returns_409(self):
        """Database conflicts are returned as 409."""
        response = self.handle(IntegrityError("duplicate"))
        self.assertEqual(response.status_code, 409)

    def test_does_not_exist_returns_404(self):
        """Missing objects not handled by the view are returned as 404."""
        response = self.handle(ObjectDoesNotExist())
        self.assertEqual(response.status_code, 404)

    def test_unknown_exception_is_not_handled(self):
        """Unexpected exceptions are left to the middleware, which returns a 500."""
        self.assertIsNone(self.handle(RuntimeError("boom")))
