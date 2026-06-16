#!/usr/bin/env python3
"""
Site performance report for luisnatera.photo
Pulls data from Ghost (via ghst CLI), Google Search Console, and Plausible.

Usage:
    uv run site-report.py
    uv run site-report.py --days 90
    uv run site-report.py --save
    uv run site-report.py --output report.md
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

import argparse
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

# Load .env from script directory or project root
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

SITE_URL = "sc-domain:luisnatera.photo"
PLAUSIBLE_BASE = "https://plausible.io"


# --- Ghost ---

def run_ghst(args: list[str]) -> dict | None:
    result = subprocess.run(
        ["ghst"] + args + ["--json"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  [warn] ghst {' '.join(args)} failed", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def ghost_range(days: int) -> str:
    if days >= 365:
        return "365d"
    elif days >= 90:
        return "90d"
    return "30d"


def get_ghost_data(days: int) -> dict:
    r = ghost_range(days)
    return {
        "overview": run_ghst(["stats", "overview", "--range", r]),
        "web": run_ghst(["stats", "web", "--range", r]),
        "posts": run_ghst(["stats", "posts", "--range", r]),
        "email": run_ghst(["stats", "email", "--range", r]),
        "growth": run_ghst(["stats", "growth", "--range", r]),
    }


# --- Plausible ---

def plausible_query(api_key: str, site_id: str, body: dict) -> dict | None:
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
        print(f"  [warn] Plausible query failed: {e}", file=sys.stderr)
        return None


def get_plausible_data(days: int) -> dict:
    api_key = os.getenv("PLAUSIBLE_API_KEY", "")
    site_id = os.getenv("PLAUSIBLE_SITE_ID", "luisnatera.photo")

    if not api_key:
        print("  [warn] PLAUSIBLE_API_KEY not set, skipping Plausible", file=sys.stderr)
        return {}

    period = f"{days}d"
    data = {}

    # Aggregate stats
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors", "pageviews", "bounce_rate", "visit_duration", "visits"],
        "date_range": period,
    })
    if r and r.get("results"):
        data["aggregate"] = r["results"][0] if r["results"] else {}

    # Top pages
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors", "pageviews"],
        "date_range": period,
        "dimensions": ["event:page"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 20},
    })
    if r:
        data["pages"] = r.get("results", [])

    # Top sources
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": period,
        "dimensions": ["visit:source"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 15},
    })
    if r:
        data["sources"] = r.get("results", [])

    # Top countries
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": period,
        "dimensions": ["visit:country_name"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 15},
    })
    if r:
        data["countries"] = r.get("results", [])

    # Top cities
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": period,
        "dimensions": ["visit:city_name"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 15},
    })
    if r:
        data["cities"] = r.get("results", [])

    # Devices
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": period,
        "dimensions": ["visit:device"],
        "order_by": [["visitors", "desc"]],
    })
    if r:
        data["devices"] = r.get("results", [])

    # UTM sources
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": period,
        "dimensions": ["visit:utm_source"],
        "order_by": [["visitors", "desc"]],
        "pagination": {"limit": 15},
    })
    if r:
        data["utm_sources"] = r.get("results", [])

    # Entry pages
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors", "visits"],
        "date_range": period,
        "dimensions": ["event:page"],
        "filters": [["is", "event:name", ["pageview"]]],
        "order_by": [["visits", "desc"]],
        "pagination": {"limit": 15},
    })
    if r:
        data["entry_pages"] = r.get("results", [])

    # Custom events (signups)
    r = plausible_query(api_key, site_id, {
        "metrics": ["visitors"],
        "date_range": period,
        "dimensions": ["event:name"],
        "filters": [["is", "event:name", ["Signup", "Signup-Confirmed"]]],
    })
    if r:
        data["signups"] = r.get("results", [])

    return data


# --- Google Search Console ---

def get_gsc_data(days: int) -> dict:
    client_id = os.getenv("GSC_CLIENT_ID", "")
    client_secret = os.getenv("GSC_CLIENT_SECRET", "")
    refresh_token = os.getenv("GSC_REFRESH_TOKEN", "")
    access_token = os.getenv("GSC_ACCESS_TOKEN", "")

    if not refresh_token:
        print("  [warn] GSC_REFRESH_TOKEN not set, skipping GSC", file=sys.stderr)
        return {}

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )

    # Auto-refresh the access token if expired
    if not creds.valid:
        try:
            creds.refresh(Request())
            print("  [info] GSC access token refreshed", file=sys.stderr)
        except Exception as e:
            print(f"  [warn] GSC token refresh failed: {e}", file=sys.stderr)
            return {}

    try:
        service = build("searchconsole", "v1", credentials=creds)
    except Exception as e:
        print(f"  [warn] GSC auth failed: {e}", file=sys.stderr)
        return {}

    end_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 2)).strftime("%Y-%m-%d")
    data = {}

    try:
        # Site totals
        r = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={"startDate": start_date, "endDate": end_date}
        ).execute()
        data["totals"] = r["rows"][0] if r.get("rows") else {}

        # Top queries
        r = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={
                "startDate": start_date, "endDate": end_date,
                "dimensions": ["query"], "rowLimit": 25,
            }
        ).execute()
        data["queries"] = r.get("rows", [])

        # Top pages
        r = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={
                "startDate": start_date, "endDate": end_date,
                "dimensions": ["page"], "rowLimit": 25,
            }
        ).execute()
        data["pages"] = r.get("rows", [])

        data["opportunities"] = [
            row for row in data["queries"]
            if row["impressions"] >= 5 and row["clicks"] == 0
        ]
        data["converting"] = [
            row for row in data["queries"]
            if row["clicks"] > 0
        ]
    except Exception as e:
        print(f"  [warn] GSC query failed: {e}", file=sys.stderr)
        print("  You may need to refresh GSC_ACCESS_TOKEN in .env", file=sys.stderr)

    return data


# --- Report ---

def fmt(val, suffix="") -> str:
    if val is None:
        return "n/a"
    if isinstance(val, float):
        return f"{val:.1f}{suffix}"
    if isinstance(val, int) and val >= 1000:
        return f"{val:,}{suffix}"
    return f"{val}{suffix}"


def dim(row: dict, key: str) -> str:
    """Extract a dimension value from a Plausible result row."""
    dims = row.get("dimensions", [])
    return dims[0] if dims else "?"


def met(row: dict, index: int = 0) -> int | float:
    """Extract a metric value from a Plausible result row."""
    metrics = row.get("metrics", [])
    return metrics[index] if index < len(metrics) else 0


def format_report(ghost: dict, gsc: dict, plausible: dict, days: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Site Performance Report — {now}", f"Period: last {days} days", ""]

    # --- Plausible Overview ---
    if plausible.get("aggregate"):
        a = plausible["aggregate"]
        metrics = a.get("metrics", [])
        # metrics order: visitors, pageviews, bounce_rate, visit_duration, visits
        lines += [
            "## Traffic Overview (Plausible)",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Visitors | {fmt(metrics[0] if len(metrics) > 0 else None)} |",
            f"| Pageviews | {fmt(metrics[1] if len(metrics) > 1 else None)} |",
            f"| Bounce rate | {fmt(metrics[2] if len(metrics) > 2 else None, '%')} |",
            f"| Avg visit duration | {fmt(metrics[3] if len(metrics) > 3 else None, 's')} |",
            f"| Total visits | {fmt(metrics[4] if len(metrics) > 4 else None)} |",
            "",
        ]

    # --- Ghost Overview (fallback if no Plausible) ---
    if not plausible.get("aggregate"):
        overview = ghost.get("overview")
        if overview and "summary" in overview:
            s = overview["summary"]
            lines += [
                "## Traffic Overview (Ghost)",
                "",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Visitors | {fmt(s.get('visitors'))} |",
                f"| Pageviews | {fmt(s.get('pageviews'))} |",
                f"| Bounce rate | {fmt(s.get('bounce_rate'), '%')} |",
                f"| Avg visit duration | {fmt(s.get('avg_session_sec'), 's')} |",
                "",
            ]

    # --- Members ---
    growth = ghost.get("growth")
    if growth and "summary" in growth:
        s = growth["summary"]
        lines += [
            "## Members",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total members | {fmt(s.get('total_members'))} |",
            f"| New (period) | +{fmt(s.get('member_delta'))} |",
            f"| Paid members | {fmt(s.get('paid_members'))} |",
            "",
        ]

    # --- Signups (Plausible custom events) ---
    if plausible.get("signups"):
        lines += ["## Signup Events (Plausible)", ""]
        lines += ["| Event | Count |", "|-------|-------|"]
        for row in plausible["signups"]:
            lines.append(f"| {dim(row, 'event:name')} | {met(row)} |")
        lines.append("")

    # --- Email ---
    email = ghost.get("email")
    if email and email.get("newsletters"):
        nl = email["newsletters"][0]
        lines += [
            "## Email Performance",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Emails sent | {fmt(nl.get('sent_posts'))} |",
            f"| Total delivered | {fmt(nl.get('recipients'))} |",
            f"| Open rate | {fmt(nl.get('open_rate'), '%')} |",
            f"| Click rate | {fmt(nl.get('click_rate'), '%')} |",
            f"| Active subscribers | {fmt(nl.get('subscribers'))} |",
            "",
        ]

    # --- Top Posts ---
    posts = ghost.get("posts")
    if posts and posts.get("posts"):
        lines += [
            "## Top Posts (Ghost — by web visits)",
            "",
            "| Post | Visits | Sent | Open rate | Click rate | New members |",
            "|------|--------|------|-----------|------------|-------------|",
        ]
        for p in posts["posts"][:10]:
            title = p.get("title", "?")
            if len(title) > 55:
                title = title[:52] + "..."
            lines.append(
                f"| {title} | {p.get('views', 0)} | {p.get('sent_count', 0)} "
                f"| {fmt(p.get('open_rate'), '%')} | {fmt(p.get('click_rate'), '%')} "
                f"| {p.get('members', 0)} |"
            )
        lines.append("")

    # --- Top Pages (Plausible) ---
    if plausible.get("pages"):
        lines += [
            "## Top Pages (Plausible)",
            "",
            "| Page | Visitors | Pageviews |",
            "|------|----------|-----------|",
        ]
        for row in plausible["pages"][:15]:
            page = dim(row, "event:page")
            lines.append(f"| {page} | {met(row, 0)} | {met(row, 1)} |")
        lines.append("")

    # --- Sources (Plausible preferred, Ghost fallback) ---
    if plausible.get("sources"):
        lines += [
            "## Traffic Sources (Plausible)",
            "",
            "| Source | Visitors |",
            "|--------|----------|",
        ]
        for row in plausible["sources"][:10]:
            lines.append(f"| {dim(row, 'visit:source')} | {met(row)} |")
        lines.append("")
    elif ghost.get("web") and ghost["web"].get("sources"):
        lines += [
            "## Traffic Sources (Ghost)",
            "",
            "| Source | Visits |",
            "|--------|--------|",
        ]
        for s in ghost["web"]["sources"][:10]:
            lines.append(f"| {s.get('label', '?')} | {fmt(s.get('visits'))} |")
        lines.append("")

    # --- UTM Sources (Plausible) ---
    if plausible.get("utm_sources"):
        utm = [r for r in plausible["utm_sources"] if dim(r, "visit:utm_source") != "(not set)"]
        if utm:
            lines += [
                "## UTM Sources (Plausible)",
                "",
                "| UTM Source | Visitors |",
                "|------------|----------|",
            ]
            for row in utm[:10]:
                lines.append(f"| {dim(row, 'visit:utm_source')} | {met(row)} |")
            lines.append("")

    # --- Locations ---
    if plausible.get("countries"):
        lines += [
            "## Top Countries (Plausible)",
            "",
            "| Country | Visitors |",
            "|---------|----------|",
        ]
        for row in plausible["countries"][:10]:
            lines.append(f"| {dim(row, 'visit:country_name')} | {met(row)} |")
        lines.append("")

    if plausible.get("cities"):
        cities = [r for r in plausible["cities"] if dim(r, "visit:city_name") != "(not set)"]
        if cities:
            lines += [
                "## Top Cities (Plausible)",
                "",
                "| City | Visitors |",
                "|------|----------|",
            ]
            for row in cities[:10]:
                lines.append(f"| {dim(row, 'visit:city_name')} | {met(row)} |")
            lines.append("")

    # --- Devices (Plausible) ---
    if plausible.get("devices"):
        lines += [
            "## Devices (Plausible)",
            "",
            "| Device | Visitors |",
            "|--------|----------|",
        ]
        for row in plausible["devices"]:
            lines.append(f"| {dim(row, 'visit:device')} | {met(row)} |")
        lines.append("")

    # --- Google Search Console ---
    if gsc:
        lines += ["## Google Search Console", ""]

        totals = gsc.get("totals", {})
        if totals:
            lines += [
                "### Site Totals",
                "",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Total clicks | {totals.get('clicks', 0)} |",
                f"| Total impressions | {fmt(totals.get('impressions', 0))} |",
                f"| Average CTR | {totals.get('ctr', 0):.1%} |",
                f"| Average position | {totals.get('position', 0):.1f} |",
                "",
            ]

        converting = gsc.get("converting", [])
        if converting:
            lines += [
                "### Queries Getting Clicks",
                "",
                "| Query | Clicks | Impressions | CTR | Position |",
                "|-------|--------|-------------|-----|----------|",
            ]
            for row in sorted(converting, key=lambda x: x["clicks"], reverse=True):
                q = row["keys"][0]
                lines.append(
                    f"| {q} | {row['clicks']} | {row['impressions']} "
                    f"| {row['ctr']:.1%} | {row['position']:.1f} |"
                )
            lines.append("")

        queries = gsc.get("queries", [])
        if queries:
            lines += [
                "### Top Queries (by impressions)",
                "",
                "| Query | Clicks | Impressions | CTR | Position |",
                "|-------|--------|-------------|-----|----------|",
            ]
            for row in sorted(queries, key=lambda x: x["impressions"], reverse=True)[:15]:
                q = row["keys"][0]
                lines.append(
                    f"| {q} | {row['clicks']} | {row['impressions']} "
                    f"| {row['ctr']:.1%} | {row['position']:.1f} |"
                )
            lines.append("")

        pages = gsc.get("pages", [])
        if pages:
            lines += [
                "### Top Pages in Search",
                "",
                "| Page | Clicks | Impressions | CTR | Position |",
                "|------|--------|-------------|-----|----------|",
            ]
            for row in sorted(pages, key=lambda x: x["impressions"], reverse=True)[:15]:
                p = row["keys"][0].replace("https://www.luisnatera.photo", "")
                lines.append(
                    f"| {p} | {row['clicks']} | {row['impressions']} "
                    f"| {row['ctr']:.1%} | {row['position']:.1f} |"
                )
            lines.append("")

        # --- Opportunities ---
        opps = gsc.get("opportunities", [])
        if opps:
            lines += [
                "## SEO Opportunities",
                "",
                "Queries with impressions but zero clicks:",
                "",
                "| Query | Impressions | Position | Action |",
                "|-------|-------------|----------|--------|",
            ]
            for row in sorted(opps, key=lambda x: x["impressions"], reverse=True):
                q = row["keys"][0]
                pos = row["position"]
                if pos <= 10:
                    action = "Page 1 — improve meta title/description to earn clicks"
                elif pos <= 20:
                    action = "Page 2 — strengthen content to push to page 1"
                else:
                    action = "Low ranking — consider dedicated content"
                lines.append(f"| {q} | {row['impressions']} | {pos:.1f} | {action} |")
            lines.append("")

    # --- Key Takeaways ---
    lines += ["## Key Takeaways", ""]

    if gsc:
        totals = gsc.get("totals", {})
        total_impr = totals.get("impressions", 0)
        total_clicks = totals.get("clicks", 0)
        opps = gsc.get("opportunities", [])
        opp_impr = sum(r["impressions"] for r in opps)

        if opp_impr > 0:
            lines.append(
                f"- **{opp_impr:,} search impressions with zero clicks.** "
                f"You're showing up but not getting clicked. "
                f"Better meta titles and descriptions could convert these to visits."
            )

        if total_clicks < 20:
            lines.append(
                f"- **Google sends very little traffic** ({total_clicks} clicks). "
                f"But {total_impr:,} impressions means you're indexed and ranking. "
                f"The gap is CTR, not visibility."
            )

    # Source concentration (use Plausible if available, else Ghost)
    sources = plausible.get("sources") or []
    if sources:
        total_v = sum(met(r) for r in sources)
        if total_v > 0:
            top = sources[0]
            top_pct = met(top) / total_v * 100
            if top_pct > 70:
                lines.append(
                    f"- **{dim(top, 'visit:source')} accounts for {top_pct:.0f}% of traffic.** "
                    f"Diversifying sources reduces dependence."
                )
    elif ghost.get("web") and ghost["web"].get("sources"):
        gh_sources = ghost["web"]["sources"]
        total_v = sum(s.get("visits", 0) for s in gh_sources)
        if total_v > 0:
            top = gh_sources[0]
            top_pct = top.get("visits", 0) / total_v * 100
            if top_pct > 70:
                lines.append(
                    f"- **{top.get('label', '?')} accounts for {top_pct:.0f}% of traffic.** "
                    f"Diversifying sources reduces dependence."
                )

    email = ghost.get("email")
    if email and email.get("newsletters"):
        nl = email["newsletters"][0]
        open_rate = nl.get("open_rate", 0)
        if open_rate > 50:
            lines.append(
                f"- **Email open rate is strong at {open_rate}%.** "
                f"Subscribers are engaged. Focus on growing the list."
            )
        subs = nl.get("subscribers", 0)
        if subs > 0:
            lines.append(f"- **{subs} active email subscribers.**")

    # Viral outlier detection
    posts = ghost.get("posts")
    if posts and posts.get("posts") and len(posts["posts"]) > 1:
        top_post = posts["posts"][0]
        second = posts["posts"][1]
        if top_post.get("views", 0) > second.get("views", 0) * 10:
            lines.append(
                f"- **Viral outlier: \"{top_post['title']}\"** with {top_post['views']:,} views "
                f"vs {second['views']} for the next post. Baseline traffic is much lower without it."
            )

    # Signup events
    if plausible.get("signups"):
        for row in plausible["signups"]:
            name = dim(row, "event:name")
            count = met(row)
            if count > 0:
                lines.append(f"- **{count} '{name}' events** tracked in Plausible.")

    lines += ["", "---", f"*Generated on {now} by site-report.py*"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Site performance report")
    parser.add_argument("--days", type=int, default=30, help="Report period in days (default: 30)")
    parser.add_argument("--output", type=str, help="Write report to markdown file")
    parser.add_argument("--save", action="store_true", help="Save data to SQLite database")
    parser.add_argument("--db", type=str, default="data/analytics.db", help="SQLite database path (default: data/analytics.db)")
    args = parser.parse_args()

    print(f"Pulling data for the last {args.days} days...", file=sys.stderr)

    print("  Fetching Ghost analytics...", file=sys.stderr)
    ghost = get_ghost_data(args.days)

    print("  Fetching Plausible analytics...", file=sys.stderr)
    plausible = get_plausible_data(args.days)

    print("  Fetching Google Search Console...", file=sys.stderr)
    gsc = get_gsc_data(args.days)

    if args.save:
        db_path = Path(__file__).parent / args.db
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                date TEXT PRIMARY KEY,
                days INTEGER,
                ghost TEXT,
                plausible TEXT,
                gsc TEXT
            )
        """)
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            "INSERT OR REPLACE INTO snapshots (date, days, ghost, plausible, gsc) VALUES (?, ?, ?, ?, ?)",
            (today, args.days, json.dumps(ghost), json.dumps(plausible), json.dumps(gsc)),
        )
        conn.commit()
        conn.close()
        print(f"  Data saved to {db_path} ({today})", file=sys.stderr)

    report = format_report(ghost, gsc, plausible, args.days)

    if args.output:
        Path(args.output).write_text(report)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
