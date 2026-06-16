#!/usr/bin/env python3
"""
LLM-friendly analytics reporting tool.
Queries the SQLite database and outputs structured markdown (default) or JSON.

Usage:
    uv run report.py freshness                     # Check if data is current
    uv run report.py summary --days 30             # KPI overview
    uv run report.py trends                        # Metric trends across periods
    uv run report.py seo --days 30                 # SEO opportunities
    uv run report.py content                       # Content performance by theme
    uv run report.py conversions --days 30         # Conversion data
    uv run report.py brief --days 30               # Full brief (all reports)
    uv run report.py query "SELECT ..." --json     # Raw SQL, JSON output
"""

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


DB_PATH = Path(__file__).parent / "data" / "analytics.db"


def get_conn():
    if not DB_PATH.exists():
        print("No database found. Run site-report.py --save first.", file=sys.stderr)
        raise SystemExit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_snapshots(conn, days=None):
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute("SELECT * FROM snapshots WHERE date >= ? ORDER BY date ASC", (cutoff,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM snapshots ORDER BY date ASC").fetchall()
    return [dict(r) for r in rows]


def parse_snapshot(row):
    return {
        "date": row["date"],
        "days": row["days"],
        "ghost": json.loads(row["ghost"]) if row["ghost"] else {},
        "plausible": json.loads(row["plausible"]) if row["plausible"] else {},
        "gsc": json.loads(row["gsc"]) if row["gsc"] else {},
        "suggest": json.loads(row["suggest"]) if row.get("suggest") else {},
    }


def agg_metric(snapshots, extract_fn, mode="sum"):
    vals = [extract_fn(s) for s in snapshots]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    if mode == "sum":
        return sum(vals)
    if mode == "avg":
        return round(sum(vals) / len(vals), 1)
    if mode == "last":
        return vals[-1]
    return vals[-1]


def cmd_summary(args):
    conn = get_conn()
    days = args.days or 7
    rows = get_snapshots(conn, days)
    prev_rows = get_snapshots(conn, days * 2)
    prev_rows = [r for r in prev_rows if r not in rows]

    snaps = [parse_snapshot(r) for r in rows]
    prev_snaps = [parse_snapshot(r) for r in prev_rows]

    def m(snaps, path, mode="sum"):
        def extract(s):
            parts = path.split(".")
            val = s
            for p in parts:
                if val is None:
                    return None
                if isinstance(val, list):
                    try: val = val[int(p)]
                    except: return None
                elif isinstance(val, dict):
                    val = val.get(p)
                else:
                    return None
            return val
        return agg_metric(snaps, extract, mode)

    visitors = m(snaps, "plausible.aggregate.metrics.0")
    visitors_prev = m(prev_snaps, "plausible.aggregate.metrics.0")
    pageviews = m(snaps, "plausible.aggregate.metrics.1")
    bounce = m(snaps, "plausible.aggregate.metrics.2", "avg")
    impressions = m(snaps, "gsc.totals.impressions")
    clicks = m(snaps, "gsc.totals.clicks")
    position = m(snaps, "gsc.totals.position", "avg")
    position_prev = m(prev_snaps, "gsc.totals.position", "avg")

    # Ghost (latest snapshot only)
    members = None
    subscribers = None
    open_rate = None
    for s in reversed(snaps):
        g = s.get("ghost", {})
        gr = g.get("growth", {})
        em = g.get("email", {})
        if gr and gr.get("summary"):
            members = gr["summary"].get("total_members")
        nls = em.get("newsletters", []) if em else []
        if nls:
            subscribers = nls[0].get("subscribers")
            open_rate = nls[0].get("open_rate")
        if members is not None:
            break

    def delta(curr, prev):
        if curr is None or prev is None:
            return ""
        d = curr - prev
        sign = "+" if d >= 0 else ""
        if isinstance(d, float):
            return f" ({sign}{d:.1f})"
        return f" ({sign}{d})"

    print(f"# Analytics Summary — Last {days} days")
    print(f"Period: {snaps[0]['date'] if snaps else '?'} to {snaps[-1]['date'] if snaps else '?'}")
    print(f"Data points: {len(snaps)}")
    print()
    print("## Key Metrics")
    print(f"- Visitors: {visitors or 'N/A'}{delta(visitors, visitors_prev)}")
    print(f"- Pageviews: {pageviews or 'N/A'}")
    print(f"- Bounce rate: {bounce or 'N/A'}%")
    print(f"- Members: {members or 'N/A'}")
    print(f"- Subscribers: {subscribers or 'N/A'}")
    print(f"- Open rate: {open_rate or 'N/A'}%")
    print(f"- Search impressions: {impressions or 'N/A'}")
    print(f"- Search clicks: {clicks or 'N/A'}")
    print(f"- Avg position: {position or 'N/A'}{delta(position, position_prev) if position and position_prev else ''}")
    print()

    # Top sources
    if snaps:
        src_totals = {}
        for s in snaps:
            for row in s.get("plausible", {}).get("sources", []):
                name = row.get("dimensions", ["?"])[0]
                val = row.get("metrics", [0])[0]
                src_totals[name] = src_totals.get(name, 0) + val
        if src_totals:
            total = sum(src_totals.values())
            print("## Traffic Sources")
            for name, val in sorted(src_totals.items(), key=lambda x: -x[1])[:8]:
                pct = round(val / total * 100) if total > 0 else 0
                print(f"- {name}: {val} ({pct}%)")
            print()


def cmd_trends(args):
    conn = get_conn()
    days = args.days or 90

    periods = [7, 30, 90]
    if days not in periods:
        periods.append(days)
    periods = sorted([p for p in periods if p <= days])

    print(f"# Trends Overview")
    print()
    print("| Metric | " + " | ".join(f"Last {p}d" for p in periods) + " |")
    print("|--------|" + "|".join("--------|" for _ in periods))

    metrics = [
        ("Visitors", "plausible.aggregate.metrics.0", "sum"),
        ("Pageviews", "plausible.aggregate.metrics.1", "sum"),
        ("Bounce %", "plausible.aggregate.metrics.2", "avg"),
        ("Impressions", "gsc.totals.impressions", "sum"),
        ("Clicks", "gsc.totals.clicks", "sum"),
        ("Avg Position", "gsc.totals.position", "avg"),
    ]

    for name, path, mode in metrics:
        vals = []
        for p in periods:
            rows = get_snapshots(conn, p)
            snaps = [parse_snapshot(r) for r in rows]
            def extract(s, path=path):
                parts = path.split(".")
                val = s
                for pt in parts:
                    if val is None: return None
                    if isinstance(val, list):
                        try: val = val[int(pt)]
                        except: return None
                    elif isinstance(val, dict): val = val.get(pt)
                    else: return None
                return val
            v = agg_metric(snaps, extract, mode)
            if v is not None:
                vals.append(f"{v:,.1f}" if isinstance(v, float) else f"{v:,}")
            else:
                vals.append("N/A")
        print(f"| {name} | " + " | ".join(vals) + " |")
    print()


def cmd_seo(args):
    conn = get_conn()
    days = args.days or 30
    rows = get_snapshots(conn, days)
    prev_rows = get_snapshots(conn, days * 2)
    prev_rows = [r for r in prev_rows if r not in rows]

    snaps = [parse_snapshot(r) for r in rows]
    prev_snaps = [parse_snapshot(r) for r in prev_rows]

    # Aggregate queries
    queries = {}
    for s in snaps:
        for row in s.get("gsc", {}).get("queries", []):
            q = row["keys"][0]
            if q not in queries:
                queries[q] = {"imp": 0, "clicks": 0, "positions": []}
            queries[q]["imp"] += row.get("impressions", 0)
            queries[q]["clicks"] += row.get("clicks", 0)
            queries[q]["positions"].append(row.get("position", 0))

    prev_queries = {}
    for s in prev_snaps:
        for row in s.get("gsc", {}).get("queries", []):
            q = row["keys"][0]
            if q not in prev_queries:
                prev_queries[q] = {"positions": []}
            prev_queries[q]["positions"].append(row.get("position", 0))

    print(f"# SEO Report — Last {days} days")
    print()

    # Top queries
    print("## Top Queries")
    print("| Query | Impressions | Clicks | CTR | Position | Prev | Trend | Priority |")
    print("|-------|-------------|--------|-----|----------|------|-------|----------|")

    sorted_q = sorted(queries.items(), key=lambda x: -x[1]["imp"])
    for q, data in sorted_q[:15]:
        pos = round(sum(data["positions"]) / len(data["positions"]), 1) if data["positions"] else None
        prev_pos = None
        if q in prev_queries and prev_queries[q]["positions"]:
            prev_pos = round(sum(prev_queries[q]["positions"]) / len(prev_queries[q]["positions"]), 1)
        ctr = round(data["clicks"] / data["imp"] * 100, 1) if data["imp"] > 0 else 0
        trend = ""
        if pos and prev_pos:
            if pos < prev_pos:
                trend = f"▲ {round(prev_pos - pos, 1)}"
            elif pos > prev_pos:
                trend = f"▼ {round(pos - prev_pos, 1)}"

        # Priority score
        expected_ctr = 30 if pos and pos <= 1 else 15 if pos and pos <= 3 else 8 if pos and pos <= 5 else 3 if pos and pos <= 10 else 1
        gap = max(0, expected_ctr - ctr)
        priority = round(data["imp"] * gap / pos) if pos and pos > 0 else 0

        # Opportunity
        opp = ""
        if pos and pos <= 10 and data["clicks"] == 0 and data["imp"] > 10:
            opp = " ← FIX META"
        elif prev_pos and pos and pos < prev_pos and prev_pos - pos > 2:
            opp = " ← RISING"

        print(f"| {q} | {data['imp']:,} | {data['clicks']} | {ctr}% | {pos or 'N/A'} | {prev_pos or 'N/A'} | {trend} | {priority}{opp} |")

    print()

    # Opportunities summary
    opps = [(q, d) for q, d in sorted_q if d["imp"] > 10 and d["clicks"] == 0]
    page1_opps = []
    for q, d in opps:
        pos = round(sum(d["positions"]) / len(d["positions"]), 1) if d["positions"] else 99
        if pos <= 10:
            page1_opps.append((q, d["imp"], pos))

    if page1_opps:
        print("## Zero-Click Opportunities (Page 1)")
        for q, imp, pos in sorted(page1_opps, key=lambda x: -x[1]):
            print(f"- \"{q}\" — {imp} impressions at position {pos}")
        print()

    # Suggest data
    latest = snaps[-1] if snaps else {}
    suggest = latest.get("suggest", {})
    if suggest:
        existing_queries = set(q.lower() for q in queries.keys())
        gaps = []
        for seed, suggestions in suggest.items():
            for s in suggestions:
                if s.lower() not in existing_queries and s.lower() != seed.lower():
                    gaps.append((seed, s))
        if gaps:
            print("## Content Gaps (people search, you don't rank)")
            for seed, s in gaps[:8]:
                print(f"- \"{s}\" (from \"{seed}\")")
            print()


def cmd_content(args):
    conn = get_conn()
    rows = get_snapshots(conn)
    snaps = [parse_snapshot(r) for r in rows]

    # Get latest Ghost posts data
    posts = []
    for s in reversed(snaps):
        g = s.get("ghost", {})
        p = g.get("posts", {})
        if p and p.get("posts"):
            posts = p["posts"]
            break

    if not posts:
        print("No content data available yet.")
        return

    print("# Content Performance")
    print()

    # By theme
    themes = {}
    for post in posts:
        for tag in post.get("tags", []):
            if tag not in themes:
                themes[tag] = {"posts": 0, "views": 0, "members": 0, "open_rates": [], "click_rates": []}
            themes[tag]["posts"] += 1
            themes[tag]["views"] += post.get("views", 0)
            themes[tag]["members"] += post.get("members", 0)
            if post.get("open_rate"):
                themes[tag]["open_rates"].append(post["open_rate"])
            if post.get("click_rate"):
                themes[tag]["click_rates"].append(post["click_rate"])

    if themes:
        print("## Performance by Theme")
        print("| Theme | Posts | Views | Members | Avg Open | Avg Click |")
        print("|-------|-------|-------|---------|----------|-----------|")
        for tag, data in sorted(themes.items(), key=lambda x: -x[1]["views"]):
            avg_open = f"{sum(data['open_rates'])/len(data['open_rates']):.1f}%" if data["open_rates"] else "N/A"
            avg_click = f"{sum(data['click_rates'])/len(data['click_rates']):.1f}%" if data["click_rates"] else "N/A"
            print(f"| {tag} | {data['posts']} | {data['views']:,} | {data['members']} | {avg_open} | {avg_click} |")
        print()

    # Top posts
    print("## Top Posts")
    print("| Title | Views | Open Rate | Click Rate | Members |")
    print("|-------|-------|-----------|------------|---------|")
    for p in sorted(posts, key=lambda x: -x.get("views", 0))[:10]:
        title = p.get("title", "?")
        if len(title) > 55:
            title = title[:52] + "..."
        print(f"| {title} | {p.get('views', 0):,} | {p.get('open_rate', 'N/A')}% | {p.get('click_rate', 'N/A')}% | {p.get('members', 0)} |")
    print()


def cmd_conversions(args):
    conn = get_conn()
    days = args.days or 30
    rows = get_snapshots(conn, days)
    snaps = [parse_snapshot(r) for r in rows]

    # Source → subscriber
    signups_by_src = {}
    visitors_by_src = {}
    for s in snaps:
        p = s.get("plausible", {})
        for row in p.get("signups_by_source", []):
            name = row.get("dimensions", ["?"])[0]
            signups_by_src[name] = signups_by_src.get(name, 0) + row.get("metrics", [0])[0]
        for row in p.get("sources", []):
            name = row.get("dimensions", ["?"])[0]
            visitors_by_src[name] = visitors_by_src.get(name, 0) + row.get("metrics", [0])[0]

    print(f"# Conversion Report — Last {days} days")
    print()
    print("## Source → Subscriber")
    print("| Source | Visitors | Signups | Conv. Rate |")
    print("|--------|----------|---------|------------|")
    all_sources = set(list(signups_by_src.keys()) + list(visitors_by_src.keys()))
    src_rows = []
    for src in all_sources:
        v = visitors_by_src.get(src, 0)
        s = signups_by_src.get(src, 0)
        rate = round(s / v * 100, 2) if v > 0 else 0
        src_rows.append((src, v, s, rate))
    for src, v, s, rate in sorted(src_rows, key=lambda x: -x[2]):
        if v > 0:
            print(f"| {src} | {v:,} | {s} | {rate}% |")
    print()

    # Page conversions
    signups_by_page = {}
    visitors_by_page = {}
    for s in snaps:
        p = s.get("plausible", {})
        for row in p.get("signups_by_page", []):
            name = row.get("dimensions", ["?"])[0]
            signups_by_page[name] = signups_by_page.get(name, 0) + row.get("metrics", [0])[0]
        for row in p.get("pages", []):
            name = row.get("dimensions", ["?"])[0]
            visitors_by_page[name] = visitors_by_page.get(name, 0) + row.get("metrics", [0])[0]

    print("## Top Converting Pages")
    print("| Page | Visitors | Signups | Conv. Rate |")
    print("|------|----------|---------|------------|")
    page_rows = []
    for page in set(list(signups_by_page.keys()) + list(visitors_by_page.keys())):
        v = visitors_by_page.get(page, 0)
        s = signups_by_page.get(page, 0)
        if v < 2:
            continue
        rate = round(s / v * 100, 2) if v > 0 else 0
        page_rows.append((page, v, s, rate))
    for page, v, s, rate in sorted(page_rows, key=lambda x: -x[2])[:10]:
        print(f"| {page} | {v:,} | {s} | {rate}% |")
    print()


def cmd_query(args):
    conn = get_conn()
    sql = args.sql
    use_json = getattr(args, "json", False)
    try:
        rows = conn.execute(sql).fetchall()
        if not rows:
            print("No results.")
            return
        keys = rows[0].keys()
        if use_json:
            result = []
            for row in rows:
                d = {}
                for k in keys:
                    v = row[k]
                    # Try to parse JSON columns
                    if isinstance(v, str) and v.startswith(("{", "[")):
                        try:
                            v = json.loads(v)
                        except json.JSONDecodeError:
                            pass
                    d[k] = v
                result.append(d)
            print(json.dumps(result, indent=2))
        else:
            print("| " + " | ".join(keys) + " |")
            print("|" + "|".join("---" for _ in keys) + "|")
            for row in rows:
                vals = []
                for k in keys:
                    v = row[k]
                    if isinstance(v, str) and len(v) > 80:
                        v = v[:77] + "..."
                    vals.append(str(v) if v is not None else "NULL")
                print("| " + " | ".join(vals) + " |")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_freshness(args):
    conn = get_conn()
    latest = conn.execute("SELECT date FROM snapshots ORDER BY date DESC LIMIT 1").fetchone()
    oldest = conn.execute("SELECT date FROM snapshots ORDER BY date ASC LIMIT 1").fetchone()
    count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    has_suggest = conn.execute("SELECT COUNT(*) FROM snapshots WHERE suggest IS NOT NULL AND suggest != '{}'").fetchone()[0]
    has_ghost = conn.execute("SELECT COUNT(*) FROM snapshots WHERE ghost != '{}'").fetchone()[0]

    latest_date = latest[0] if latest else "none"
    oldest_date = oldest[0] if oldest else "none"
    today = datetime.now().strftime("%Y-%m-%d")
    stale = latest_date < today if latest else True

    print(f"# Data Freshness")
    print(f"- Latest snapshot: {latest_date}")
    print(f"- Oldest snapshot: {oldest_date}")
    print(f"- Total snapshots: {count}")
    print(f"- Snapshots with Ghost data: {has_ghost}")
    print(f"- Snapshots with suggest data: {has_suggest}")
    print(f"- Today: {today}")
    print(f"- Stale: {'YES — run collection or pull from repo' if stale else 'No — data is current'}")
    if stale:
        print()
        print("To update:")
        print("  git -C /Users/luisnatera/Documents/tynstudio/photo-analytics pull")
        print("  # or collect fresh data:")
        print("  uv run site-report.py --days 7 --save")


def cmd_brief(args):
    """Full LLM-friendly brief combining all reports."""
    print("# Analytics Brief — " + datetime.now().strftime("%Y-%m-%d"))
    print()

    # Temporarily redirect args
    class Args:
        days = args.days or 30
        sql = None
    a = Args()

    cmd_summary(a)
    cmd_seo(a)
    cmd_content(a)
    cmd_conversions(a)


def main():
    parser = argparse.ArgumentParser(description="LLM-friendly analytics reports")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("summary", help="Latest snapshot overview")
    p.add_argument("--days", type=int, help="Period in days (default: 7)")

    p = sub.add_parser("trends", help="Key metric trends")
    p.add_argument("--days", type=int, help="Max period in days (default: 90)")

    p = sub.add_parser("seo", help="SEO opportunities")
    p.add_argument("--days", type=int, help="Period in days (default: 30)")

    p = sub.add_parser("content", help="Content performance")

    p = sub.add_parser("conversions", help="Conversion data")
    p.add_argument("--days", type=int, help="Period in days (default: 30)")

    p = sub.add_parser("query", help="Run arbitrary SQL")
    p.add_argument("sql", help="SQL query")
    p.add_argument("--json", action="store_true", help="Output as JSON (with parsed JSON columns)")

    p = sub.add_parser("brief", help="Full brief for LLM context")
    p.add_argument("--days", type=int, help="Period in days (default: 30)")

    p = sub.add_parser("freshness", help="Check data freshness and how to update")

    args = parser.parse_args()

    commands = {
        "summary": cmd_summary,
        "trends": cmd_trends,
        "seo": cmd_seo,
        "content": cmd_content,
        "conversions": cmd_conversions,
        "query": cmd_query,
        "brief": cmd_brief,
        "freshness": cmd_freshness,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
