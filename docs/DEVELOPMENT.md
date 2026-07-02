# Development

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

## Tests

```bash
python -m pytest -q
```

The suite covers collectors, source-health fallback, diff behavior, notification routing, weekly merging, and dashboard/RSS rendering. New collectors should include tests for:

- A normal non-empty response
- An empty or malformed response
- A partially failed paginated response
- Last-known-good retention
- Initial baseline behavior
- Additions, revisions, removals, and relevant status transitions
- Presentation and notification routing

Run syntax and patch checks before handing off changes:

```bash
python -m py_compile collector.py main.py differ.py dashboard.py emailer.py config.py
git diff --check
```

## Architecture

- `collector.py` retrieves and normalizes source data.
- `main.py` applies health policy, stores snapshots, orchestrates diffs, and routes notifications.
- `differ.py` computes structured changes and keyword alerts.
- `dashboard.py` renders the dashboard and RSS artifacts.
- `emailer.py` renders and sends email, Webex, and webhook messages.
- `config.py` defines endpoints, thresholds, keywords, and environment-backed settings.

## Safe change workflow

1. Add or update collector fixtures before changing comparison behavior.
2. Keep fetch-success metadata separate from returned records so valid empty results can be distinguished from failures.
3. Apply fallback at the narrowest safe scope; do not block healthy domains.
4. Baseline a newly introduced source once without alerts.
5. Test generated dashboard, RSS, email, and Webex payloads without sending them.
6. Finish with a live read-only collection in dry-run mode.
