# Local covers

A provider cover is a link Floppy shows from the provider. A **local cover** is a
picture a person chose, copied into Floppy's own data folder so it keeps working
whatever happens to the site it came from.

Code: `src/app/local_covers.py` (storage), `src/app/cover_views.py` (views).

## How it is stored

- The file lives at `FLOPPY_DATA_DIR/covers/<sha256>.<jpg|png|webp>`. The name is the
  hash of the saved bytes, so the same picture is stored once and a name never changes.
- `Item.image` holds the path `/covers/<sha256>.<ext>`. Nothing else in the app needs to
  know the difference: templates run it through `image_url`, which leaves it alone, and
  the API renderer turns it into an absolute URL for clients that have no page to resolve
  it against.
- Card sizing (`app.artwork_sizes`, see the media-card contract) never rewrites a local
  cover: it only shortens provider URLs. With the image cache on, a card's smaller
  provider size is a second file in `image-cache/`, next to the full-size one Detail uses.
- `covers/` is **not** part of `image-cache/`. "Clear cache" removes derived copies of
  provider artwork; a local cover cannot be re-downloaded, so it must survive that.
  Back up the data folder and the covers come with it.

## What is accepted

| Rule | Why |
|---|---|
| JPEG, PNG or WebP only, checked by decoding with Pillow | A file's name or `Content-Type` proves nothing; SVG is refused because it can carry script |
| 10 MB at most, before and after download | Same cap as the provider image cache |
| Re-encoded, longest side at most 1600 px | Drops EXIF (a phone photo carries its GPS position) and keeps a 12 MP photo from filling the disk |

## The picker

The UI is the reusable `components/image_picker.html` (Plex-style): a bar with
**choose an image**, **drag and drop** and **enter a URL** in a filled container, over one
horizontally scrolling row (the Home rows' compact card width) holding the current picture
and the pictures on offer. Another place needs only a `save_url` that accepts `cover_file` or `cover_url`, and a
`candidates_url` that returns `components/image_picker_tiles.html`. `cover_change_section.html`
is the cover's use of it.

All three ways in end at `local_covers.store_bytes`, so the rules above apply to each.

1. **Upload** a file (choose or drop).
2. **Link** to an image. Links to a web page (a book or comic site) use the cover the
   page names in `og:image`, `twitter:image` or `<link rel="image_src">`.
3. **Offered pictures**: Open Library by the item's Barcode field, then the item's provider
   search results. Choosing one posts its link, so it is copied like any other link.

Links are fetched with `integrations.safe_fetch`, not the artwork allowlist, because a
person can paste any site. See [outbound-fetch.md](outbound-fetch.md) for the controls
(public addresses only, pinned connection, bounded size and time) and the residual
DNS-rebinding gap, which applies here too.

The CLZ importer copies each cover it is given the same way. It is best-effort: a
cover that cannot be fetched stays a link, and a run stops trying after
`COVER_FAILURE_LIMIT` failures in a row so an unreachable host costs seconds, not minutes.

## Who may change a cover, and who sees it

- The person must track the item or have it in their Collection
  (`cover_views.user_has_item`). Episodes are excluded.
- `Item` rows are shared by everyone on the instance, so a changed cover changes for
  every user who has that item. The pre-existing Image URL field already behaved this way.
- `/covers/<name>` is public, like `/image-cache/<token>`, because public lists and API
  clients show covers without a session. The name is a content hash, so it cannot be
  guessed, and only names Floppy made are served (anything else is a 404).

## Cleaning up

When an item's cover changes, the old file is deleted if no item still points at it
(`local_covers.discard_if_unused`). Deleting an item does not remove its file.

## Manual items

A manual item's Metadata form reads the image from `manual_metadata["image"]` before
`Item.image`, so changing the cover updates both, and the form's Image URL field accepts
a local path unchanged. Without that, saving the form would put the old cover back or
reject the field.
