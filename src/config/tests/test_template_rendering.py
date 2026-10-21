from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from app.models import MediaTypes
from lists.models import CustomList


class TemplateCompilationTests(SimpleTestCase):
    """Test that every template compiles."""

    def test_all_templates_compile(self):
        """Compile every template under the project templates directory."""
        template_root = Path(settings.BASE_DIR) / "templates"
        template_names = sorted(
            path.relative_to(template_root).as_posix()
            for path in template_root.rglob("*.html")
        )

        for template_name in template_names:
            with self.subTest(template=template_name):
                get_template(template_name)


class PageRenderingPerLanguageTests(TestCase):
    """Test that every page renders with a real context in each language."""

    def setUp(self):
        """Create a user with a custom list."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.custom_list = CustomList.objects.create(name="Test", owner=self.user)

    def _pages(self):
        """Return the URLs of the pages to render for a logged-in user.

        Pages that call external provider APIs (search, media details) are left
        out, their templates are still checked by TemplateCompilationTests.
        """
        pages = [
            reverse("home"),
            reverse("statistics"),
            reverse("journal"),
            reverse("create_entry"),
            reverse("calendar"),
            reverse("lists"),
            reverse("list_detail", args=[self.custom_list.id]),
            reverse("account"),
            reverse("notifications"),
            reverse("preferences"),
            reverse("integrations"),
            reverse("import_data"),
            reverse("export_data"),
            reverse("advanced"),
            reverse("about"),
        ]
        pages += [
            reverse("medialist", args=[self.user.username, media_type])
            for media_type in MediaTypes.values
            if media_type not in (MediaTypes.SEASON, MediaTypes.EPISODE)
        ]
        return pages

    def _assert_renders(self, url, language):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Language"], language)

    def test_pages_render_for_each_language(self):
        """Render every logged-in page in each configured language."""
        self.client.login(**self.credentials)

        for language, _name in settings.LANGUAGES:
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
            for url in self._pages():
                with self.subTest(language=language, url=url):
                    self._assert_renders(url, language)

    def test_login_page_renders_for_each_language(self):
        """Render the login page in each language."""
        for language, _name in settings.LANGUAGES:
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
            with self.subTest(language=language):
                self._assert_renders(reverse("account_login"), language)
