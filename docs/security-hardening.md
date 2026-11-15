# Security hardening and upgrade notes

## TMDB proxy is superuser-only

The TMDB proxy in Settings > Advanced applies to every user's TMDB requests, so
only superusers can set it now, and only superusers' values are used. If
several superusers set one, the earliest-created superuser's proxy wins. A
proxy saved earlier by an ordinary user stays in the database but is ignored:
after upgrading, TMDB requests go direct unless a superuser already has a
configured proxy, or until a superuser sets one.

## Celery JSON transition

Celery task and result serializers and accepted content are JSON only. Workers
reject old pickle messages rather than executing them. Uploads already use
shared staging paths; the calendar API now sends a user ID rather than a User
instance. Custom task publishers must send JSON-compatible values (Kombu also
supports typed dates and bytes), not Django models, querysets or open files.

Before upgrading an existing installation:

1. Record the old image digest or source commit. Make and verify a database
   backup and retain the matching configuration and original signing/encryption
   key (`SECRET`, `SECRET_FILE`, or generated `secret_key`). Retain import source
   files too. Follow the [backup and restore guidance](deployment.md#sqlite-data-paths);
   a CSV export alone cannot restore accounts, credentials and settings.
2. Stop new submissions and all beat/scheduled producers. Keep the old workers
   running on the trusted broker until outstanding work finishes. Check active,
   reserved and scheduled tasks and **all** configured queues (`celery`,
   `interactive`, `discover`, and any custom queues), including priority queues
   and Redis unacknowledged deliveries. The container's background worker embeds
   beat (`--beat`): stopping only a separate beat service does not stop that
   producer. Drain using old-version workers without beat while submissions and
   the ordinary application containers are stopped.
3. Stop all old workers and publishers together, then deploy the new version.
   Do not use a mixed-version rolling deployment for this transition.
4. If draining is impossible, explicitly cancel/requeue unfinished imports and
   jobs through their normal workflows. Old pickle messages will be rejected
   and discarded by new workers; they will not be converted automatically, and
   old task rows may continue to show pending. Do not flush a shared Redis or
   delete application data to perform this upgrade.

An ordinary `docker compose pull && docker compose up -d` does **not** perform
this drain. Queued pickle messages are discarded on delivery; reserved and ETA
messages restored after worker shutdown are discarded too. An abrupt stop can
leave deliveries in Redis's `unacked` hash/sorted set until the visibility timeout
expires (normally at least an hour). An empty ready queue alone is therefore not
proof of a completed drain. Obtain replies from every expected old worker for
`celery -A config inspect active`, `reserved`, `scheduled`, and `active_queues`;
verify all configured priority queues and unacknowledged deliveries are empty on
the isolated broker or application key prefix. Missing worker replies do not
mean no work. Run those inspection commands with the trusted old release's
configuration: old control replies also use pickle, which a JSON-only client
refuses. Do not re-enable pickle in the upgraded deployment to inspect them.
ETA jobs must finish on trusted old workers or be explicitly
cancelled and recreated; waiting only for currently active jobs is insufficient.

Rejected messages do not invoke task-failure handlers. A tracked `TaskResult`
can remain `PENDING`; the imports screen hides pending rows after 30 minutes and
housekeeping later removes them, without replaying their task. Such rows are
status records, not a durable copy of the original arguments or uploaded file.
Cancel a pending import through the imports screen while available, retain its
source file, and submit a fresh job after upgrading. For an interrupted running
import, inspect Recent Import Runs and its partial changes before cancelling,
rolling back where supported, or resubmitting: replay is not universally
idempotent. Durable import/outbox recovery covers its own workflows, not every
Celery task. JSON serialization alone supplies no generic lost-job recovery.

The prior default result serializer was already JSON. Historical non-JSON
results from custom deployments are no longer readable. Retain a backup and
re-run the supported job if its result is still needed. On rollback, stop new
publishers/workers first; old workers accept JSON, but do not resume accepting
pickle on an untrusted broker.

If a worker logs a rejected pickle message whose method is `ping`, it is a
Celery control command, not evidence of a failed Xbox sync or a queued task.
Floppy's health-check ping uses the JSON-configured Celery app. Check for old
Floppy processes, other Celery applications, monitoring tools, and custom
publishers using the same broker and control exchange; the message alone does
not identify its sender. Upgrade or reconfigure the publisher to use JSON, or
isolate deployments using separate brokers or distinct `REDIS_PREFIX` values.
Changing a prefix also changes the queues and cache keys: coordinate all
processes of a deployment and drain or explicitly cancel outstanding work as
described above before switching. Do not enable pickle or flush Redis.

JSON removes executable broker-message deserialization, not the need to protect
Redis: anyone able to publish can invoke registered tasks with forged arguments.
The Django Redis cache also retains its Python object serializer. Keep Redis on
a trusted network; use authenticated/TLS Redis URLs for a remote service.

## HTTPS and deployment settings

LAN HTTP remains supported. For an HTTPS-only deployment, pass these environment
values to Floppy (a Compose `.env` alone does not pass arbitrary values through):

```text
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
ALLOWED_HOSTS=floppy.example.com
URLS=https://floppy.example.com
REGISTRATION=False
USE_X_FORWARDED_HOST=False
USE_X_FORWARDED_PORT=False
```

Enable `USE_X_FORWARDED_PROTO=True` only behind a trusted proxy that replaces
client-supplied `X-Forwarded-Proto`, and prevent direct client access to the app
listener. Host/port forwarding is separately configurable; leave it off unless
required and sanitized. `URLS` adds trusted origins and hosts; it does not enable
TLS or remove Docker's wildcard default. Set `ALLOWED_HOSTS` explicitly.

Terminate TLS and enforce HTTP-to-HTTPS redirects at the proxy. Enable HSTS there
only after testing HTTPS and deciding which subdomains it covers. Secure cookies
alone do not redirect HTTP. Preserve `SECRET` across upgrades; changing it can
invalidate sessions and make encrypted integration credentials unreadable.

Registration remains on for first-run provisioning; disable it afterward for a
private instance. Keep demo and auto-login off. The shipped Compose stacks publish
only the application port, not Redis/PostgreSQL, and use a stack bridge network.
Do not attach untrusted containers or publish datastore ports. Host networking,
shared namespaces, external Redis, and additional proxy networks need operator
review. No network topology or existing database password is changed here.

## Account recovery

Enabling, replacing or disabling an authenticator and regenerating recovery codes
require the current local password. Password checks use allauth's per-user
`reauthenticate` rate limit. TOTP remains a password-recovery mechanism, not login
MFA. Users with an unusable local password must establish one through their
supported account flow before using these controls; this does not add an SSO
reauthentication protocol. Initial pending QR setup remains visible to the
signed-in user, and pending setup is not a substitute for reauthentication.

## Startup recovery downloads

The startup listener runs before Django on the application port. Its incident
page and diagnostic details remain anonymous so recovery works without Django,
Redis or working authentication. Restrict this listener at the proxy/firewall
when the app is reachable beyond trusted operators. Proxy access rules must
cover the entire application listener, including `/backup`, `/retry`, `/accept`
and `/quarantine`, rather than only normal Django routes.

Database download still defaults off. To enable it temporarily, set both:

- `FLOPPY_SQLITE_ALLOW_BACKUP_DOWNLOAD=true`
- `FLOPPY_SQLITE_BACKUP_TOKEN` to a separately generated random value of at least
  32 characters (for example, `python -c 'import secrets; print(secrets.token_urlsafe(32))'`).

The page now requires this token in a POST form. GET downloads, missing/short
server tokens, wrong tokens and cross-origin browser requests are refused. The
server never embeds the token in the page or URL. Use HTTPS or a trusted local
connection, remove both settings after recovery, and protect downloaded files
like the live database. Existing scripts using GET `/backup` must switch to
POST with a `token` form field. The incident approval code for repairs remains
separate. Prefer copying a verified snapshot directly from the protected volume.
