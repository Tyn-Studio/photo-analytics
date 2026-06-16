#!/usr/bin/env python3
"""
Backfill historical analytics data into the SQLite database.
Pulls data for multiple time windows to create historical data points.

Usage:
    uv run backfill.py
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-auth",
#     "google-auth-httplib2",
#     "google-api-python-client",
#     "python-dotenv",
#     "httpx",
# ]
# ///

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path(__file__).parent / "data" / "analytics.db"
SITE_URL = "sc-domain:luisnatera.photo"
PLAUSIBLE_BASE = "https://plausible.io"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            date TEXT PRIMARY KEY,
            days INTEGER,
            ghost TEXT,
            plausible TEXT,
            gsc TEXT
        )
    """)
    conn.commit()
    return conn


def get_gsc_service():
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GSC_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GSC_CLIENT_ID"),
        client_secret=os.getenv("GSC_CLIENT_SECRET"),
    )
    if not creds.valid:
        creds.refresh(Request())
    return build("searchconsole", "v1", credentials=creds)


def fetch_gsc(service, start_date: str, end_date: str) -> dict:
    data = {}
    try:
        r = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={"startDate": start_date, "endDate": end_date}
        ).execute()
        data["totals"] = r["rows"][0] if r.get("rows") else {}

        r = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={"startDate": start_date, "endDate": end_date, "dimensions": ["query"], "rowLimit": 25}
        ).execute()
        data["queries"] = r.get("rows", [])

        r = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={"startDate": start_date, "endDate": end_date, "dimensions": ["page"], "rowLimit": 25}
        ).execute()
        data["pages"] = r.get("rows", [])
    except Exception as e:
        print(f"    GSC error: {e}", file=sys.stderr)
    return data


def plausible_query(api_key: str, site_id: str, body: dict):
    try:
        r = httpx.post(
            f"{PLAUSIBLE_BASE}/api/v2/query",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"site_id": site_id, **body},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"    Plausible error: {e}", file=sys.stderr)
        return None


def fetch_plausible(start_date: str, end_date: str) -> dict:
    api_key = os.getenv("PLAUSIBLE_API_KEY", "")
    site_id = os.getenv("PLAUSIBLE_SITE_ID", "luisnatera.photo")
    if not api_key:
        return {}

    date_range = [start_date, end_date]
    data = {}

    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors", "pageviews", "bounce_rate", "visit_duration", "visits"],
        "date_range": date_range,
    })
    if r and r.get("results"):
        data["aggregate"] = r["results"][0] if r["results"] else {}

    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors", "pageviews"],
        "date_range": date_range,
        "dimensions": ["event:page"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 20},
    })
    if r:
        data["pages"] = r.get("results", [])

    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": date_range,
        "dimensions": ["visit:source"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 15},
    })
    if r:
        data["sources"] = r.get("results", [])

    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": date_range,
        "dimensions": ["visit:country_name"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 15},
    })
    if r:
        data["countries"] = r.get("results", [])

    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": date_range,
        "dimensions": ["visit:device"],
        "order_by": [["visitors", "desc"]],
    })
    if r:
        data["devices"] = r.get("results", [])

    return data


def fetch_ghost(days: int) -> dict:
    """Try to get Ghost data via ghst CLI."""
    def run_ghst(args):
        result = subprocess.run(
            ["ghst"] + args + ["--json"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    r = "365d" if days >= 365 else "90d" if days >= 90 else "30d"
    return {
        "overview": run_ghst(["stats", "overview", "--range", r]),
        "web": run_ghst(["stats", "web", "--range", r]),
        "posts": run_ghst(["stats", "posts", "--range", r]),
        "email": run_ghst(["stats", "email", "--range", r]),
        "growth": run_ghst(["stats", "growth", "--range", r]),
    }


def main():
    conn = init_db()
    service = get_gsc_service()

    # Generate weekly windows going back ~12 months
    # GSC data is available with a 2-day delay, up to 16 months back
    today = datetime.now()
    windows = []

    # Weekly snapshots for the last 52 weeks
    for weeks_ago in range(0, 52):
        end = today - timedelta(days=2 + weeks_ago * 7)
        start = end - timedelta(days=7)
        label = end.strftime("%Y-%m-%d")
        windows.append((label, 7, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))

    windows.reverse()  # oldest first

    print(f"Backfilling {len(windows)} weekly snapshots...", file=sys.stderr)

    for label, days, start_date, end_date in windows:
        # Check if we already have this date
        existing = conn.execute("SELECT 1 FROM snapshots WHERE date = ?", (label,)).fetchone()
        if existing:
            print(f"  {label}: already exists, skipping", file=sys.stderr)
            continue

        print(f"  {label}: fetching ({start_date} to {end_date})...", file=sys.stderr)

        gsc = fetch_gsc(service, start_date, end_date)
        plausible = fetch_plausible(start_date, end_date)

        # Ghost only has 30d/90d/365d ranges, not arbitrary dates
        # We'll only fetch Ghost data for the most recent snapshot
        ghost = {}

        conn.execute(
            "INSERT OR REPLACE INTO snapshots (date, days, ghost, plausible, gsc) VALUES (?, ?, ?, ?, ?)",
            (label, days, json.dumps(ghost), json.dumps(plausible), json.dumps(gsc)),
        )
        conn.commit()

    # Fetch Ghost data for today's snapshot (it only supports fixed ranges)
    print("  Fetching Ghost data for latest snapshot...", file=sys.stderr)
    ghost = fetch_ghost(30)
    latest_date = windows[-1][0]
    conn.execute(
        "UPDATE snapshots SET ghost = ? WHERE date = ?",
        (json.dumps(ghost), latest_date),
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    conn.close()
    print(f"Done. {total} snapshots in database.", file=sys.stderr)


if __name__ == "__main__":
    main()
