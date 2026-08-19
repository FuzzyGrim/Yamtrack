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
