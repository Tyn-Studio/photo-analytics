#!/usr/bin/env python3
"""
Generate a static HTML dashboard from analytics snapshots.

Usage:
    uv run dashboard.py
"""

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


DB_PATH = Path(__file__).parent / "data" / "analytics.db"
OUTPUT = Path(__file__).parent / "dashboard.html"


def load_snapshots() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM snapshots ORDER BY date ASC").fetchall()
    conn.close()

    snapshots = []
    for row in rows:
        s = {
            "date": row["date"],
            "days": row["days"],
            "ghost": json.loads(row["ghost"]) if row["ghost"] else {},
            "plausible": json.loads(row["plausible"]) if row["plausible"] else {},
            "gsc": json.loads(row["gsc"]) if row["gsc"] else {},
        }
        snapshots.append(s)
    return snapshots


def extract_metric(snapshot: dict, path: str):
    """Extract a nested metric like 'plausible.aggregate.metrics.0'."""
    parts = path.split(".")
    val = snapshot
    for p in parts:
        if val is None:
            return None
        if isinstance(val, list):
            try:
                val = val[int(p)]
            except (IndexError, ValueError):
                return None
        elif isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


def build_series(snapshots: list[dict], path: str) -> list:
    return [extract_metric(s, path) for s in snapshots]


def fmt_val(val, suffix=""):
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.1f}{suffix}"
    if isinstance(val, int) and val >= 1000:
        return f"{val:,}{suffix}"
    return f"{val}{suffix}"


def delta_str(current, previous):
    if current is None or previous is None:
        return ""
    diff = current - previous
    if isinstance(diff, float):
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.1f}"
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff}"


def generate_html(snapshots: list[dict]) -> str:
    dates = json.dumps([s["date"] for s in snapshots])

    # Traffic metrics
    visitors = build_series(snapshots, "plausible.aggregate.metrics.0")
    pageviews = build_series(snapshots, "plausible.aggregate.metrics.1")
    bounce_rate = build_series(snapshots, "plausible.aggregate.metrics.2")
    visit_duration = build_series(snapshots, "plausible.aggregate.metrics.3")

    # GSC metrics
    gsc_clicks = [extract_metric(s, "gsc.totals.clicks") for s in snapshots]
    gsc_impressions = [extract_metric(s, "gsc.totals.impressions") for s in snapshots]
    gsc_ctr = [round(extract_metric(s, "gsc.totals.ctr") * 100, 1) if extract_metric(s, "gsc.totals.ctr") is not None else None for s in snapshots]
    gsc_position = [extract_metric(s, "gsc.totals.position") for s in snapshots]

    # Ghost metrics
    members = []
    open_rate = []
    subscribers = []
    for s in snapshots:
        ghost = s.get("ghost", {})
        growth = ghost.get("growth", {})
        email = ghost.get("email", {})
        summary = growth.get("summary", {}) if growth else {}
        members.append(summary.get("total_members") if summary else None)
        nls = email.get("newsletters", []) if email else []
        if nls:
            open_rate.append(nls[0].get("open_rate"))
            subscribers.append(nls[0].get("subscribers"))
        else:
            open_rate.append(None)
            subscribers.append(None)

    # Source breakdown for latest snapshot
    sources_data = ""
    if snapshots:
        latest_sources = extract_metric(snapshots[-1], "plausible.sources") or []
        source_labels = []
        source_values = []
        for row in latest_sources[:8]:
            dims = row.get("dimensions", [])
            metrics = row.get("metrics", [])
            source_labels.append(dims[0] if dims else "?")
            source_values.append(metrics[0] if metrics else 0)
        sources_data = f"labels: {json.dumps(source_labels)}, values: {json.dumps(source_values)}"

    # Latest values for summary table
    latest = snapshots[-1] if snapshots else {}
    prev = snapshots[-2] if len(snapshots) > 1 else {}

    def lv(path):
        return extract_metric(latest, path)

    def pv(path):
        return extract_metric(prev, path)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>luisnatera.photo — Analytics Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 2rem; }}
