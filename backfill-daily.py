#!/usr/bin/env python3
"""
Rebuild every snapshot's Plausible + GSC data as TRUE single-day values.

Why: snapshots historically stored rolling-window totals (1d / 7d / 30d,
depending on the --days used at collection time), but report.py and the
dashboard SUM per-snapshot values across a period. Summing overlapping
windows of mixed sizes massively overcounts visitors, pageviews and search
metrics (observed ~8x inflation on 30-day visitors).

The fix is the standard time-series model: each snapshot holds exactly one
day's data, so summing over a period is correct. This script pulls the full
history once per breakdown using Plausible's `time:day` dimension and GSC's
`date` dimension (a handful of API calls, not per-day loops), then overwrites
each snapshot's `plausible` and `gsc` to contain only that date's values, in
the same shape site-report.py produces. It also sets days=1.

Going forward, the daily collector runs with --days 1 so new snapshots match.

Usage:
    uv run backfill-daily.py            # apply
    uv run backfill-daily.py --dry-run  # preview a few dates, write nothing
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx",
#     "python-dotenv",
#     "google-auth",
#     "google-api-python-client",
# ]
# ///

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import os

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path(__file__).parent / "data" / "analytics.db"
PLAUSIBLE_BASE = "https://plausible.io"
GSC_SITE = "sc-domain:luisnatera.photo"
SIGNUP_GOALS = ["Signup", "Signup-Confirmed"]


# ---------- Plausible: pull whole history bucketed by day ----------

def plausible_daily(start, end):
    key = os.getenv("PLAUSIBLE_API_KEY", "")
    site = os.getenv("PLAUSIBLE_SITE_ID", "luisnatera.photo")
    if not key:
        print("PLAUSIBLE_API_KEY missing", file=sys.stderr)
        sys.exit(1)

    def q(body):
        r = httpx.post(
            f"{PLAUSIBLE_BASE}/api/v2/query",
            headers={"Authorization": f"Bearer {key}"},
            json={"site_id": site, **body}, timeout=60,
        )
        r.raise_for_status()
        return r.json().get("results", [])

    # Explicit custom range covering all snapshot dates. (The 12mo/6mo presets
    # are calendar-bucketed and exclude the current partial month — they would
    # silently drop recent weeks.) Plausible omits zero-traffic days, so the
    # caller zero-fills any snapshot date with no row.
    RANGE = [start, end]

    # aggregate metrics per day -> {date: [visitors, pageviews, bounce, dur, visits]}
    agg = {}
    for row in q({"metrics": ["visitors", "pageviews", "bounce_rate", "visit_duration", "visits"],
                  "date_range": RANGE, "dimensions": ["time:day"]}):
        agg[row["dimensions"][0]] = row["metrics"]

    # pages per day -> {date: [ {dimensions:[page], metrics:[v,pv,bounce,dur]} ]}
    pages = defaultdict(list)
    for row in q({"metrics": ["visitors", "pageviews", "bounce_rate", "visit_duration"],
                  "date_range": RANGE, "dimensions": ["time:day", "event:page"],
                  "order_by": [["visitors", "desc"]], "pagination": {"limit": 10000}}):
        d, page = row["dimensions"]
        pages[d].append({"dimensions": [page], "metrics": row["metrics"]})

    # sources per day -> {date: [ {dimensions:[source], metrics:[visitors]} ]}
    sources = defaultdict(list)
    for row in q({"metrics": ["visitors"], "date_range": RANGE,
                  "dimensions": ["time:day", "visit:source"],
                  "order_by": [["visitors", "desc"]], "pagination": {"limit": 10000}}):
        d, src = row["dimensions"]
        sources[d].append({"dimensions": [src], "metrics": row["metrics"]})

    # signups by source per day
    su_src = defaultdict(list)
    for row in q({"metrics": ["visitors"], "date_range": RANGE,
                  "dimensions": ["time:day", "visit:source"],
                  "filters": [["is", "event:name", SIGNUP_GOALS]],
                  "order_by": [["visitors", "desc"]], "pagination": {"limit": 10000}}):
        d, src = row["dimensions"]
        su_src[d].append({"dimensions": [src], "metrics": row["metrics"]})

    # signups by page per day
    su_page = defaultdict(list)
    for row in q({"metrics": ["visitors"], "date_range": RANGE,
                  "dimensions": ["time:day", "event:page"],
                  "filters": [["is", "event:name", SIGNUP_GOALS]],
                  "order_by": [["visitors", "desc"]], "pagination": {"limit": 10000}}):
        d, page = row["dimensions"]
        su_page[d].append({"dimensions": [page], "metrics": row["metrics"]})

    # total signups per day (single number, mirrors data["signups"])
    signups = defaultdict(list)
    for row in q({"metrics": ["visitors"], "date_range": RANGE,
                  "dimensions": ["time:day", "event:name"],
                  "filters": [["is", "event:name", SIGNUP_GOALS]]}):
        d, name = row["dimensions"]
        signups[d].append({"dimensions": [name], "metrics": row["metrics"]})

    dates = set(agg)
    out = {}
    for d in dates:
        out[d] = {
            "aggregate": {"metrics": agg[d]},
            "pages": pages.get(d, []),
            "sources": sources.get(d, []),
            "signups": signups.get(d, []),
            "signups_by_source": su_src.get(d, []),
            "signups_by_page": su_page.get(d, []),
        }
    return out


# ---------- GSC: pull whole history bucketed by day ----------

def gsc_daily(min_date, max_date):
    creds = Credentials(
        token=None, refresh_token=os.getenv("GSC_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GSC_CLIENT_ID"), client_secret=os.getenv("GSC_CLIENT_SECRET"),
    )
    creds.refresh(Request())
    svc = build("searchconsole", "v1", credentials=creds)

    def query(dimensions, limit=25000):
        return svc.searchanalytics().query(siteUrl=GSC_SITE, body={
            "startDate": min_date, "endDate": max_date,
            "dimensions": dimensions, "rowLimit": limit,
        }).execute().get("rows", [])

    # totals per day
    totals = {}
    for row in query(["date"]):
        d = row["keys"][0]
        totals[d] = {"clicks": row.get("clicks", 0), "impressions": row.get("impressions", 0),
                     "ctr": row.get("ctr", 0), "position": row.get("position", 0)}

    # queries per day
    queries = defaultdict(list)
    for row in query(["date", "query"]):
        d, term = row["keys"]
        queries[d].append({"keys": [term], "clicks": row.get("clicks", 0),
                           "impressions": row.get("impressions", 0),
                           "ctr": row.get("ctr", 0), "position": row.get("position", 0)})

    # pages per day
    gpages = defaultdict(list)
    for row in query(["date", "page"]):
        d, page = row["keys"]
        gpages[d].append({"keys": [page], "clicks": row.get("clicks", 0),
                          "impressions": row.get("impressions", 0),
                          "ctr": row.get("ctr", 0), "position": row.get("position", 0)})

    out = {}
    for d in totals:
        # keep top 25 queries/pages per day to mirror site-report's rowLimit
        qs = sorted(queries.get(d, []), key=lambda x: x["impressions"], reverse=True)[:25]
        ps = sorted(gpages.get(d, []), key=lambda x: x["impressions"], reverse=True)[:25]
        out[d] = {"totals": totals[d], "queries": qs, "pages": ps}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    snap_dates = [r[0] for r in conn.execute("SELECT date FROM snapshots ORDER BY date").fetchall()]
    if not snap_dates:
        print("no snapshots", file=sys.stderr); sys.exit(1)
    min_d, max_d = snap_dates[0], snap_dates[-1]
    print(f"Snapshots: {len(snap_dates)} ({min_d} -> {max_d})", file=sys.stderr)

    print("Pulling Plausible daily history...", file=sys.stderr)
    pl = plausible_daily(min_d, max_d)
    print(f"  Plausible daily coverage: {len(pl)} days "
          f"({min(pl) if pl else '-'} -> {max(pl) if pl else '-'})", file=sys.stderr)

    print("Pulling GSC daily history...", file=sys.stderr)
    gs = gsc_daily(min_d, max_d)
    print(f"  GSC daily coverage: {len(gs)} days "
          f"({min(gs) if gs else '-'} -> {max(gs) if gs else '-'})", file=sys.stderr)

    # Source tracking floors: a snapshot date at/after a source's first data day
    # but with no row is a genuine zero-traffic day -> store zeros, not the old
    # rolling value. Dates before a source existed are left untouched.
    pl_start, pl_end = (min(pl), max(pl)) if pl else (None, None)
    gs_start, gs_end = (min(gs), max(gs)) if gs else (None, None)
    PL_ZERO = {"aggregate": {"metrics": [0, 0, 0, 0, 0]}, "pages": [], "sources": [],
               "signups": [], "signups_by_source": [], "signups_by_page": []}
    GS_ZERO = {"totals": {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0},
               "queries": [], "pages": []}

    updated, pl_zero, pl_skip, gs_zero, gs_skip = 0, 0, 0, 0, 0
    for d in snap_dates:
        row = conn.execute("SELECT plausible, gsc FROM snapshots WHERE date=?", (d,)).fetchone()
        cur_pl = json.loads(row[0]) if row[0] else {}
        cur_gs = json.loads(row[1]) if row[1] else {}

        # No upper bound: a snapshot date at/after a source's first data day
        # with no row is either a true zero-traffic day or a recent not-yet-
        # finalized day (GSC lags ~2d) — both should be ~0, never the old
        # rolling-window total. Only dates before tracking began are untouched.
        if d in pl:
            new_pl = pl[d]
        elif pl_start and d >= pl_start:
            new_pl = dict(PL_ZERO); pl_zero += 1
        else:
            new_pl = cur_pl; pl_skip += 1

        if d in gs:
            new_gs = gs[d]
        elif gs_start and d >= gs_start:
            new_gs = dict(GS_ZERO); gs_zero += 1
        else:
            new_gs = cur_gs; gs_skip += 1

        if args.dry_run:
            if d in (snap_dates[-4:] + [snap_dates[len(snap_dates)//2]]):
                v = (new_pl.get("aggregate", {}).get("metrics") or [None])[0]
                imp = new_gs.get("totals", {}).get("impressions")
                print(f"  {d}: visitors={v} gsc_impr={imp}", file=sys.stderr)
            continue

        conn.execute("UPDATE snapshots SET plausible=?, gsc=?, days=1 WHERE date=?",
                     (json.dumps(new_pl), json.dumps(new_gs), d))
        updated += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    verb = "Would update" if args.dry_run else "Updated"
    print(f"{verb} {updated} snapshots to true-daily.", file=sys.stderr)
    print(f"  Plausible: zero-filled {pl_zero}, left untouched (pre-tracking) {pl_skip}", file=sys.stderr)
    print(f"  GSC:       zero-filled {gs_zero}, left untouched (pre-tracking) {gs_skip}", file=sys.stderr)


if __name__ == "__main__":
    main()
