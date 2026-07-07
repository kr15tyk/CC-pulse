# CC Pulse

CC Pulse monitors public cybersecurity certification and standards sources, compares daily snapshots, and reports meaningful changes without treating collection failures as real removals.

[Live dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)

## Coverage

| Source | Monitored content |
|---|---|
| NIAP | Products, Protection Profiles, Technical Decisions, announcements, events, and policy letters |
| NSA CSfC | Components List, announcements, capability packages, and selection documents |
| NIST CSRC | News, FIPS publications, CMVP, and post-quantum standards |
| CC Portal | International news, Protection Profiles, and certified products |
| NATO NIAPCL | Certified products and Cisco-specific changes |
| EUCC / ENISA | Scheme requirements and certificates |
| CCTL labs | New laboratory posts |

The tool detects additions, revisions, status changes, removals, and document replacements. Suspiciously empty or incomplete collections retain their last-known-good data. Operational failures stay out of the public dashboard and Webex; persistent failures are escalated internally.

## Quick start

Requires Python 3.10 or newer (the code uses `X | None` type syntax). On macOS the system `python3` is 3.9; install a newer interpreter (e.g. `brew install python@3.12`) and build the venv from it.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt

python main.py --bootstrap       # establish the first snapshot
CC_DRY_RUN=true python main.py   # collect and render without notifications
python main.py                   # daily collection and diff
python main.py --weekly          # seven-day digest
python -m pytest -q              # test suite
```

Notification credentials and storage paths are configured with `CC_*` environment variables. See [Operations](docs/OPERATIONS.md) for the supported settings and deployment flow.

## Documentation

- [Monitoring coverage and change detection](docs/MONITORING.md)
- [Operations, health checks, and validation](docs/OPERATIONS.md)
- [Notification routing](docs/NOTIFICATIONS.md)
- [Development and testing](docs/DEVELOPMENT.md)

The scheduled workflow is defined in [`.github/workflows/cc_pulse.yml`](.github/workflows/cc_pulse.yml).
