# Operational commands

The checked-in Compose services are `yamtrack` and `redis`.

## Import dry-run

```bash
docker compose exec yamtrack python manage.py import_anime_franchise --profile continuity --dry-run
```

## Import by profile

```bash
docker compose exec yamtrack python manage.py import_anime_franchise --profile continuity
docker compose exec yamtrack python manage.py import_anime_franchise --profile satellites
docker compose exec yamtrack python manage.py import_anime_franchise --profile complete
```

## Full rescan

```bash
docker compose exec yamtrack python manage.py import_anime_franchise --profile complete --full-rescan
```

## Refresh MAL provider cache during import

```bash
docker compose exec yamtrack python manage.py import_anime_franchise --profile continuity --refresh-cache
```

## Limit by user or count

```bash
docker compose exec yamtrack python manage.py import_anime_franchise --profile satellites --user-id 1 --limit 10
```

## Schedule franchise cache rebuild

```bash
docker compose exec yamtrack python manage.py shell -c "from app.tasks import build_mal_anime_franchise_payload; build_mal_anime_franchise_payload.delay('34161')"
```

## Inspect Redis cache

```bash
docker compose exec redis redis-cli keys '*anime*franchise*'
docker compose exec redis redis-cli get mal_anime_franchise_34161
docker compose exec redis redis-cli get mal_anime_franchise_34161:meta
docker compose exec redis redis-cli get mal_anime_franchise_alias_34428
docker compose exec redis redis-cli get mal_anime_franchise_34161:aliases
```

## Delete targeted cache

```bash
docker compose exec redis redis-cli del mal_anime_franchise_34161 mal_anime_franchise_34161:meta mal_anime_franchise_34161:aliases
docker compose exec redis redis-cli del mal_anime_franchise_alias_34428
```

## Inspect settings

```bash
docker compose exec yamtrack python manage.py shell -c "from django.conf import settings; names=['ANIME_FRANCHISE_GROUPING_ENABLED','ANIME_FRANCHISE_IMPORT_AUTOMATION_ENABLED','ANIME_FRANCHISE_IMPORT_AUTOMATION_INTERVAL_MINUTES','ANIME_FRANCHISE_IMPORT_AUTOMATION_PROFILE','ANIME_FRANCHISE_IMPORT_AUTOMATION_REFRESH_CACHE','ANIME_FRANCHISE_IMPORT_AUTOMATION_FULL_RESCAN','ANIME_FRANCHISE_IMPORT_AUTOMATION_LIMIT','ANIME_FRANCHISE_CACHE_TTL_DAYS','ANIME_FRANCHISE_CACHE_ALIASES_ENABLED','ANIME_FRANCHISE_CACHE_FRESH_DAYS','ANIME_FRANCHISE_BUILD_COOLDOWN_HOURS','ANIME_FRANCHISE_RETRY_AFTER_ERROR_HOURS','ANIME_FRANCHISE_QUEUE_LOCK_MINUTES','ANIME_FRANCHISE_TASK_LOCK_MINUTES','ANIME_FRANCHISE_MAX_NODES','ANIME_FRANCHISE_PAYLOAD_SCHEMA_VERSION','MAL_RATE_LIMIT_PER_MINUTE']; print({n:getattr(settings,n) for n in names})"
```

## Restart services

```bash
docker compose restart yamtrack
docker compose restart redis
```

If your deployment adds separate worker or beat services, restart those deployment-specific service names too. They do not exist in the checked-in Compose files.

## Logs

```bash
docker compose logs -f yamtrack
docker compose logs -f redis
```

## Rebuild Anime Series View

Use dry-run first, then run the write command when the planned changes look correct:

```bash
docker compose exec yamtrack python manage.py rebuild_anime_series_view --all-users --dry-run
docker compose exec yamtrack python manage.py rebuild_anime_series_view --all-users
docker compose exec yamtrack python manage.py rebuild_anime_series_view --user-id 1 --dry-run
docker compose exec yamtrack python manage.py rebuild_anime_series_view --user-id 1 --limit 20 --dry-run
```

## Inspect Anime Series View memberships

Count tracked, projected, and unprojected MAL anime for one user:

```bash
docker compose exec yamtrack python manage.py shell -c "from app.models import Anime, AnimeSeriesViewMembership, Sources; tracked=set(Anime.objects.filter(user_id=1, item__source=Sources.MAL.value).values_list('item__media_id', flat=True)); projected=set(AnimeSeriesViewMembership.objects.filter(user_id=1, media_id__in=tracked).values_list('media_id', flat=True)); print({'tracked': len(tracked), 'projected': len(projected), 'unprojected': len(tracked-projected)})"
```

List memberships by root when a franchise appears split or merged incorrectly:

```bash
docker compose exec yamtrack python manage.py shell -c "from app.models import AnimeSeriesViewMembership; from collections import defaultdict; rows=AnimeSeriesViewMembership.objects.filter(user_id=1).order_by('root_media_id','media_id').values_list('root_media_id','media_id','display_title','group_kind','projection_version'); roots=defaultdict(list); [roots[root].append((media,title,kind,version)) for root,media,title,kind,version in rows]; [print(root, members) for root,members in roots.items()]"
```
