#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

from django.core.management import execute_from_command_line


def main():
    """Run administrative tasks."""
    if "test" in sys.argv:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
    else:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