h1 {{ font-size: 1.4rem; margin-bottom: 0.5rem; }}
.subtitle {{ color: #8b949e; margin-bottom: 2rem; font-size: 0.85rem; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 2rem; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.25rem; }}
.card h2 {{ font-size: 0.9rem; color: #8b949e; margin-bottom: 1rem; }}
canvas {{ max-height: 220px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
th {{ text-align: left; color: #8b949e; padding: 0.5rem 0.75rem; border-bottom: 1px solid #30363d; }}
td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid #21262d; }}
.delta {{ color: #8b949e; font-size: 0.8rem; }}
.delta.up {{ color: #3fb950; }}
.delta.down {{ color: #f85149; }}
.full {{ grid-column: 1 / -1; }}
@media (max-width: 768px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<h1>luisnatera.photo</h1>
<p class="subtitle">Analytics Dashboard — updated {now} — {len(snapshots)} data points</p>

<div class="grid">

<div class="card">
<h2>Traffic</h2>
<canvas id="trafficChart"></canvas>
</div>

<div class="card">
<h2>Search Performance</h2>
<canvas id="searchChart"></canvas>
</div>

<div class="card">
<h2>Newsletter</h2>
<canvas id="newsletterChart"></canvas>
</div>

<div class="card">
<h2>Sources (latest)</h2>
<canvas id="sourcesChart"></canvas>
</div>

<div class="card full">
<h2>Summary</h2>
<table>
<tr><th>Metric</th><th>Latest</th><th>Previous</th><th>Change</th></tr>
<tr><td>Visitors</td><td>{fmt_val(visitors[-1] if visitors else None)}</td><td>{fmt_val(visitors[-2] if len(visitors) > 1 else None)}</td><td>{delta_str(visitors[-1] if visitors else None, visitors[-2] if len(visitors) > 1 else None)}</td></tr>
<tr><td>Pageviews</td><td>{fmt_val(pageviews[-1] if pageviews else None)}</td><td>{fmt_val(pageviews[-2] if len(pageviews) > 1 else None)}</td><td>{delta_str(pageviews[-1] if pageviews else None, pageviews[-2] if len(pageviews) > 1 else None)}</td></tr>
<tr><td>Bounce Rate</td><td>{fmt_val(bounce_rate[-1] if bounce_rate else None, '%')}</td><td>{fmt_val(bounce_rate[-2] if len(bounce_rate) > 1 else None, '%')}</td><td>{delta_str(bounce_rate[-1] if bounce_rate else None, bounce_rate[-2] if len(bounce_rate) > 1 else None)}</td></tr>
<tr><td>Search Impressions</td><td>{fmt_val(gsc_impressions[-1] if gsc_impressions else None)}</td><td>{fmt_val(gsc_impressions[-2] if len(gsc_impressions) > 1 else None)}</td><td>{delta_str(gsc_impressions[-1] if gsc_impressions else None, gsc_impressions[-2] if len(gsc_impressions) > 1 else None)}</td></tr>
<tr><td>Search Position (avg)</td><td>{fmt_val(gsc_position[-1] if gsc_position else None)}</td><td>{fmt_val(gsc_position[-2] if len(gsc_position) > 1 else None)}</td><td>{delta_str(gsc_position[-1] if gsc_position else None, gsc_position[-2] if len(gsc_position) > 1 else None)}</td></tr>
<tr><td>Members</td><td>{fmt_val(members[-1] if members else None)}</td><td>{fmt_val(members[-2] if len(members) > 1 else None)}</td><td>{delta_str(members[-1] if members else None, members[-2] if len(members) > 1 else None)}</td></tr>
<tr><td>Open Rate</td><td>{fmt_val(open_rate[-1] if open_rate else None, '%')}</td><td>{fmt_val(open_rate[-2] if len(open_rate) > 1 else None, '%')}</td><td>{delta_str(open_rate[-1] if open_rate else None, open_rate[-2] if len(open_rate) > 1 else None)}</td></tr>
<tr><td>Subscribers</td><td>{fmt_val(subscribers[-1] if subscribers else None)}</td><td>{fmt_val(subscribers[-2] if len(subscribers) > 1 else None)}</td><td>{delta_str(subscribers[-1] if subscribers else None, subscribers[-2] if len(subscribers) > 1 else None)}</td></tr>
</table>
</div>

</div>

<script>
const dates = {dates};
const chartOpts = {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#8b949e', font: {{ size: 11 }} }} }} }},
    scales: {{
        x: {{ ticks: {{ color: '#8b949e', font: {{ size: 10 }} }}, grid: {{ color: '#21262d' }} }},
        y: {{ ticks: {{ color: '#8b949e', font: {{ size: 10 }} }}, grid: {{ color: '#21262d' }} }}
    }}
}};

new Chart(document.getElementById('trafficChart'), {{
    type: 'line',
    data: {{
        labels: dates,
        datasets: [
            {{ label: 'Visitors', data: {json.dumps(visitors)}, borderColor: '#58a6ff', tension: 0.3, pointRadius: 3 }},
            {{ label: 'Pageviews', data: {json.dumps(pageviews)}, borderColor: '#3fb950', tension: 0.3, pointRadius: 3 }}
        ]
    }},
    options: chartOpts
}});

new Chart(document.getElementById('searchChart'), {{
    type: 'line',
    data: {{
        labels: dates,
        datasets: [
            {{ label: 'Impressions', data: {json.dumps(gsc_impressions)}, borderColor: '#d2a8ff', tension: 0.3, yAxisID: 'y', pointRadius: 3 }},
            {{ label: 'Clicks', data: {json.dumps(gsc_clicks)}, borderColor: '#f0883e', tension: 0.3, yAxisID: 'y', pointRadius: 3 }},
            {{ label: 'Position', data: {json.dumps(gsc_position)}, borderColor: '#8b949e', tension: 0.3, yAxisID: 'y1', pointRadius: 3 }}
        ]
    }},
    options: {{
        ...chartOpts,
        scales: {{
            ...chartOpts.scales,
            y1: {{ position: 'right', reverse: true, ticks: {{ color: '#8b949e', font: {{ size: 10 }} }}, grid: {{ display: false }} }}
        }}
    }}
}});

new Chart(document.getElementById('newsletterChart'), {{
    type: 'line',
    data: {{
        labels: dates,
        datasets: [
            {{ label: 'Members', data: {json.dumps(members)}, borderColor: '#58a6ff', tension: 0.3, pointRadius: 3 }},
            {{ label: 'Subscribers', data: {json.dumps(subscribers)}, borderColor: '#3fb950', tension: 0.3, pointRadius: 3 }}
        ]
    }},
    options: chartOpts
}});

const srcData = {{ {sources_data} }};
new Chart(document.getElementById('sourcesChart'), {{
    type: 'doughnut',
    data: {{
        labels: srcData.labels || [],
        datasets: [{{ data: srcData.values || [], backgroundColor: ['#58a6ff','#3fb950','#d2a8ff','#f0883e','#f85149','#8b949e','#79c0ff','#56d364'] }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'right', labels: {{ color: '#8b949e', font: {{ size: 11 }} }} }} }} }}
}});
</script>
</body>
</html>"""
    return html


def main():
    if not DB_PATH.exists():
        print(f"No database found at {DB_PATH}. Run site-report.py --save first.", file=sys.stderr)
        raise SystemExit(1)

    snapshots = load_snapshots()
    if not snapshots:
        print("No snapshots in database.", file=sys.stderr)
        raise SystemExit(1)

    html = generate_html(snapshots)
    OUTPUT.write_text(html)
    print(f"Dashboard written to {OUTPUT} ({len(snapshots)} snapshots)", file=sys.stderr)


if __name__ == "__main__":
    main()
