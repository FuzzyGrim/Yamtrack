import sys
from unittest import mock

from django.test import SimpleTestCase
from gunicorn.app.wsgiapp import run


class GunicornConfigTests(SimpleTestCase):
    """Test Gunicorn configuration."""

    def test_config(self):
        """Test that the Gunicorn configuration file is valid."""
        argv = [
            "gunicorn",
            "--check-config",
            "--config",
            "python:config.gunicorn",
            "config.wsgi",
        ]
        mock_argv = mock.patch.object(sys, "argv", argv)

        with self.assertRaises(SystemExit) as cm, mock_argv:
            run()

        exit_code = cm.exception.args[0]
        self.assertEqual(exit_code, 0)
