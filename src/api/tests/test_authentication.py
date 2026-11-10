from .base import YamtrackApiTestCase
from .endpoints import get_endpoint_cases


class AuthenticationMatrixTests(YamtrackApiTestCase):
    """Assert protected endpoints reject missing or invalid credentials."""

    def test_protected_endpoints_require_authentication(self):
        """Protected endpoints must reject requests with no credentials."""
        for case in get_endpoint_cases():
            if case.is_public:
                continue

            with self.subTest(
                method=case.method,
                endpoint=case.url_name,
                args=case.args,
            ):
                response = self.call_api(
                    case.method,
                    case.url_name,
                    args=case.args,
                    payload=case.payload,
                )
                self.assertEqual(response.status_code, 401)
                self.assertIn("WWW-Authenticate", response.headers)

    def test_protected_endpoints_reject_invalid_token(self):
        """Protected endpoints must reject requests with invalid API keys."""
        for case in get_endpoint_cases():
            if case.is_public:
                continue

            with self.subTest(
                method=case.method,
                endpoint=case.url_name,
                args=case.args,
            ):
                response = self.call_api(
                    case.method,
                    case.url_name,
                    args=case.args,
                    payload=case.payload,
                    headers=self.invalid_auth_headers,
                )
                self.assertEqual(response.status_code, 401)
                self.assertIn("WWW-Authenticate", response.headers)
