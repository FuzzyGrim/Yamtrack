# CHANGELOG.md

All notable changes to this project should be documented here.

---

## Unreleased

### Added (Social Sharing)

- Public user profiles with avatar and bio support
- User directory to browse and discover other users
- Public/private list visibility toggle (is_public field on CustomList)
- Public list detail page for viewing lists owned by other users
- Navigation links: "People" (user directory) and "My Profile" in sidebar
- Media file upload support (MEDIA_ROOT/MEDIA_URL configuration)
- Avatar image upload in account settings
- Bio field in user account settings
- Public/private badge on list cards
- Profile view "Add to My Library" button on media details when viewing another user's profile
- Read-only profile view: hides tracking controls, score editor, episode tracking, and Actions sidebar when viewing another user's media
- `can_edit` / `can_delete` context variables in list_detail view for cleaner template ownership checks
- 17 new tests covering social sharing scenarios (profile view, list privacy, Add to My Library, can_edit/can_delete)

### Changed

- Privacy toggle description updated to reference "other users" instead of "anonymous visitors"
- List edit button now uses `can_edit` context variable instead of inline `{% if user == custom_list.owner %}` check
- Delete list button now uses `can_delete` context variable
- `is_public` toggle in list_form.html now uses the project's standard Tailwind `peer` CSS pattern (matching preferences.html and account.html), replacing broken Alpine.js custom switch

### Fixed

- Login/signup crash when Redis is not running (graceful fallback to LocMemCache)
- Static files 404 when DEBUG=False (created .env with DEBUG=True)
- Privacy logic redesigned: tracked items in private lists are now properly hidden from other users (single source of truth in `get_private_item_ids`)
- TV show privacy cascades to all seasons and episodes (same media_id/source)
- Avatar display on People page: explicit 48x48 dimensions prevent image distortion
- Moving an item to a private list no longer requires removing tracking records to maintain privacy
- Changing tracking status/score no longer exposes previously hidden items
- `get_private_item_ids` returns a list (not queryset) to avoid ORM evaluation issues downstream
- public_profile view FieldError on `progressed_at` for Season model (property vs DB field conflict)
- Removed unused `total_episodes` calculation from public_profile view
- AnonymousUser crash in base template when accessing public pages (get_sidebar_media_types, get_search_media_types, media_type_readable filters)
- Decorator ordering on user_directory view (login_not_required must be outermost)
- pyrate_limiter Redis connection fallback to InMemoryBucket when Redis unavailable
- List form "Is Public" toggle broken (missing x-ref="input" on checkbox for Alpine.js)
- List form "Is Public" toggle visually broken (white circle only, no ON/OFF state) — replaced broken Alpine.js custom switch with Tailwind `peer` CSS pattern used elsewhere in the project
- Public profile links to media details now pass from_profile parameter for read-only context
- HTMX HX-Location fallback in base.html to ensure page updates after form submissions
- `media_details.html` Alpine.js x-data typo (`{ tab: 'overview' } }` extra `}`) that broke all tab switching
- Orphaned `{% endwith %}` tag in media_details.html causing TemplateSyntaxError
- `season_details` view missing profile context support (profile_user, profile_medias, is_profile_view)
- Private list items leaking to public profiles via get_private_item_ids (now excludes items also in public lists)
- Avatar uploads succeed but images not displayed (nginx missing `/media/` location block)

---

## Version History

(Add released versions below)
