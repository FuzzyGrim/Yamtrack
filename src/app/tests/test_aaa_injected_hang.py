"""INJECTION (throwaway): a test hangs past the watchdog."""

import time

from django.test import TestCase


class InjectedHangTests(TestCase):
    def test_hangs(self):
        time.sleep(100_000)
