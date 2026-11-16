"""INJECTION (throwaway): a required browser test fails."""

from django.test import SimpleTestCase, tag


@tag("slow", "playwright")
class InjectedBrowserFailureTests(SimpleTestCase):
    def test_fails(self):
        self.fail("injected browser failure")
