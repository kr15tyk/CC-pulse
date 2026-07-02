# Operations

## Run modes

```bash
python main.py --bootstrap   # initial snapshot; no diff or alerts
python main.py               # daily collection, diff, dashboard, and notifications
python main.py --merge       # merge matrix collector outputs
python main.py --weekly      # merge the latest seven daily diffs and send the digest
python main.py --redash      # rebuild the dashboard from the latest stored diff
python main.py --staging     # rebuild into docs/staging
```

Set `CC_DRY_RUN=true` to suppress Webex, webhook, and email sends while still exercising collection, diffing, persistence, and dashboard generation.

## Configuration

| Variable | Purpose |
|---|---|
| `CC_DRY_RUN` | Suppress outbound notifications |
| `CC_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `CC_SNAPSHOT_DIR` | Snapshot storage directory |
| `CC_DIFF_DIR` | Daily-diff storage directory |
| `CC_WEBEX_BOT_TOKEN` | Webex bot credential |
| `CC_WEBEX_ROOM_ID` | Webex destination |
| `CC_WEBHOOK_URL` | Optional generic webhook destination |
| `CC_SMTP_HOST`, `CC_SMTP_PORT` | SMTP server |
| `CC_EMAIL_USERNAME`, `CC_EMAIL_PASSWORD` | SMTP credentials |
| `CC_EMAIL_FROM`, `CC_EMAIL_RECIPIENTS` | Email sender and comma-separated recipients |

## Source health

Health states are:

- `healthy`: current collection passed its checks.
- `stale`: current collection failed and last-known-good data was retained.
- `failed`: current collection failed without usable prior data.

Degradation is visible in workflow logs and daily internal status email from the first failed run. A specific source or NIAP subcollection escalates by internal email on its third consecutive failure, then every seventh failed run. Health warnings are not placed on the public dashboard or in Webex.

## Validation before rollout

1. Run `python -m pytest -q` and require a fully green suite.
2. Run `CC_DRY_RUN=true python main.py` against the live sources.
3. Inspect the new snapshot, daily diff, generated dashboard, and RSS output.
4. Confirm new sources were silently baselined and no mass additions/removals appeared.
5. Confirm `source_health` accurately identifies any degraded collection.
6. Enable notifications only after the dry run is clean.

For deterministic validation, use fixture snapshots for added, revised, archived, removed, empty, partial, and recovered collection scenarios instead of waiting for a live source change.

## Storage

Daily snapshots default to `snapshots/`; daily diffs default to `snapshots/diffs/`. The dashboard and RSS files are generated under `docs/`. The scheduled workflow can redirect snapshot storage through `CC_SNAPSHOT_DIR` and `CC_DIFF_DIR`.
