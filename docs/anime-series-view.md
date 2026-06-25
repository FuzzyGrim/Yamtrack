# Anime Series View

## Product behavior

Anime Series View is the anime-list presentation that groups the user's tracked MAL anime into franchise cards. It is distinct from the MAL anime detail page: the detail page explains one MAL franchise, while Anime Series View reorganizes the user's Anime library.

- The `series` layout applies only to the Anime media list.
- Non-anime media types fall back to the normal grid/table layouts.
- A large MAL franchise is shown as one card when the projection is confident.
- A tracked anime remains a singleton when there is no reliable groupable evidence.
- Non-confident projections are not persisted.
- Cards do not render member titles; they summarize the group instead.

## Architecture

```text
AnimeFranchiseSnapshotService
    ↓
AnimeSeriesViewProjectionBuilder
    ↓
AnimeSeriesViewFranchiseRefreshService
    ↓
AnimeSeriesViewMembership
    ↓
build_anime_series_view
    ↓
anime_series_groups.html / anime_series_group_card.html
```

`media_list` must remain DB-only. Do not add a MAL provider call, snapshot build, DB write, cache build, or Celery scheduling to `build_anime_series_view` or to Series View rendering. Refresh work belongs to triggers, tasks, and rebuild commands before the reader runs.

## Projection rules

The projection consumes franchise snapshots and uses the real Series View rule set, not the detail-page UI sections.

Groupable relations are:

- `prequel`
- `sequel`
- `parent_story`
- `full_story`
- `side_story`
- `spin_off`
- `alternative_setting`
- `alternative_version`

Compatible root media types are:

- `tv`
- `ona`
- `movie`
- `ova`

Independent continuity boundaries are intentionally narrow:

- `alternative_version` is the only alternative relation used as a boundary.
- `alternative_setting` remains groupable and is not a boundary.
- The independent continuity media types affected by the boundary are `tv`, `ona`, and `ova`.
- `movie` is a compatible root media type, but it does not trigger the autonomous `alternative_version` boundary behavior.

## Persisted read model

`AnimeSeriesViewMembership` is the per-user read model consumed by the list reader. It stores:

- `user`
- `media_id`
- `root_media_id`
- `display_media_id`
- `display_title`
- `display_image`
- `display_media_type`
- `display_start_date`
- `group_kind`
- `projection_version`
- `created_at`
- `updated_at`

Persistence constraints and read-path indexes:

- unique constraint on `(user, media_id)`;
- indexes on `(user, media_id)`, `(user, root_media_id)`, and `(user, projection_version)`;
- `projection_version = franchise_root_v2`;
- `group_kind = franchise | singleton`.

Memberships are not global. They are per-user rows for the user's tracked anime.

## Refresh triggers

Series View membership refresh is asynchronous after user-library changes:

- manual MAL anime add -> `schedule_manual_add`;
- delete MAL anime -> `schedule_delete`;
- import-created anime -> `schedule_import_batch`.

Operational behavior:

- refresh is scheduled after the surrounding transaction commits;
- Celery task `Refresh Anime Series View franchise projection` performs the refresh;
- queue locks prevent duplicate refresh scheduling for the same user/media/mode tuple;
- the queue lock is deleted in `finally` when the task exits;
- normal refresh is non-destructive until a projection succeeds;
- delete mode removes direct memberships for deleted media and then rebuilds affected groups.

## Reader and UI rendering

`build_anime_series_view` reads persisted memberships and groups tracked entries by `root_media_id`. It does not build snapshots and does not call MAL.

Series View pagination is 12 groups per page. Each card renders the root image, root title, root media type, and the count of tracked entries. The card link points to the root through `media_url`.

`unprojected_count` is the count of tracked entries without an exploitable membership. When it is non-zero, the first Series View page shows the preparation message so the user understands that grouping is still being prepared.

## Rebuild command

Use the management command to backfill or repair memberships:

```bash
docker compose exec yamtrack python manage.py rebuild_anime_series_view --all-users --dry-run
docker compose exec yamtrack python manage.py rebuild_anime_series_view --all-users
docker compose exec yamtrack python manage.py rebuild_anime_series_view --user-id 1 --dry-run
docker compose exec yamtrack python manage.py rebuild_anime_series_view --user-id 1 --media-id 11757 --dry-run
```

Even with `refresh_cache=False`, a rebuild can contact MAL when the local provider cache does not contain the nodes required to build the snapshot.

## Debugging checklist

Check these symptoms from the read model back toward the projection and provider cache:

- `unprojected_count > 0`;
- stale membership;
- wrong root;
- franchise split into multiple cards;
- alternative continuity merged when it should remain separated;
- too many MAL calls during backfill;
- Celery refresh task failure.

## Boundaries and known examples

These examples explain the rules; do not hardcode these examples or their MAL IDs into product behavior.

- SAO + Gun Gale Online -> same Sword Art Online card when the snapshot confirms the groupable relationship.
- Code Geass branches -> same card if the snapshot confirms groupable franchise structure.
- Spice & Wolf old/remake -> separate cards through the `alternative_version` boundary.

## Future direction

The current model is a per-user read model. It is robust for this step. A future Spec 2 could replace or complement it with a global MAL franchise index:

```text
media_id -> root_media_id + display metadata
```

The projection should remain pure and independent of the user so this evolution stays possible.
