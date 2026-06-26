#!/usr/bin/env python3
"""
Backfill historical member counts into existing snapshots.

Ghost's `stats growth` returns a *daily* member time-series (up to 365 days),
even though the per-snapshot web/posts/email breakdowns are only available for
fixed ranges. For a long stretch the daily collector failed to authenticate
ghst in CI, so `ghost.growth.summary.total_members` is missing on most
snapshots and the dashboard's member chart has holes.

This script recovers the daily member counts from a single 365d growth pull and
injects `growth.summary` (total/free/paid members + a trailing-30d delta) into
any snapshot that is missing it. It only fills gaps — snapshots that already
have member data are left untouched. Other ghost fields (web/posts/email) are
genuinely unrecoverable per-date and are left as-is.

Usage:
    uv run backfill-members.py            # apply
    uv run backfill-members.py --dry-run  # preview only
"""

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "analytics.db"


def fetch_member_series() -> dict[str, dict]:
    """Return {date: {total_members, free_members, paid_members}} from ghst.

    Relies on ghst's local config (keychain) or GHOST_URL/GHOST_STAFF_TOKEN
    env overrides, same as site-report.py.
    """
    import os

    auth = []
    if os.getenv("GHOST_URL"):
        auth += ["--url", os.getenv("GHOST_URL")]
    if os.getenv("GHOST_STAFF_TOKEN"):
        auth += ["--staff-token", os.getenv("GHOST_STAFF_TOKEN")]

    result = subprocess.run(
        ["ghst", "stats", "growth", "--range", "365d"] + auth + ["--json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("ghst stats growth failed:", result.stderr.strip(), file=sys.stderr)
        sys.exit(1)

    data = json.loads(result.stdout)
    series = data.get("members") or []
    return {
        row["date"]: {
            "total_members": row.get("total_members"),
            "free_members": row.get("free_members"),
            "paid_members": row.get("paid_members"),
        }
        for row in series
        if row.get("date")
    }


def has_member_data(ghost: dict) -> bool:
    gr = (ghost or {}).get("growth") or {}
    summary = gr.get("summary") or {}
    return summary.get("total_members") is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = ap.parse_args()

    series = fetch_member_series()
    if not series:
        print("No member series returned; aborting.", file=sys.stderr)
        sys.exit(1)
    print(f"Recovered daily member series: {min(series)} -> {max(series)} "
          f"({len(series)} days)", file=sys.stderr)

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT date, ghost FROM snapshots ORDER BY date").fetchall()

    filled, skipped_have, skipped_nodata = 0, 0, 0
    for snap_date, ghost_json in rows:
        ghost = json.loads(ghost_json) if ghost_json else {}
        if has_member_data(ghost):
            skipped_have += 1
            continue
        point = series.get(snap_date)
        if not point or point.get("total_members") is None:
            skipped_nodata += 1
            continue

        # trailing 30-day delta, matching the live collector's range
        prior = (date.fromisoformat(snap_date) - timedelta(days=30)).isoformat()
        prior_total = (series.get(prior) or {}).get("total_members")
        delta = (point["total_members"] - prior_total) if prior_total is not None else None

        gr = ghost.get("growth") or {}
        summary = gr.get("summary") or {}
        summary.update({
            "free_members": point["free_members"],
            "paid_members": point["paid_members"],
            "total_members": point["total_members"],
            "member_delta": delta,
            "backfilled": True,  # mark reconstructed values
        })
        gr["summary"] = summary
        ghost["growth"] = gr

        if not args.dry_run:
            conn.execute(
                "UPDATE snapshots SET ghost = ? WHERE date = ?",
                (json.dumps(ghost), snap_date),
            )
        filled += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    verb = "Would fill" if args.dry_run else "Filled"
    print(f"{verb} member data on {filled} snapshots.", file=sys.stderr)
    print(f"  Left untouched (already had data): {skipped_have}", file=sys.stderr)
    print(f"  Skipped (no recoverable point): {skipped_nodata}", file=sys.stderr)


if __name__ == "__main__":
    main()
