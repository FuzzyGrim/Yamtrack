# Localization (i18n)

## Supported languages

Currently configured languages:

- `en` (default)
- `it-it`

## Translating Yamtrack

Translations are managed on [Crowdin](https://crowdin.com/project/yamtrack). Sign in, pick a language and translate the strings there; you don't need to edit any files in the repository.

Changes made on Crowdin are synced back automatically: a GitHub Action opens a pull request with the updated `.po` files in `src/locale/<locale>/LC_MESSAGES/`. Please don't edit those files directly in a pull request, as they are overwritten by the next sync from Crowdin.

Strings that haven't been translated yet are shown in English.

## Add a new language

To request a new language, open the [Crowdin project](https://crowdin.com/project/yamtrack) and click **Request new language** in the Details section of the Dashboard tab. Once a maintainer approves it, the language can be translated there.

After some translations have been made and synced, a maintainer adds the language to the `LANGUAGES` setting in `src/config/settings.py` so it appears in the app language selector. The language code is the name of its folder in `src/locale/`, in lower case and with a hyphen, for example `pt-br` for `pt_BR`.

!!! warning IMPORTANT
    When a new language is added, test it by running the development server and switching to the new language from the app language selector. More infos at [General setup](development.md#general-setup).

## Adding translatable strings

When you add or change text in the code, mark it for translation with `{% trans %}`, `{% blocktranslate %}`, or `gettext_lazy` / `gettext` (can be imported as `_`). In JavaScript, use `gettext()` from the JavaScript catalog.

You don't need to run `makemessages` or update any `.po` files. After your change is merged into `dev`, the Crowdin GitHub Action extracts the strings with `makemessages` and uploads them to Crowdin for translation.

### Testing translations locally

Compiling translations requires the GNU gettext tools (for example `sudo apt install gettext`). To see the translated UI locally, compile the message catalogs with:

```bash
cd src
uv run manage.py compilemessages
```

This generates the `.mo` files that Django uses for displaying text. They are not committed to the repository.

To check how new strings are extracted, you can also run `makemessages` locally:

```bash
cd src
uv run manage.py makemessages --all -d django
uv run manage.py makemessages --all -d djangojs
```

Don't commit the resulting `.po` changes; Crowdin is the source of truth for translations.


### Translating strings with links

Use the `inline_link` template tag when a translated sentence contains a link. It builds an HTML anchor with `format_html`, so the URL, CSS class, and translated label are escaped safely. The HTML stays out of the `.po` message and translators can move the link placeholder to fit their language.

Load `app_tags`, define the URL and translated label, then assign the tag output to a variable for use in `blocktrans`:

```django
{% load app_tags i18n %}

{% url 'url-of-page' as test_url %}
{# Translators: In "Watch provider region is not configured. Set a region or disable this feature in clickable text of the link.", this is the linked text. #}
{% trans "clickable text of the link" as link_label %}
{% with html_class="text-indigo-400 hover:text-indigo-300 hover:underline" %}
    {% inline_link test_url html_class link_label as test_link %}
    {% blocktrans trimmed %}
        Watch provider region is not configured. Set a region or disable this feature in {{ test_link }}.
    {% endblocktrans %}
{% endwith %}
```

The extracted message contains a placeholder such as `%(test_link)s`, not the anchor markup. Keep the link label in its own `{% trans %}` string so it can be translated independently. Add a `{# Translators: ... #}` comment immediately before that string, giving the complete sentence and explaining which words form the link; Django includes this comment in the `.po` file with the label. `inline_link` is a simple tag, not a filter: pass its three arguments separated by spaces, then use `as` to capture its output. Avoid marking arbitrary values safe; `inline_link` handles escaping through `format_html`.

For external pages, add `new_tab=True` so the link opens in a new tab with `target="_blank" rel="noopener noreferrer"`:

```django
{% trans "link" as repository_link_label %}
{% inline_link "https://example.com/manifest.json" html_class repository_link_label new_tab=True as repository_link %}
{% blocktrans trimmed %}
    Add this repository {{ repository_link }} to your "Plugins Catalog" in Jellyfin settings.
{% endblocktrans %}
```

### Translating strings with inline code

Use `inline_code` for inline code or configuration values inside a translated sentence. Like `inline_link`, it creates one escaped HTML fragment so translators can move the placeholder without handling HTML tags:

```django
{% inline_code "URLS" "bg-[#39404b] px-1 py-0.5 rounded" as urls_code %}
{% blocktrans trimmed %}
    Set the {{ urls_code }} environment variable to your application URL.
{% endblocktrans %}
```

### User language and background tasks

The selected language is saved on the user account (`User.language`), not in a browser cookie. `app.middleware.UserLanguageMiddleware` activates it on every request of an authenticated user, so the browser's `Accept-Language` header is only used for anonymous pages such as the login page.

Celery tasks run outside a request, so no language is active and every string falls back to `LANGUAGE_CODE`. When a task builds text that a user will read (import summaries and warnings, notifications), wrap that code in `translation.override(user.language)`:

```python
from django.utils import translation

with translation.override(user.language):
    message = _("Imported %(media_list)s.") % {"media_list": media_list}
```

For strings defined once and used for several users, such as a notification title, use `gettext_lazy` and convert them with `str()` inside the `override` block.
