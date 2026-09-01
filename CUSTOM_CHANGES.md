# Custom Yamtrack Changes

This fork is a personal customized build of the upstream [FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack).

It is intentionally **not automatically synchronized or patched when upstream releases a new version**. The goal is to keep a known-working customized build and only port these changes forward when an upstream release is worth adopting.

## Custom feature: cross-account tracking visibility

### Purpose

When searching for media, show whether the same media is already tracked by other Yamtrack accounts on this installation.

### Behavior compared with upstream

Upstream Yamtrack enriches search results with the current user's own tracking state. This customization additionally looks up all local accounts tracking each search-result item and exposes their usernames to the result templates.

In grid results, a tracked item displays a small **Tracked by N account(s)** section followed by the usernames.

In list results, a tracked item displays **Tracked by N account(s):** followed by the usernames.

The lookup supports seasons by including the season number in the media key. Usernames are de-duplicated and sorted case-insensitively.

### Implementation areas

The customization currently affects:

- `src/app/helpers.py` — builds a tracking-users lookup while enriching media search results.
- `src/templates/app/search.html` — passes `tracking_users` into result cards.
- `src/templates/app/components/media_card.html` — displays cross-account tracking information in grid view.
- `src/templates/app/components/media_card_list.html` — displays cross-account tracking information in list view.
- Tests covering helper behavior and media search output.

The previous private implementation recorded the feature commits as `10c942b6`, `0cc5153b`, and `7ad56c7c`.

## Updating this fork later

Do not automatically merge every upstream Yamtrack release and do not blindly copy old modified files over new upstream files.

When an upstream version is worth adopting:

1. Start from the desired new upstream Yamtrack version.
2. Read this document to understand the intended behavior.
3. Compare the previous customized version against its upstream base when useful.
4. Reimplement the cross-account tracking behavior using the new upstream architecture.
5. Preserve new upstream behavior wherever possible.
6. Run the relevant Yamtrack tests and manually verify grid and list search results with multiple local accounts.
7. Deploy the tested customized version.

## Automation policy

There should be no custom workflow that periodically checks upstream, automatically prepares releases, or automatically reapplies this feature to new upstream versions. Updates are deliberate and manual/AI-assisted.