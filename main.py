"""
main.py — Entry point for CC Pulse.

Features:
- Structured logging (respects config.LOG_LEVEL)
- Daily diff JSON saved to snapshots/diffs/ (decouples weekly from re-diffing)
- Weekly job merges pre-computed daily diff files (fast, no re-diff)
- Webex + immediate email alert fired after daily diff if keyword alerts found
- Graceful handling of SanityError (rejects bad snapshot, does not overwrite)
- Snapshot rotation: keeps last 30 daily snapshots + diffs (fix #4)
- Guard against double-run overwriting today's snapshot (fix #7)
- Matrix merge mode: assemble full snapshot from per-domain partial JSONs (issue #20)
- Per-domain health metadata with last-known-good fallback and escalation

Usage:
  python main.py                  # Daily pulse check
  python main.py --weekly         # Send weekly email from stored daily diffs
  python main.py --bootstrap      # Collect initial snapshot (no diff)
  python main.py --merge          # Assemble snapshot from snapshots/partial/*.json then diff/alert
"""

import argparse
import copy
import glob
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import config

# ── Logging setup ─────────────────────────────────────────────────────────────────────────────
def _setup_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    for noisy in ("urllib3", "requests", "feedparser"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("cc_pulse.main")

# ── Lazy imports (after logging is configured) ────────────────────────────────────────────
def _imports():
    import collector
    import differ
    import dashboard
    import emailer
    return collector, differ, dashboard, emailer

# ── Path helpers ───────────────────────────────────────────────────────────────────────────────
def snapshot_path(dt=None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    return os.path.join(config.SNAPSHOT_DIR, dt.strftime("%Y-%m-%d") + ".json")

def diff_path(dt=None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    os.makedirs(config.DIFF_DIR, exist_ok=True)
    return os.path.join(config.DIFF_DIR, dt.strftime("%Y-%m-%d") + "_diff.json")

def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(obj: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    log.info("Saved: %s", path)

def _latest_prior_snapshot() -> str | None:
    """Return the most recent snapshot file that is NOT today's."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pattern = os.path.join(config.SNAPSHOT_DIR, "*.json")
    files = sorted(glob.glob(pattern), reverse=True)
    for f in files:
        if today not in os.path.basename(f):
            return f
    return None

# ── Snapshot rotation (fix #4) ──────────────────────────────────────────────────────────────────────
KEEP_SNAPSHOTS = 30  # days

DOMAIN_KEYS = (
    "niap", "cc_portal", "cctl_labs", "csfc",
    "cc_crypto", "nato", "eucc", "nd_itc",
)

DOMAIN_LABELS = {
    "niap": "NIAP",
    "cc_portal": "CC Portal",
    "cctl_labs": "CCTL Labs",
    "csfc": "CSfC",
    "cc_crypto": "CC Crypto",
    "nato": "NATO NIAPCL",
    "eucc": "EUCC / ENISA",
    "nd_itc": "ND-iTC",
}

NIAP_SUBCOLLECTION_LABELS = {
    "news": "News announcements",
    "events": "Events",
    "policies": "Policy letters",
}


def _config_minimum(name: str, default: int) -> int:
    value = getattr(config, name, default)
    return value if isinstance(value, int) else default


def _apply_niap_subcollection_health(
    new_snapshot: dict,
    prior_snapshot: dict,
) -> dict[str, dict]:
    """Validate and selectively retain NIAP announcements and policies.

    NIAP's products, announcements, events, and policies are separate API
    calls.  Treating them as one all-or-nothing domain either accepts an empty
    feed or freezes healthy product data.  This helper applies last-known-good
    fallback only to a failed subcollection.
    """
    new_niap = new_snapshot.setdefault("niap", {})
    prior_niap = prior_snapshot.get("niap", {})
    metadata = new_niap.get("_collection_health")
    if not isinstance(metadata, dict):
        # Legacy/manual snapshots have no request outcome metadata. Preserve
        # backward compatibility rather than guessing whether an empty list is
        # a successful response.
        return {}

    prior_metadata = prior_niap.get("_collection_health", {})
    prior_subhealth = (
        prior_snapshot.get("source_health", {})
        .get("niap", {})
        .get("subcollections", {})
    )
    minimums = {
        "news": _config_minimum("SANITY_MIN_NIAP_NEWS", 1),
        "events": 0,  # a successful events query can legitimately be empty
        "policies": _config_minimum("SANITY_MIN_NIAP_POLICIES", 1),
    }
    result: dict[str, dict] = {}

    for key, label in NIAP_SUBCOLLECTION_LABELS.items():
        current_items = new_niap.get(key, [])
        prior_items = prior_niap.get(key, [])
        meta = metadata.get(key, {}) if isinstance(metadata.get(key), dict) else {}
        prior_meta = (
            prior_metadata.get(key, {})
            if isinstance(prior_metadata.get(key), dict) else {}
        )
        observed = len(current_items) if isinstance(current_items, list) else 0
        prior_count = len(prior_items) if isinstance(prior_items, list) else 0
        minimum = minimums[key]

        successful = meta.get("success") is True and meta.get("complete", True) is True
        enough = observed >= minimum
        suspicious_drop = (
            key in ("news", "policies")
            and prior_count >= 10
            and observed < max(minimum, prior_count // 2)
        )
        current_ok = successful and enough and not suspicious_drop

        if current_ok:
            result[key] = {
                "label": label,
                "status": "healthy",
                "observed": observed,
                "consecutive_failures": 0,
                "using_last_known_good": False,
            }
            continue

        previous = prior_subhealth.get(key, {})
        previous_failures = (
            previous.get("consecutive_failures", 0)
            if previous.get("status") in ("stale", "failed") else 0
        )
        prior_was_successful = (
            previous.get("status") == "healthy"
            or prior_meta.get("success") is True
            or prior_count >= max(1, minimum)
        )
        # Events can be a valid empty collection, but only when a previous run
        # explicitly recorded that successful state.
        if key == "events" and prior_count == 0:
            prior_was_successful = (
                previous.get("status") == "healthy"
                or prior_meta.get("success") is True
            )
        if prior_was_successful:
            new_niap[key] = copy.deepcopy(prior_items)

        reasons = []
        if not successful:
            reasons.append(meta.get("detail") or "request failed or was incomplete")
        if not enough:
            reasons.append(f"returned {observed}; minimum expected {minimum}")
        if suspicious_drop:
            reasons.append(f"suspicious drop from {prior_count} to {observed}")
        result[key] = {
            "label": label,
            "status": "stale" if prior_was_successful else "failed",
            "observed": observed,
            "consecutive_failures": previous_failures + 1,
            "using_last_known_good": prior_was_successful,
            "detail": "; ".join(reasons),
        }
        log.warning(
            "[Health] NIAP %s is %s (failure #%d): %s",
            key,
            result[key]["status"],
            result[key]["consecutive_failures"],
            result[key]["detail"],
        )

    return result


def _source_health_checks(snapshot: dict) -> dict[str, list[dict]]:
    """Return the minimum viable collection checks for every source domain.

    These checks deliberately measure stable, representative collections rather
    than every page/feed. A failed check marks the domain unusable for diffing;
    callers can then retain the last-known-good domain data instead of treating
    a fetch failure as a real mass removal.
    """
    niap = snapshot.get("niap", {})
    cc_portal = snapshot.get("cc_portal", {})
    cctl_labs = snapshot.get("cctl_labs", {})
    csfc = snapshot.get("csfc", {})
    cc_crypto = snapshot.get("cc_crypto", {})
    nato = snapshot.get("nato", {})
    eucc = snapshot.get("eucc", {})

    return {
        "niap": [
            {"name": "PCL products", "observed": len(niap.get("pcl", [])),
             "minimum": config.SANITY_MIN_PCL},
            {"name": "Protection Profiles", "observed": len(niap.get("pps", [])),
             "minimum": config.SANITY_MIN_PPS},
        ],
        "cc_portal": [
            {"name": "portal records", "observed": sum(
                len(cc_portal.get(key, [])) for key in ("news", "pps", "products")
            ), "minimum": 1},
        ],
        "cctl_labs": [
            {"name": "lab feed items", "observed": sum(
                len(items) for items in cctl_labs.values() if isinstance(items, list)
            ), "minimum": 1},
        ],
        "csfc": [
            {"name": "APL items", "observed": len(csfc.get("pages", {}).get("apl", [])),
             "minimum": config.SANITY_MIN_CSFC_APL},
            {"name": "announcements", "observed": len(
                csfc.get("pages", {}).get("announcements", [])
            ), "minimum": config.SANITY_MIN_CSFC_ANNOUNCEMENTS},
        ],
        "cc_crypto": [
            {"name": "publications", "observed": len(
                cc_crypto.get("pages", {}).get("publications", [])
            ), "minimum": config.SANITY_MIN_CC_CRYPTO_PUBS},
        ],
        "nato": [
            {"name": "NIAPCL products", "observed": len(
                nato.get("pages", {}).get("all_products", [])
            ), "minimum": config.SANITY_MIN_NATO_PRODUCTS},
        ],
        "eucc": [
            {"name": "certificates", "observed": len(
                eucc.get("pages", {}).get("certificates", [])
            ), "minimum": config.SANITY_MIN_EUCC_CERTS},
        ],
        "nd_itc": [
            {"name": "NIT RFIs", "observed": len(
                snapshot.get("nd_itc", {}).get("nit_rfis", [])
            ), "minimum": config.SANITY_MIN_ND_ITC_RFIS},
            {"name": "Allowed-With entries", "observed": len(
                snapshot.get("nd_itc", {}).get("awl_entries", [])
            ), "minimum": 1},
        ],
    }


def _checks_pass(checks: list[dict]) -> bool:
    return bool(checks) and all(
        check.get("observed", 0) >= check.get("minimum", 1)
        for check in checks
    )


def _collection_iter(domain_data: dict):
    """Yield (path, list) for every diffable list collection in a domain:
    top-level lists plus lists nested one level under `pages` / `feeds`.
    Dict-of-dict structures (doc_headers, component_selection_hashes) are
    hash-compared elsewhere, not item-diffed, so they're skipped here."""
    if not isinstance(domain_data, dict):
        return
    for key, val in domain_data.items():
        if isinstance(val, list):
            yield (key, val)
        elif key in ("pages", "feeds") and isinstance(val, dict):
            for subkey, subval in val.items():
                if isinstance(subval, list):
                    yield (f"{key}.{subkey}", subval)


def _apply_collection_collapse_guard(
    new_snapshot: dict,
    prior_snapshot: dict,
    source_health: dict[str, dict],
) -> None:
    """Retain last-known-good for secondary collections that partially
    collapsed while their domain still passed its representative check.

    Without this, a domain marked 'healthy' can still emit false mass-removal
    diffs when one of its pages/feeds silently drops to near-zero. Runs only on
    healthy domains (stale/failed domains already reverted wholesale). NIAP
    news/events/policies are excluded — the sub-collection health check owns
    those with more precise per-request metadata.
    """
    if not isinstance(prior_snapshot, dict):
        return
    baseline = _config_minimum("COLLAPSE_MIN_BASELINE", 8)
    niap_owned = {"news", "events", "policies"}

    for domain in DOMAIN_KEYS:
        health = source_health.get(domain, {})
        if health.get("status") != "healthy":
            continue
        new_data = new_snapshot.get(domain, {})
        prior_data = prior_snapshot.get(domain, {})
        if not isinstance(new_data, dict) or not isinstance(prior_data, dict):
            continue

        prior_counts = {
            path: len(lst) for path, lst in _collection_iter(prior_data)
        }
        collapses = []
        for path, lst in _collection_iter(new_data):
            if domain == "niap" and path in niap_owned:
                continue
            prior_count = prior_counts.get(path, 0)
            observed = len(lst)
            if prior_count >= baseline and observed < prior_count // 2:
                # Retain the prior collection in place of the collapsed one.
                if "." in path:
                    parent, child = path.split(".", 1)
                    new_data.setdefault(parent, {})[child] = copy.deepcopy(
                        prior_data.get(parent, {}).get(child, [])
                    )
                else:
                    new_data[path] = copy.deepcopy(prior_data.get(path, []))
                collapses.append(f"{path} {observed}/{prior_count}")

        if not collapses:
            continue

        prior_health = prior_snapshot.get("source_health", {}).get(domain, {})
        previous_failures = (
            prior_health.get("consecutive_failures", 0)
            if prior_health.get("status") in ("stale", "failed") else 0
        )
        detail = "collection collapse (retained last-known-good): " + "; ".join(collapses)
        existing_detail = health.get("detail")
        health.update({
            "status": "stale",
            "consecutive_failures": previous_failures + 1,
            "using_last_known_good": True,
            "detail": f"{existing_detail}; {detail}" if existing_detail else detail,
        })
        log.warning(
            "[Health] %s degraded to stale (failure #%d): %s",
            domain, health["consecutive_failures"], detail,
        )


def _apply_source_health(
    new_snapshot: dict,
    prior_snapshot: dict | None = None,
    collection_errors: set[str] | None = None,
) -> dict:
    """Attach health metadata and retain last-known-good failed domains.

    Status meanings:
      healthy -- current collection passed its minimum checks
      stale   -- current collection failed; prior healthy data was retained
      failed  -- current collection failed and no usable prior data exists
    """
    collection_errors = collection_errors or set()
    prior_snapshot = prior_snapshot or {}
    niap_subcollections = _apply_niap_subcollection_health(
        new_snapshot, prior_snapshot
    )
    current_checks = _source_health_checks(new_snapshot)
    prior_checks = _source_health_checks(prior_snapshot)
    prior_health = prior_snapshot.get("source_health", {})
    checked_at = new_snapshot.get("collected_at", datetime.now(timezone.utc).isoformat())
    source_health: dict[str, dict] = {}

    for domain in DOMAIN_KEYS:
        checks = current_checks[domain]
        current_ok = domain not in collection_errors and _checks_pass(checks)
        partial_failures = {
            key: health
            for key, health in niap_subcollections.items()
            if health.get("status") != "healthy"
        } if domain == "niap" else {}
        if current_ok and partial_failures:
            previous = prior_health.get(domain, {})
            previous_failures = (
                previous.get("consecutive_failures", 0)
                if previous.get("status") in ("stale", "failed") else 0
            )
            all_have_fallback = all(
                health.get("using_last_known_good")
                for health in partial_failures.values()
            )
            source_health[domain] = {
                "label": DOMAIN_LABELS[domain],
                "status": "stale" if all_have_fallback else "failed",
                "checks": checks,
                "subcollections": niap_subcollections,
                "consecutive_failures": previous_failures + 1,
                "using_last_known_good": any(
                    health.get("using_last_known_good")
                    for health in partial_failures.values()
                ),
                "detail": "; ".join(
                    f"{health['label']}: {health.get('detail', health['status'])}"
                    for health in partial_failures.values()
                ),
                "checked_at": checked_at,
            }
            continue
        if current_ok:
            source_health[domain] = {
                "label": DOMAIN_LABELS[domain],
                "status": "healthy",
                "checks": checks,
                "consecutive_failures": 0,
                "using_last_known_good": False,
                "checked_at": checked_at,
            }
            if domain == "niap" and niap_subcollections:
                source_health[domain]["subcollections"] = niap_subcollections
            continue

        previous = prior_health.get(domain, {})
        previous_failures = (
            previous.get("consecutive_failures", 0)
            if previous.get("status") in ("stale", "failed") else 0
        )
        has_last_known_good = (
            domain in prior_snapshot
            and _checks_pass(prior_checks.get(domain, []))
        )
        if has_last_known_good:
            new_snapshot[domain] = copy.deepcopy(prior_snapshot[domain])

        failed_checks = [
            f"{check['name']} {check['observed']}/{check['minimum']}"
            for check in checks
            if check.get("observed", 0) < check.get("minimum", 1)
        ]
        if domain in collection_errors:
            failed_checks.insert(0, "collector output missing or unreadable")

        source_health[domain] = {
            "label": DOMAIN_LABELS[domain],
            "status": "stale" if has_last_known_good else "failed",
            "checks": checks,
            "consecutive_failures": previous_failures + 1,
            "using_last_known_good": has_last_known_good,
            "detail": "; ".join(failed_checks) or "collector failed",
            "checked_at": checked_at,
        }
        if domain == "niap" and niap_subcollections:
            source_health[domain]["subcollections"] = niap_subcollections
        log.warning(
            "[Health] %s is %s (failure #%d): %s",
            domain,
            source_health[domain]["status"],
            source_health[domain]["consecutive_failures"],
            source_health[domain]["detail"],
        )

    _apply_collection_collapse_guard(new_snapshot, prior_snapshot, source_health)
    new_snapshot["source_health"] = source_health
    return new_snapshot


def _diff_baseline_with_recoveries(old_snapshot: dict, new_snapshot: dict) -> dict:
    """Baseline a source's first healthy collection instead of alerting it all.

    If the prior snapshot did not meet a domain's minimum checks, there is no
    trustworthy prior dataset to diff against. Treat the newly healthy data as
    that domain's baseline; subsequent runs will report real changes normally.
    """
    old_checks = _source_health_checks(old_snapshot)
    new_checks = _source_health_checks(new_snapshot)
    recovered = {
        domain for domain in DOMAIN_KEYS
        if not _checks_pass(old_checks[domain]) and _checks_pass(new_checks[domain])
    }
    baseline = copy.deepcopy(old_snapshot)
    for domain in recovered:
        baseline[domain] = copy.deepcopy(new_snapshot[domain])
        if domain == "csfc":
            # A first healthy CSfC collection can follow several days of empty
            # WAF-blocked snapshots. Baseline the recovered bulk page data to
            # prevent a flood, but preserve recently dated Selection-document
            # URLs so genuine updates published during the outage are diffed.
            recent_selection_updates = _recent_dated_csfc_selection_links(new_snapshot)
            prior_links = old_snapshot.get("csfc", {}).get("selection_links", {})
            baseline_links = baseline[domain].get("selection_links", {})
            for heading in recent_selection_updates:
                if heading in prior_links:
                    baseline_links[heading] = prior_links[heading]
                else:
                    baseline_links.pop(heading, None)
                log.info(
                    "[Health] Preserving recent CSfC Selection update during "
                    "recovery: %s",
                    heading,
                )
        log.info(
            "[Health] %s produced its first healthy collection; baselining without alerts.",
            domain,
        )

    # Baseline a newly introduced NIAP subcollection once. A recovery that
    # already has retained last-known-good data is intentionally *not*
    # baselined, so changes that happened during the outage are still detected.
    old_niap = old_snapshot.get("niap", {})
    new_niap = new_snapshot.get("niap", {})
    old_subhealth = (
        old_snapshot.get("source_health", {}).get("niap", {}).get("subcollections", {})
    )
    old_collection_metadata = old_niap.get("_collection_health", {})
    new_subhealth = (
        new_snapshot.get("source_health", {}).get("niap", {}).get("subcollections", {})
    )
    for key in NIAP_SUBCOLLECTION_LABELS:
        new_ok = new_subhealth.get(key, {}).get("status") == "healthy"
        old_health = old_subhealth.get(key, {})
        old_meta = (
            old_collection_metadata.get(key, {})
            if isinstance(old_collection_metadata.get(key), dict) else {}
        )
        no_trustworthy_baseline = (
            key not in old_niap
            or (
                old_health.get("status") == "failed"
                and not old_health.get("using_last_known_good")
            )
            or (
                old_meta.get("success") is False
                and not old_health.get("using_last_known_good")
            )
        )
        if new_ok and no_trustworthy_baseline:
            baseline.setdefault("niap", {})[key] = copy.deepcopy(new_niap.get(key, []))
            log.info(
                "[Health] NIAP %s produced its first healthy collection; "
                "baselining without alerts.",
                key,
            )

    return baseline if recovered or baseline != old_snapshot else old_snapshot


CSFC_RECOVERY_LOOKBACK_DAYS = 14
_CSFC_DOCUMENT_DATE = re.compile(r"(?<!\d)(20\d{2})[-_](\d{2})[-_](\d{2})(?!\d)")


def _recent_dated_csfc_selection_links(snapshot: dict) -> set[str]:
    """Return CSfC Selection headings whose URL is recently date-stamped.

    NSA includes publication dates in newly issued Selection filenames. This
    gives recovery runs a narrow way to retain real changes without treating
    every link from the first healthy scrape as newly added.
    """
    collected_at = snapshot.get("collected_at", "")
    try:
        collected = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return set()

    cutoff = collected.date() - timedelta(days=CSFC_RECOVERY_LOOKBACK_DAYS)
    recent: set[str] = set()
    links = snapshot.get("csfc", {}).get("selection_links", {})
    for heading, href in links.items():
        match = _CSFC_DOCUMENT_DATE.search(unquote(href or ""))
        if not match:
            continue
        try:
            document_date = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            ).date()
        except ValueError:
            continue
        if cutoff <= document_date <= collected.date():
            recent.add(heading)
    return recent

def _rotate_old_files() -> None:
    """Delete snapshot and diff files older than KEEP_SNAPSHOTS days."""
    for pattern in (
        os.path.join(config.SNAPSHOT_DIR, "*.json"),
        os.path.join(config.DIFF_DIR, "*_diff.json"),
    ):
        files = sorted(glob.glob(pattern))
        to_delete = files[:-KEEP_SNAPSHOTS] if len(files) > KEEP_SNAPSHOTS else []
        for f in to_delete:
            try:
                os.remove(f)
                log.info("[Rotate] Deleted old file: %s", f)
            except OSError as exc:
                log.warning("[Rotate] Could not delete %s: %s", f, exc)

# ── Empty baseline snapshot (fix #2) ──────────────────────────────────────────────────────────────────
def _empty_snapshot() -> dict:
    """Return a structurally complete empty snapshot for first-run diffing."""
    return {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "collected_at": "",
        "niap": {"pcl": [], "pps": [], "tds": [], "events": [], "news": [], "policies": []},  # fix #26: removed dead "cctls" key
        "cc_portal": {"news": [], "pps": [], "products": [], "communities": [], "publications": [], "pp_rss": []},
        "cctl_labs": {},
        "csfc":      {"pages": {}, "capability_package_headers": {}, "feeds": {}},
        "cc_crypto": {"pages": {}},
        "nato":      {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "eucc":      {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "nd_itc":    {"nit_rfis": [], "nit_rfis_archived": [],
                      "awl_entries": [], "awl_meta": []},
        "source_health": {},
    }

# ── Shared alert-firing logic ──────────────────────────────────────────────────────────────────────────
def _fire_alerts(diff: dict, emailer) -> None:
    """Send all notifications derived from a completed diff.

    Shared between run_daily() and run_merge() to avoid duplication.
    """
    if config.DRY_RUN:
        alerts = diff.get("alerts", [])
        log.info("[DRY RUN] Notifications suppressed. Would have fired: %d alert(s), %d new TD(s), %d new Cisco cert(s).",
                 len(alerts),
                 len(diff.get("niap", {}).get("tds", {}).get("added", [])),
                 len(diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", [])))
        return
    source_health = diff.get("source_health", {})
    def reached_threshold(health: dict) -> bool:
        failures = health.get("consecutive_failures", 0)
        return (
            health.get("status") in ("stale", "failed")
            and (
                failures == 3
                or (failures >= 7 and failures % 7 == 0)
            )
        )

    health_escalations = {}
    for domain, health in source_health.items():
        subcollections = health.get("subcollections", {})
        failed_subcollections = {
            key: subhealth for key, subhealth in subcollections.items()
            if subhealth.get("status") in ("stale", "failed")
        }
        if failed_subcollections:
            for key, subhealth in failed_subcollections.items():
                if reached_threshold(subhealth):
                    health_escalations[f"{domain}.{key}"] = {
                        **subhealth,
                        "label": f"{health.get('label', domain)} — {subhealth.get('label', key)}",
                    }
        elif reached_threshold(health):
            health_escalations[domain] = health
    if health_escalations:
        log.warning(
            "%d source-health issue(s) reached a notification threshold.",
            len(health_escalations),
        )
        emailer.send_source_health_email(health_escalations)

    alerts = diff.get("alerts", [])
    if alerts:
        log.warning("%d keyword alert(s) — firing notifications...", len(alerts))
        emailer.send_webex_alert(alerts)
        emailer.send_webhook_alert(alerts)
        log.warning("Sending immediate alert email...")
        emailer.send_alert_email(alerts)
    else:
        log.info("No keyword alerts.")

    new_tds = diff.get("niap", {}).get("tds", {}).get("added", [])
    if new_tds:
        log.info("%d new NIAP TD(s) — sending Webex notification...", len(new_tds))
        emailer.send_new_tds_webex(new_tds)

    new_cisco_certs = diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", [])
    if new_cisco_certs:
        log.info("%d new Cisco NDcPP certification(s) — sending celebration...", len(new_cisco_certs))
        emailer.send_cisco_cert_celebration(new_cisco_certs, source="niap")
        emailer.send_cisco_cert_email(new_cisco_certs, source="niap")

    new_cisco_csfc_alerts = [
        a for a in diff.get("alerts", [])
        if "CSfC" in a.get("source", "") and
        a.get("kind") in ("new", "new_cert") and
        any(kw in a.get("title", "").lower() for kw in config.CISCO_VENDOR_KEYWORDS)
    ]
    if new_cisco_csfc_alerts:
        log.info("%d new Cisco CSfC alert(s) — sending celebration...", len(new_cisco_csfc_alerts))
        emailer.send_cisco_cert_celebration(new_cisco_csfc_alerts, source="csfc")
        emailer.send_cisco_cert_email(new_cisco_csfc_alerts, source="csfc")

    new_cisco_nato = diff.get("nato", {}).get("cisco_added", [])
    if new_cisco_nato and diff.get("nato", {}).get("baseline_reset"):
        log.warning(
            "%d Cisco NATO NIAPCL listing(s) detected during a baseline reset "
            "— celebration suppressed (re-detection, not new listings).",
            len(new_cisco_nato))
    elif new_cisco_nato:
        log.info("%d new Cisco NATO NIAPCL listing(s) — sending celebration...", len(new_cisco_nato))
        emailer.send_cisco_cert_celebration(new_cisco_nato, source="nato")
        emailer.send_cisco_cert_email(new_cisco_nato, source="nato")

    new_cisco_eucc = diff.get("eucc", {}).get("cisco_added", [])
    if new_cisco_eucc and diff.get("eucc", {}).get("baseline_reset"):
        log.warning(
            "%d Cisco EUCC certificate(s) detected during a baseline reset "
            "— celebration suppressed (re-detection, not new certifications).",
            len(new_cisco_eucc))
    elif new_cisco_eucc:
        log.info("%d new Cisco EUCC certification(s) — sending celebration...", len(new_cisco_eucc))
        emailer.send_cisco_cert_celebration(new_cisco_eucc, source="eucc")
        emailer.send_cisco_cert_email(new_cisco_eucc, source="eucc")

    # -- NIAP PP changes (new PPs, sunset changes) --------------------------------
    new_pps = diff.get("niap", {}).get("pps", {}).get("added", [])
    pp_sunsets = diff.get("niap", {}).get("pps", {}).get("sunset_changes", [])
    if new_pps or pp_sunsets:
        log.info(
            "%d new PP(s), %d PP sunset change(s) — sending Webex notification...",
            len(new_pps), len(pp_sunsets),
        )
        emailer.send_new_pps_webex(new_pps, pp_sunsets)

    # -- NIAP announcement and policy content changes --------------------------
    # These are genuine source-content updates, not operational health warnings.
    # Collector failures remain internal-only via send_source_health_email().
    niap_content_changes = []
    niap_diff = diff.get("niap", {})
    for section, kinds in (
        ("news", ("added", "revised", "deactivated", "reactivated", "removed")),
        ("events", ("added", "revised", "deactivated", "reactivated", "removed")),
        ("policies", ("added", "revised", "archived", "reactivated", "removed")),
    ):
        for kind in kinds:
            for original in niap_diff.get(section, {}).get(kind, []):
                item = copy.deepcopy(original)
                item.setdefault("_change_kind", kind)
                item["_content_type"] = section
                niap_content_changes.append(item)
    if niap_content_changes:
        log.info(
            "%d NIAP announcement/policy content change(s) — sending Webex notification...",
            len(niap_content_changes),
        )
        emailer.send_niap_news_webex(niap_content_changes)

    # -- Daily status heartbeat (always fires unless DRY_RUN) ----------------
    emailer.send_daily_status_email(diff)

# ── Run modes ─────────────────────────────────────────────────────────────────────────────────────
def run_daily(output_dir: str = None) -> None:
    """Collect, diff, dashboard, alert (Webex + immediate email on alerts)."""
    _setup_logging()
    collector, differ, dashboard, emailer = _imports()

    log.info("=" * 55)
    log.info("CC Pulse daily run — %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 55)

    today_path = snapshot_path()
    if os.path.exists(today_path):
        log.warning(
            "Today's snapshot already exists at %s — skipping collection "
            "to avoid duplicate diff. Delete it manually to force a re-run.",
            today_path,
        )
        sys.exit(0)

    try:
        new_snap = collector.collect_all(validate=False)
    except collector.SanityError as exc:
        log.error("Snapshot rejected by sanity check: %s", exc)
        log.error("Aborting — no files written.")
        sys.exit(1)
    prior_path = _latest_prior_snapshot()
    first_run = prior_path is None
    if first_run:
        log.warning("No prior snapshot found — diff will be empty (first run). Alerts suppressed.")
        old_snap = _empty_snapshot()
    else:
        log.info("Diffing against: %s", prior_path)
        old_snap = _load_json(prior_path)

    _apply_source_health(new_snap, None if first_run else old_snap)
    try:
        collector.validate_snapshot(new_snap)
    except collector.SanityError as exc:
        log.error("Snapshot rejected by sanity check after fallback: %s", exc)
        log.error("Aborting — no files written.")
        sys.exit(1)
    _save_json(new_snap, today_path)

    old_for_diff = old_snap if first_run else _diff_baseline_with_recoveries(old_snap, new_snap)
    diff = differ.compute_diff(old_for_diff, new_snap)
    if first_run:
        log.warning("First run: suppressing all changes from diff (baseline snapshot, not a real diff).")
        def _clear_lists(obj):
            if isinstance(obj, list):
                return []
            if isinstance(obj, dict):
                return {k: _clear_lists(v) for k, v in obj.items()}
            return obj
        for section in ("niap", "cc_portal", "cctl_labs", "csfc", "cc_crypto", "nato", "eucc"):  # fix #23
            if section in diff:
                diff[section] = _clear_lists(diff[section])
        diff["alerts"] = []
    _save_json(diff, diff_path())

    _rotate_old_files()
    dashboard.render_dashboard(diff, output_dir=output_dir or config.DASHBOARD_DIR)
    _fire_alerts(diff, emailer)
    log.info("Daily run complete.")


def run_merge(partial_dir: str = "snapshots/partial", output_dir: str = None) -> None:
    """Assemble a full snapshot from per-domain partial JSONs, then diff and alert.

    This is the merge step for the GitHub Actions matrix workflow (issue #20).
    Each matrix job runs `python collector.py --domain <name>` which writes
    `snapshots/partial/<name>.json`.  Once all matrix jobs complete, the
    downstream merge-and-notify job calls `python main.py --merge` to:

    1. Read every <partial_dir>/<domain>.json file.
    2. Assemble them into a full snapshot (same structure as collect_all()).
    3. Validate with collector.validate_snapshot().
    4. Write the full snapshot to snapshots/YYYY-MM-DD.json.
    5. Diff, render dashboard, fire alerts.

    Missing or suspiciously empty domains use last-known-good data when it is
    available, so a single slow/failed collector does not create false removal
    events. Source health metadata keeps that degraded state visible.
    """
    _setup_logging()
    collector, differ, dashboard, emailer = _imports()

    log.info("=" * 55)
    log.info("CC Pulse merge run — %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 55)

    today_path = snapshot_path()
    if os.path.exists(today_path):
        log.warning(
            "Today's snapshot already exists at %s — skipping merge "
            "to avoid duplicate diff.",
            today_path,
        )
        sys.exit(0)

    domains = list(collector.DOMAIN_COLLECTORS.keys())
    domain_data: dict = {}
    missing: list = []
    for domain in domains:
        path = os.path.join(partial_dir, f"{domain}.json")
        if os.path.exists(path):
            try:
                domain_data[domain] = _load_json(path)
                log.info("[Merge] Loaded partial: %s (%d bytes)", path, os.path.getsize(path))
            except Exception as exc:
                log.error("[Merge] Failed to load %s: %s — using empty fallback.", path, exc)
                domain_data[domain] = {}
                missing.append(domain)
        else:
            log.warning("[Merge] Partial file not found: %s — using empty fallback.", path)
            domain_data[domain] = {}
            missing.append(domain)

    if missing:
        log.warning("[Merge] %d domain(s) missing/failed: %s", len(missing), missing)

    new_snap = {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "niap":      domain_data.get("niap", {}),
        "cc_portal": domain_data.get("cc_portal", {}),
        "cctl_labs": domain_data.get("cctl_labs", {}),
        "csfc":      domain_data.get("csfc", {}),
        "cc_crypto": domain_data.get("cc_crypto", {}),
                "nato":      domain_data.get("nato", {}),
        "eucc":      domain_data.get("eucc", {}),
        "nd_itc":    domain_data.get("nd_itc", {}),
    }

    prior_path = _latest_prior_snapshot()
    first_run = prior_path is None
    if first_run:
        log.warning("[Merge] No prior snapshot — diff suppressed (first run).")
        old_snap = _empty_snapshot()
    else:
        log.info("[Merge] Diffing against: %s", prior_path)
        old_snap = _load_json(prior_path)

    _apply_source_health(
        new_snap,
        None if first_run else old_snap,
        collection_errors=set(missing),
    )

    try:
        collector.validate_snapshot(new_snap)
    except collector.SanityError as exc:
        log.error("[Merge] Snapshot failed sanity check: %s", exc)
        log.error("[Merge] Aborting — no files written.")
        sys.exit(1)

    _save_json(new_snap, today_path)

    old_for_diff = old_snap if first_run else _diff_baseline_with_recoveries(old_snap, new_snap)
    diff = differ.compute_diff(old_for_diff, new_snap)
    if first_run:
        def _clear_lists(obj):  # fix #23
            if isinstance(obj, list):
                return []
            if isinstance(obj, dict):
                return {k: _clear_lists(v) for k, v in obj.items()}
            return obj
        for section in ("niap", "cc_portal", "cctl_labs", "csfc", "cc_crypto", "nato", "eucc"):
            if section in diff:
                diff[section] = _clear_lists(diff[section])
        diff["alerts"] = []
    _save_json(diff, diff_path())

    _rotate_old_files()
    dashboard.render_dashboard(diff, output_dir=output_dir or config.DASHBOARD_DIR)
    _fire_alerts(diff, emailer)
    log.info("[Merge] Merge run complete.")


def run_weekly() -> None:
    """Merge stored daily diff files and send weekly email digest."""
    _setup_logging()
    _, differ, _, emailer = _imports()

    log.info("Building weekly digest from stored daily diffs...")
    pattern = os.path.join(config.DIFF_DIR, "*_diff.json")
    files = sorted(glob.glob(pattern))
    if not files:
        log.error("No daily diff files found in %s.", config.DIFF_DIR)
        log.error("Run the daily job at least once first.")
        sys.exit(1)

    window = files[-7:]
    log.info(
        "Merging %d daily diff(s): %s ... %s",
        len(window),
        os.path.basename(window[0]),
        os.path.basename(window[-1]),
    )
    diffs = [_load_json(f) for f in window]

    weekly = differ.merge_weekly_diffs([copy.deepcopy(d) for d in diffs])
    if config.DRY_RUN:
        log.info("[DRY RUN] Weekly notifications suppressed. %d alert(s) in weekly digest.",
                 len(weekly.get("alerts", [])))
    else:
        emailer.send_weekly_email(weekly)
        emailer.send_webex_alert(weekly.get("alerts", []))
        emailer.send_webhook_alert(weekly.get("alerts", []))
        log.info("Weekly digest sent.")

def run_bootstrap() -> None:
    """Collect the initial snapshot without producing a diff."""
    _setup_logging()
    collector, _, _, _ = _imports()

    log.info("Bootstrap mode — collecting initial snapshot (no diff).")
    try:
        snap = collector.collect_all()
    except collector.SanityError as exc:
        log.error("Bootstrap rejected by sanity check: %s", exc)
        sys.exit(1)

    path = snapshot_path()
    _save_json(snap, path)
    log.info("Bootstrap complete. Snapshot at %s", path)
    log.info("Run the daily job tomorrow to get your first diff.")

def run_redash(output_dir: str = None) -> None:
    """Re-render the dashboard HTML from the latest stored diff file."""
    _imports()
    import dashboard as dash_mod
    import glob as _glob

    diffs = sorted(_glob.glob(os.path.join(config.DIFF_DIR, "*_diff.json")))
    if not diffs:
        log.error("No diff files found in %s -- run daily mode first.", config.DIFF_DIR)
        sys.exit(1)

    dest = output_dir or config.DASHBOARD_DIR
    latest = diffs[-1]
    log.info("[Redash] Loading diff from %s", latest)
    diff = _load_json(latest)
    dash_mod.render_dashboard(diff, output_dir=dest)
    log.info("[Redash] Dashboard re-rendered to %s from %s", dest, latest)

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CC Pulse — Common Criteria monitoring engine"
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Send weekly email digest from stored daily diffs",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Collect initial snapshot only (no diff)",
    )
    parser.add_argument(
        "--readme",
        action="store_true",
        help="Post the pinned README/info message to Webex (then pin it manually)",
    )
    parser.add_argument(
        "--redash",
        action="store_true",
        help="Re-render dashboard from the latest stored diff (no collection)",
    )
    parser.add_argument(
        "--staging",
        action="store_true",
        help="Re-render dashboard from the latest stored diff into docs/staging/ (private test dashboard)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help=(
            "Assemble full snapshot from snapshots/partial/*.json (written by "
            "matrix collect jobs), then diff, render dashboard, and send alerts. "
            "Used by the merge-and-notify Actions job."
        ),
    )
    args = parser.parse_args()

    if args.readme:
        _setup_logging()
        _, _, _, emailer = _imports()
        emailer.send_readme_message()
    elif args.staging:
        run_redash(output_dir=config.STAGING_DIR)
    elif args.redash:
        run_redash()
    elif args.bootstrap:
        run_bootstrap()
    elif args.weekly:
        run_weekly()
    elif args.merge:
        run_merge()
    else:
        run_daily()

if __name__ == "__main__":
    main()
