"""INJECTION (throwaway): the interpreter dies mid-shard."""

import os

from django.test import TestCase


class InjectedCrashTests(TestCase):
    def test_interpreter_exits(self):
        os._exit(1)
