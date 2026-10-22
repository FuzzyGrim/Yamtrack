# Localization (i18n)

## Supported languages

Currently configured languages:

- `en` (default)
- `it`

## Add a new language

Yamtrack uses Django i18n with message catalogs stored in the `/src/locale` directory.

From the repository root, run:

```bash
cd src
uv run manage.py makemessages -d django -l lang_code
uv run manage.py makemessages -d djangojs -l lang_code
```

This generates the `.po` and `.mo` files for the language specified by `lang_code`, that is the [locale name](https://docs.djangoproject.com/en/6.0/topics/i18n/#term-locale-name) of the language you want to add. For example, for French, use `fr`.

To translate the newly created language, edit the `locale/lang_code/LC_MESSAGES/django.po` and `locale/lang_code/LC_MESSAGES/djangojs.po` files. No specific app is needed since they are plain-text files, but there are some tools that can help.

After editing the `.po` message file, compile the message catalogs with:

```bash
uv run manage.py compilemessages
```

After compiling, add the language to the `LANGUAGES` setting in `src/config/settings.py`.

!!! warning IMPORTANT
    If you add a new language, you need test it by running the development server and switching to the new language from the app language selector. More infos at [General setup](development.md#general-setup).

## Update existing languages

From the repository root, run:

```bash
cd src
uv run manage.py makemessages -d django -l en -l it
uv run manage.py makemessages -d djangojs -l en -l it
```

You need to add all the supported languages with the `-l` flag.

To translate the newly created language, edit the `locale/lang_code/LC_MESSAGES/django.po` and `locale/lang_code/LC_MESSAGES/djangojs.po` files. No specific app is needed since they are plain-text files, but there are some tools that can help.

After editing the `.po` message file, compile the message catalogs with:

```bash
uv run manage.py compilemessages
```

Recommended contribution flow:

1. Mark strings with `{% trans %}`, `{% blocktranslate %}`, or `gettext_lazy` / `gettext` (can be imported as `_`).
2. Run `makemessages` for supported locales.
3. Update translations in `locale/<lang>/LC_MESSAGES/django.po`.
4. Run `compilemessages` and verify UI language switching from the app language selector.

!!! warning IMPORTANT
    If you modify an existing language, you need test it by running the development server and switching to the new language from the app language selector. More infos at [General setup](development.md#general-setup).

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

