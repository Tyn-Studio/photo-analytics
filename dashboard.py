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
        suggest_raw = row["suggest"] if "suggest" in row.keys() else None
        s = {
            "date": row["date"],
            "days": row["days"],
            "ghost": json.loads(row["ghost"]) if row["ghost"] else {},
            "plausible": json.loads(row["plausible"]) if row["plausible"] else {},
            "gsc": json.loads(row["gsc"]) if row["gsc"] else {},
            "suggest": json.loads(suggest_raw) if suggest_raw else {},
        }
        snapshots.append(s)
    return snapshots


def extract_metric(snapshot: dict, path: str):
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


def generate_html(snapshots: list[dict]) -> str:
    # Traffic metrics
    visitors = build_series(snapshots, "plausible.aggregate.metrics.0")
    pageviews = build_series(snapshots, "plausible.aggregate.metrics.1")
    bounce_rate = build_series(snapshots, "plausible.aggregate.metrics.2")

    # GSC metrics
    gsc_clicks = [extract_metric(s, "gsc.totals.clicks") for s in snapshots]
    gsc_impressions = [extract_metric(s, "gsc.totals.impressions") for s in snapshots]
    gsc_position = [extract_metric(s, "gsc.totals.position") for s in snapshots]

    # Ghost metrics
    members, subscribers, open_rate, click_rate = [], [], [], []
    emails_sent, recipients, opened, clicked = [], [], [], []
    for s in snapshots:
        ghost = s.get("ghost", {})
        growth = ghost.get("growth", {})
        email = ghost.get("email", {})
        summary = growth.get("summary", {}) if growth else {}
        members.append(summary.get("total_members") if summary else None)
        nls = email.get("newsletters", []) if email else []
        if nls:
            nl = nls[0]
            subscribers.append(nl.get("subscribers"))
            open_rate.append(nl.get("open_rate"))
            click_rate.append(nl.get("click_rate"))
            emails_sent.append(nl.get("sent_posts"))
            recipients.append(nl.get("recipients"))
            opened.append(nl.get("opened"))
            clicked.append(nl.get("clicked"))
        else:
            for lst in [subscribers, open_rate, click_rate, emails_sent, recipients, opened, clicked]:
                lst.append(None)

    all_data = {
        "dates": [s["date"] for s in snapshots],
        "visitors": visitors, "pageviews": pageviews, "bounce_rate": bounce_rate,
        "gsc_impressions": gsc_impressions, "gsc_clicks": gsc_clicks, "gsc_position": gsc_position,
        "members": members, "subscribers": subscribers,
        "emails_sent": emails_sent, "recipients": recipients,
        "opened": opened, "clicked": clicked,
        "open_rate": open_rate, "click_rate": click_rate,
    }

    # Per-snapshot source data
    all_sources = []
    for s in snapshots:
        src = s.get("plausible", {}).get("sources", [])
        labels = [row.get("dimensions", ["?"])[0] for row in src[:8]]
        values = [row.get("metrics", [0])[0] for row in src[:8]]
        all_sources.append({"labels": labels, "values": values})

    # Ghost posts data (for content themes, age, post count)
    latest_ghost_posts = []
    for s in reversed(snapshots):
        ghost = s.get("ghost", {})
        posts = ghost.get("posts", {})
        if posts and posts.get("posts"):
            latest_ghost_posts = posts["posts"]
            break

    # Per-snapshot: signups by source, signups by page, page engagement
    all_signups_by_source = []
    all_signups_by_page = []
    all_page_engagement = []
    for s in snapshots:
        p = s.get("plausible", {})
        sbs = p.get("signups_by_source", [])
        all_signups_by_source.append([{"src": r.get("dimensions", ["?"])[0], "n": r.get("metrics", [0])[0]} for r in sbs])
        sbp = p.get("signups_by_page", [])
        all_signups_by_page.append([{"page": r.get("dimensions", ["?"])[0], "n": r.get("metrics", [0])[0]} for r in sbp])
        pages = p.get("pages", [])
        engagement = []
        for row in pages[:15]:
            dims = row.get("dimensions", ["?"])
            metrics = row.get("metrics", [])
            engagement.append({
                "page": dims[0],
                "visitors": metrics[0] if len(metrics) > 0 else 0,
                "pageviews": metrics[1] if len(metrics) > 1 else 0,
                "bounce": metrics[2] if len(metrics) > 2 else None,
                "duration": metrics[3] if len(metrics) > 3 else None,
            })
        all_page_engagement.append(engagement)

    # Latest suggest data (use most recent non-empty)
    latest_suggest = {}
    for s in reversed(snapshots):
        if s.get("suggest"):
            latest_suggest = s["suggest"]
            break

    # SEO raw data: pass to JS for dynamic filtering
    seo_queries_raw: dict[str, list] = {}
    seo_pages_raw: dict[str, list] = {}
    for s in snapshots:
        date = s["date"]
        gsc = s.get("gsc", {})
        for row in gsc.get("queries", []):
            q = row["keys"][0]
            seo_queries_raw.setdefault(q, []).append({
                "d": date, "i": row.get("impressions", 0),
                "c": row.get("clicks", 0), "p": round(row.get("position", 0), 1),
            })
        for row in gsc.get("pages", []):
            p = row["keys"][0].replace("https://www.luisnatera.photo", "")
            seo_pages_raw.setdefault(p, []).append({
                "d": date, "i": row.get("impressions", 0),
                "c": row.get("clicks", 0), "p": round(row.get("position", 0), 1),
            })

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_snapshots = len(snapshots)

    # Use string concatenation to avoid f-string brace escaping hell
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>luisnatera.photo — Analytics</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #f7f7f7;
    min-height: 100vh;
    color: #1a1a1a;
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'SF Pro Display', sans-serif;
    padding: 0 2rem 2rem;
    padding-top: 4.5rem;
    font-size: 13px;
    -webkit-font-smoothing: antialiased;
    max-width: 1400px;
    margin: 0 auto;
}

/* Header */
.header {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    background: rgba(247,247,247,0.85);
    backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
    border-bottom: 1px solid rgba(0,0,0,0.06);
    padding: 0.6rem 2rem;
    display: flex; align-items: center; justify-content: space-between;
}
.header-left { display: flex; align-items: baseline; gap: 0.6rem; }
.header h1 { font-size: 0.95rem; font-weight: 600; color: #1a1a1a; letter-spacing: -0.01em; }
.header .meta { color: #aaa; font-size: 0.7rem; }
.controls { display: flex; align-items: center; gap: 0.2rem; }
.controls .sep { color: #ddd; margin: 0 0.4rem; }
.controls .label { color: #aaa; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.08em; margin-right: 0.2rem; }
.controls button {
    background: transparent; border: 1px solid rgba(0,0,0,0.08);
    color: #aaa; padding: 0.2rem 0.5rem;
    border-radius: 4px; cursor: pointer; font-size: 0.6rem;
    transition: all 0.15s ease;
}
.controls button:hover { border-color: rgba(0,0,0,0.2); color: #555; }
.controls button.active { background: #1a1a1a; color: #fff; border-color: #1a1a1a; }

/* KPI row */
.kpi-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 0.75rem; margin-bottom: 1.5rem; }
.kpi { background: #fff; border: 1px solid rgba(0,0,0,0.05); border-radius: 8px; padding: 1rem 1.1rem; }
.kpi-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin-bottom: 0.4rem; font-weight: 500; }
.kpi-value { font-size: 1.7rem; font-weight: 300; letter-spacing: -0.02em; line-height: 1; color: #1a1a1a; }
.kpi-delta { font-size: 0.7rem; margin-top: 0.3rem; font-weight: 400; }
.kpi-delta.up { color: #16a34a; }
.kpi-delta.down { color: #dc2626; }
.kpi-delta.neutral { color: #ccc; }

/* Insights */
.insights { background: #fff; border: 1px solid rgba(0,0,0,0.05); border-radius: 8px; padding: 1.1rem 1.25rem; margin-bottom: 1.5rem; }
.insights h2 { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin-bottom: 0.6rem; font-weight: 500; }
.insights ul { list-style: none; padding: 0; }
.insights li { padding: 0.3rem 0; color: #555; font-size: 0.85rem; line-height: 1.55; }
.insights li::before { content: '·'; margin-right: 0.5rem; color: #bbb; font-weight: 700; }
.insights .tag { display: inline-block; font-size: 0.65rem; padding: 0.12rem 0.4rem; border-radius: 3px; margin-left: 0.25rem; font-weight: 600; }
.insights .tag-opp { background: rgba(74,111,165,0.1); color: #3d6199; }
.insights .tag-win { background: rgba(22,163,74,0.1); color: #15803d; }
.insights .tag-warn { background: rgba(220,38,38,0.1); color: #b91c1c; }

/* Sections */
.section { margin-bottom: 1.5rem; }
.section-title { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: #999; margin-bottom: 0.75rem; font-weight: 500; }

/* Grid */
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.75rem; }
.card { background: #fff; border: 1px solid rgba(0,0,0,0.05); border-radius: 8px; padding: 1.1rem 1.25rem; }
.card h2 { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin-bottom: 0.9rem; font-weight: 500; }
.full { grid-column: 1 / -1; }
canvas { max-height: 240px; }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
th { text-align: left; color: #999; font-weight: 500; padding: 0.45rem 0.6rem; border-bottom: 1px solid rgba(0,0,0,0.06); font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.04em; cursor: pointer; user-select: none; }
th:hover { color: #555; }
th .sort-arrow { font-size: 0.55rem; margin-left: 0.2rem; color: #aaa; }
td { padding: 0.45rem 0.6rem; border-bottom: 1px solid rgba(0,0,0,0.03); color: #777; }
tr:hover td { color: #1a1a1a; }

@media (max-width: 900px) { .grid, .grid-3, .kpi-row { grid-template-columns: 1fr; } }
@media (min-width: 901px) and (max-width: 1100px) { .kpi-row { grid-template-columns: repeat(3, 1fr); } }
</style>
</head>
<body>

<div class="header">
<div class="header-left">
    <h1>luisnatera.photo</h1>
    <span class="meta">""" + now + " · " + str(n_snapshots) + """ pts</span>
</div>
<div class="controls">
    <span class="label">Range</span>
    <button class="range-btn" onclick="setRange(7, this)">7d</button>
    <button class="range-btn" onclick="setRange(30, this)">30d</button>
    <button class="range-btn" onclick="setRange(90, this)">90d</button>
    <button class="range-btn" onclick="setRange(180, this)">6m</button>
    <button class="range-btn active" onclick="setRange(0, this)">All</button>
    <span class="sep">·</span>
    <span class="label">Group</span>
    <button class="agg-btn active" onclick="setAgg('day', this)">Day</button>
    <button class="agg-btn" onclick="setAgg('week', this)">Week</button>
    <button class="agg-btn" onclick="setAgg('month', this)">Month</button>
</div>
</div>

<!-- KPIs -->
<div class="kpi-row" id="kpiRow"></div>

<!-- Insights + Opportunities -->
<div class="grid" style="margin-bottom:1.5rem;">
<div class="insights" id="insightsCard"></div>
<div class="insights" id="opportunitiesCard"></div>
</div>

<!-- TRAFFIC -->
<div class="section">
<div class="section-title">Traffic</div>
<div class="grid">
<div class="card"><h2>Visitors & Pageviews</h2><canvas id="trafficChart"></canvas></div>
<div class="card"><h2>Sources</h2><canvas id="sourcesChart"></canvas></div>
<div class="card full"><h2>Sources Over Time</h2><canvas id="sourcesTimeChart" style="max-height:200px;"></canvas></div>
</div>
</div>

<!-- CONVERSIONS -->
<div class="section">
<div class="section-title">Conversions</div>
<div class="grid">
<div class="card" id="convSourceCard"></div>
<div class="card" id="convPageCard"></div>
</div>
</div>

<!-- CONTENT -->
<div class="section">
<div class="section-title">Content</div>
<div class="grid">
<div class="card" id="contentThemeCard"></div>
<div class="card" id="contentEngagementCard"></div>
</div>
</div>

<!-- SEARCH -->
<div class="section">
<div class="section-title">Search</div>
<div class="grid">
<div class="card"><h2>Impressions & Clicks</h2><canvas id="searchChart"></canvas></div>
<div class="card"><h2>Position Trends</h2><canvas id="queryTrendsChart"></canvas></div>
<div class="card full" id="queryTableCard"></div>
<div class="card full" id="pageTableCard"></div>
</div>
</div>

<!-- DETAIL -->
<div class="section">
<div class="section-title">Detail</div>
<div class="grid">
<div class="card full" id="summaryCard"></div>
</div>
</div>

<script>
const DATA = """ + json.dumps(all_data) + """;
const SOURCES = """ + json.dumps(all_sources) + """;
const SEO_QUERIES = """ + json.dumps(seo_queries_raw) + """;
const SEO_PAGES = """ + json.dumps(seo_pages_raw) + """;
const SUGGEST = """ + json.dumps(latest_suggest) + """;
const GHOST_POSTS = """ + json.dumps(latest_ghost_posts) + """;
const SIGNUPS_BY_SOURCE = """ + json.dumps(all_signups_by_source) + """;
const SIGNUPS_BY_PAGE = """ + json.dumps(all_signups_by_page) + """;
const PAGE_ENGAGEMENT = """ + json.dumps(all_page_engagement) + """;
const COLORS = ['#1a1a1a','#444','#666','#888','#aaa','#bbb','#ccc','#ddd'];

const SUM_KEYS = ['visitors','pageviews','gsc_impressions','gsc_clicks','emails_sent','recipients','opened','clicked'];
const AVG_KEYS = ['bounce_rate','gsc_position','open_rate','click_rate'];
const LAST_KEYS = ['members','subscribers'];

let charts = {};
let currentRange = 0;
let currentAgg = 'day';

Chart.defaults.color = '#bbb';
Chart.defaults.borderColor = 'rgba(0,0,0,0.04)';
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif";

const chartOpts = {
    responsive: true,
    animation: { duration: 200 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
        legend: { labels: { color: '#777', font: { size: 11 }, boxWidth: 8, boxHeight: 8, padding: 16, usePointStyle: true, pointStyle: 'circle' } },
        tooltip: { backgroundColor: '#1a1a1a', borderColor: 'rgba(0,0,0,0.1)', borderWidth: 1, titleColor: '#999', bodyColor: '#fff', padding: 10, cornerRadius: 6, titleFont: { size: 11 }, bodyFont: { size: 12 } }
    },
    scales: {
        x: { ticks: { color: '#aaa', font: { size: 10 }, maxRotation: 0 }, grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false } },
        y: { ticks: { color: '#aaa', font: { size: 10 } }, grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false } }
    },
    elements: { line: { borderWidth: 1.5 }, point: { radius: 0, hoverRadius: 3, hoverBorderWidth: 2 } }
};

function slice(arr, n) {
    if (!n || n <= 0) return arr;
    return arr.slice(-n);
}

function bucketKey(date, agg) {
    if (agg === 'day') return date;
    if (agg === 'week') {
        const d = new Date(date);
        const day = d.getDay();
        const diff = d.getDate() - day + (day === 0 ? -6 : 1);
        const monday = new Date(d.setDate(diff));
        return monday.toISOString().slice(0, 10);
    }
    if (agg === 'month') return date.slice(0, 7);
    return date;
}

function aggregate(dates, series, agg, mode) {
    if (agg === 'day') return { labels: dates, values: series };
    const buckets = {};
    const bucketOrder = [];
    for (let i = 0; i < dates.length; i++) {
        const key = bucketKey(dates[i], agg);
        if (!buckets[key]) { buckets[key] = []; bucketOrder.push(key); }
        buckets[key].push(series[i]);
    }
    const values = bucketOrder.map(key => {
        const vals = buckets[key].filter(v => v !== null && v !== undefined);
        if (vals.length === 0) return null;
        if (mode === 'sum') return vals.reduce((a, b) => a + b, 0);
        if (mode === 'avg') return vals.reduce((a, b) => a + b, 0) / vals.length;
        if (mode === 'last') return vals[vals.length - 1];
        return vals[vals.length - 1];
    });
    return { labels: bucketOrder, values };
}

function aggMode(key) {
    if (SUM_KEYS.includes(key)) return 'sum';
    if (AVG_KEYS.includes(key)) return 'avg';
    return 'last';
}

function getAggSeries(key, n, agg) {
    return aggregate(slice(DATA.dates, n), slice(DATA[key], n), agg, aggMode(key));
}

function fmt(v, suffix) {
    if (v === null || v === undefined) return 'N/A';
    suffix = suffix || '';
    if (typeof v === 'number' && !Number.isInteger(v)) return v.toFixed(1) + suffix;
    if (typeof v === 'number' && v >= 1000) return v.toLocaleString() + suffix;
    return v + suffix;
}

function deltaStr(curr, prev) {
    if (curr === null || prev === null || curr === undefined || prev === undefined) return '';
    const d = curr - prev;
    const sign = d >= 0 ? '+' : '';
    return sign + (typeof d === 'number' && !Number.isInteger(d) ? d.toFixed(1) : d);
}

function periodAggregate(key, startIdx, endIdx) {
    const series = DATA[key].slice(startIdx, endIdx);
    const vals = series.filter(v => v !== null && v !== undefined);
    if (vals.length === 0) return null;
    const mode = aggMode(key);
    if (mode === 'sum') return vals.reduce((a, b) => a + b, 0);
    if (mode === 'avg') return vals.reduce((a, b) => a + b, 0) / vals.length;
    return vals[vals.length - 1];
}

function buildKpis(n) {
    const total = DATA.dates.length;
    const periodLen = (n > 0 && n < total) ? n : total;
    const currStart = total - periodLen;
    const prevStart = Math.max(0, currStart - periodLen);
    const prevEnd = currStart;
    const hasPrev = prevStart < prevEnd;

    const kpis = [
        { label: 'Visitors', key: 'visitors', suffix: '' },
        { label: 'Pageviews', key: 'pageviews', suffix: '' },
        { label: 'Members', key: 'members', suffix: '' },
        { label: 'Subscribers', key: 'subscribers', suffix: '' },
        { label: 'Impressions', key: 'gsc_impressions', suffix: '' },
        { label: 'Avg Position', key: 'gsc_position', suffix: '', invert: true },
    ];

    let html = '';
    for (const { label, key, suffix, invert } of kpis) {
        const cv = periodAggregate(key, currStart, total);
        const pv = hasPrev ? periodAggregate(key, prevStart, prevEnd) : null;
        const d = deltaStr(cv, pv);
        let cls = 'neutral';
        if (cv !== null && pv !== null) {
            if (invert) cls = cv < pv ? 'up' : cv > pv ? 'down' : 'neutral';
            else cls = cv > pv ? 'up' : cv < pv ? 'down' : 'neutral';
        }
        html += `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${fmt(cv, suffix)}</div>`;
        if (d) html += `<div class="kpi-delta ${cls}">${d}</div>`;
        html += '</div>';
    }
    document.getElementById('kpiRow').innerHTML = html;
}

function buildInsights(n) {
    const total = DATA.dates.length;
    const periodLen = (n > 0 && n < total) ? n : total;
    const currStart = total - periodLen;
    const prevStart = Math.max(0, currStart - periodLen);
    const prevEnd = currStart;
    const hasPrev = prevStart < prevEnd;

    const cv = (key) => periodAggregate(key, currStart, total);
    const pv = (key) => hasPrev ? periodAggregate(key, prevStart, prevEnd) : null;

    const items = [];
    const visitors = cv('visitors');
    const visitorsP = pv('visitors');
    const imp = cv('gsc_impressions');
    const impP = pv('gsc_impressions');
    const pos = cv('gsc_position');
    const posP = pv('gsc_position');
    const clicks = cv('gsc_clicks');

    // Traffic insight
    if (visitors !== null && visitorsP !== null && visitorsP > 0) {
        const pct = Math.round((visitors - visitorsP) / visitorsP * 100);
        if (pct > 10) items.push(`Traffic up ${pct}% vs previous period (${visitors.toLocaleString()} visitors) <span class="tag tag-win">growing</span>`);
        else if (pct < -10) items.push(`Traffic down ${Math.abs(pct)}% vs previous period (${visitors.toLocaleString()} visitors) <span class="tag tag-warn">declining</span>`);
        else items.push(`Traffic stable at ${visitors.toLocaleString()} visitors`);
    }

    // Search insight
    if (imp !== null && imp > 0) {
        let s = `${imp.toLocaleString()} search impressions`;
        if (clicks !== null) s += `, ${clicks} clicks`;
        if (pos !== null) s += ` at avg position ${pos.toFixed(1)}`;
        if (posP !== null && pos !== null && pos < posP - 0.5) s += ` <span class="tag tag-win">improving</span>`;
        else if (posP !== null && pos !== null && pos > posP + 0.5) s += ` <span class="tag tag-warn">dropping</span>`;
        if (clicks === 0 && imp > 50) s += ` <span class="tag tag-opp">CTR opportunity</span>`;
        items.push(s);
    }

    // Source concentration
    const srcSlice = slice(SOURCES, n);
    const srcTotals = {};
    for (const s of srcSlice) {
        for (let i = 0; i < s.labels.length; i++) {
            srcTotals[s.labels[i]] = (srcTotals[s.labels[i]] || 0) + (s.values[i] || 0);
        }
    }
    const sorted = Object.entries(srcTotals).sort((a, b) => b[1] - a[1]);
    const srcTotal = sorted.reduce((a, s) => a + s[1], 0);
    if (sorted.length > 0 && srcTotal > 0) {
        const topPct = Math.round(sorted[0][1] / srcTotal * 100);
        if (topPct > 70) items.push(`${sorted[0][0]} drives ${topPct}% of traffic <span class="tag tag-warn">concentrated</span>`);
    }

    // SEO opportunities from query data
    const { start, end } = getDateRange(n);
    const opps = [];
    for (const [query, entries] of Object.entries(SEO_QUERIES)) {
        const curr = filterEntries(entries, start, end);
        if (curr.length === 0) continue;
        const qImp = curr.reduce((a, e) => a + e.i, 0);
        const qClk = curr.reduce((a, e) => a + e.c, 0);
        const qPos = curr.reduce((a, e) => a + e.p, 0) / curr.length;
        if (qPos <= 10 && qClk === 0 && qImp > 10) opps.push({ query, imp: qImp, pos: qPos.toFixed(1) });
    }
    if (opps.length > 0) {
        opps.sort((a, b) => b.imp - a.imp);
        const top = opps[0];
        items.push(`"${top.query}" has ${top.imp} impressions at position ${top.pos} with zero clicks <span class="tag tag-opp">fix meta</span>`);
        if (opps.length > 1) items.push(`${opps.length} total queries ranking on page 1 with no clicks`);
    }

    // Newsletter
    const mem = cv('members');
    const memP = pv('members');
    if (mem !== null && memP !== null && mem > memP) {
        items.push(`+${mem - memP} new members this period (${mem} total)`);
    }

    const openRate = cv('open_rate');
    if (openRate !== null) {
        let s = `Email open rate: ${openRate.toFixed(1)}%`;
        if (openRate > 50) s += ' <span class="tag tag-win">strong</span>';
        items.push(s);
    }

    let html = '<h2>Insights</h2><ul>';
    for (const item of items) html += `<li>${item}</li>`;
    if (items.length === 0) html += '<li>Not enough data for insights yet</li>';
    html += '</ul>';
    document.getElementById('insightsCard').innerHTML = html;
}

function buildOpportunities(n) {
    const { start, end } = getDateRange(n);
    const periodLen = n > 0 ? n : DATA.dates.length;
    const prevEnd = start;
    const prevStartIdx = Math.max(0, DATA.dates.indexOf(start) - periodLen);
    const prevStart = DATA.dates[prevStartIdx] || DATA.dates[0];
    const hasPrev = prevStart < prevEnd;

    const items = [];

    // 1. Zero-click opportunities from GSC
    const opps = [];
    for (const [query, entries] of Object.entries(SEO_QUERIES)) {
        const curr = filterEntries(entries, start, end);
        if (curr.length === 0) continue;
        const imp = curr.reduce((a, e) => a + e.i, 0);
        const clk = curr.reduce((a, e) => a + e.c, 0);
        const pos = curr.reduce((a, e) => a + e.p, 0) / curr.length;
        if (pos <= 10 && clk === 0 && imp > 5) opps.push({ query, imp, pos: pos.toFixed(1) });
    }
    opps.sort((a, b) => b.imp - a.imp);
    if (opps.length > 0) {
        items.push(`<strong>Zero-click queries on page 1:</strong>`);
        for (const o of opps.slice(0, 3)) {
            items.push(`"${o.query}" — ${o.imp} imp, pos ${o.pos} <span class="tag tag-opp">fix meta</span>`);
        }
    }

    // 2. Rising queries (position improving)
    const rising = [];
    for (const [query, entries] of Object.entries(SEO_QUERIES)) {
        const curr = filterEntries(entries, start, end);
        const prev = hasPrev ? filterEntries(entries, prevStart, prevEnd) : [];
        if (curr.length < 3 || prev.length < 3) continue;
        const posNow = curr.reduce((a, e) => a + e.p, 0) / curr.length;
        const posPrev = prev.reduce((a, e) => a + e.p, 0) / prev.length;
        if (posNow < posPrev - 1) rising.push({ query, from: posPrev.toFixed(1), to: posNow.toFixed(1), delta: (posPrev - posNow).toFixed(1) });
    }
    rising.sort((a, b) => parseFloat(b.delta) - parseFloat(a.delta));
    if (rising.length > 0) {
        items.push(`<strong>Rising queries:</strong>`);
        for (const r of rising.slice(0, 3)) {
            items.push(`"${r.query}" pos ${r.from} → ${r.to} <span class="tag tag-win">+${r.delta}</span>`);
        }
    }

    // 3. Related searches people are looking for (from Google Suggest)
    if (Object.keys(SUGGEST).length > 0) {
        // Find suggestions we don't rank for yet
        const rankedQueries = new Set(Object.keys(SEO_QUERIES).map(q => q.toLowerCase()));
        const gaps = [];
        for (const [seed, suggestions] of Object.entries(SUGGEST)) {
            for (const s of suggestions) {
                if (!rankedQueries.has(s.toLowerCase()) && s.toLowerCase() !== seed.toLowerCase()) {
                    gaps.push({ seed, suggestion: s });
                }
            }
        }
        if (gaps.length > 0) {
            items.push(`<strong>Content gaps (people search, you don't rank):</strong>`);
            for (const g of gaps.slice(0, 4)) {
                items.push(`"${g.suggestion}" <span class="tag tag-opp">write about</span>`);
            }
        }
    }

    let html = '<h2>Opportunities</h2><ul>';
    for (const item of items) html += `<li>${item}</li>`;
    if (items.length === 0) html += '<li>No opportunities identified yet</li>';
    html += '</ul>';
    document.getElementById('opportunitiesCard').innerHTML = html;
}

function buildSummary(n) {
    const total = DATA.dates.length;
    const periodLen = (n > 0 && n < total) ? n : total;
    const currStart = total - periodLen;
    const prevStart = Math.max(0, currStart - periodLen);
    const prevEnd = currStart;
    const hasPrev = prevStart < prevEnd;
    const periodLabel = n > 0 ? `Last ${n}d` : 'All time';
    const prevLabel = hasPrev ? `Prev ${periodLen}d` : '';

    const rows = [
        ['Visitors', 'visitors', ''], ['Pageviews', 'pageviews', ''],
        ['Bounce Rate', 'bounce_rate', '%'],
        ['Search Impressions', 'gsc_impressions', ''], ['Search Position', 'gsc_position', ''],
        ['Members', 'members', ''], ['Subscribers', 'subscribers', ''],
        ['Emails Sent', 'emails_sent', ''], ['Recipients', 'recipients', ''],
        ['Opened', 'opened', ''], ['Clicked', 'clicked', ''],
        ['Open Rate', 'open_rate', '%'], ['Click Rate', 'click_rate', '%'],
    ];

    let html = `<h2>Summary</h2><table><tr><th>Metric</th><th>${periodLabel}</th><th>${prevLabel}</th><th>Change</th></tr>`;
    for (const [name, key, suf] of rows) {
        const cv = periodAggregate(key, currStart, total);
        const pv = hasPrev ? periodAggregate(key, prevStart, prevEnd) : null;
        html += `<tr><td>${name}</td><td>${fmt(cv, suf)}</td><td>${fmt(pv, suf)}</td><td>${deltaStr(cv, pv)}</td></tr>`;
    }
    html += '</table>';
    document.getElementById('summaryCard').innerHTML = html;
}

function renderCharts(n, agg) {
    Object.values(charts).forEach(c => c.destroy());
    charts = {};

    const tv = getAggSeries('visitors', n, agg);
    const tp = getAggSeries('pageviews', n, agg);

    buildKpis(n);
    buildInsights(n);
    buildOpportunities(n);

    charts.traffic = new Chart(document.getElementById('trafficChart'), {
        type: 'line',
        data: { labels: tv.labels, datasets: [
            { label: 'Visitors', data: tv.values, borderColor: '#1a1a1a', backgroundColor: 'rgba(0,0,0,0.04)', fill: true, tension: 0.4, borderWidth: 1.5 },
            { label: 'Pageviews', data: tp.values, borderColor: '#bbb', borderDash: [4,3], tension: 0.4, borderWidth: 1.5 }
        ]},
        options: chartOpts
    });

    const si = getAggSeries('gsc_impressions', n, agg);
    const sc = getAggSeries('gsc_clicks', n, agg);
    const sp = getAggSeries('gsc_position', n, agg);

    charts.search = new Chart(document.getElementById('searchChart'), {
        type: 'line',
        data: { labels: si.labels, datasets: [
            { label: 'Impressions', data: si.values, borderColor: '#1a1a1a', backgroundColor: 'rgba(0,0,0,0.04)', fill: true, tension: 0.4, yAxisID: 'y', borderWidth: 1.5 },
            { label: 'Clicks', data: sc.values, borderColor: '#c0392b', tension: 0.4, yAxisID: 'y', borderWidth: 2 },
            { label: 'Position', data: sp.values, borderColor: '#bbb', tension: 0.4, yAxisID: 'y1', borderDash: [3,3], borderWidth: 1.5 }
        ]},
        options: { ...chartOpts, scales: { ...chartOpts.scales,
            y1: { position: 'right', reverse: true, ticks: { color: '#ccc', font: { size: 9 } }, grid: { display: false, drawBorder: false } }
        }}
    });

    const srcSlice = slice(SOURCES, n);
    const srcTotals = {};
    for (const s of srcSlice) {
        for (let i = 0; i < s.labels.length; i++) {
            srcTotals[s.labels[i]] = (srcTotals[s.labels[i]] || 0) + (s.values[i] || 0);
        }
    }
    const sorted = Object.entries(srcTotals).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const srcTotal = sorted.reduce((a, s) => a + s[1], 0);
    const srcLabels = sorted.map(s => {
        const pct = srcTotal > 0 ? Math.round(s[1] / srcTotal * 100) : 0;
        return s[0] + '  ' + pct + '%';
    });
    const barColors = sorted.map((_, i) => {
        const greys = ['#1a1a1a','#444','#666','#888','#aaa','#bbb','#ccc','#ddd'];
        return greys[i] || '#ddd';
    });

    charts.sources = new Chart(document.getElementById('sourcesChart'), {
        type: 'bar',
        data: { labels: srcLabels, datasets: [{ data: sorted.map(s => s[1]), backgroundColor: barColors, borderRadius: 4, barThickness: 14 }] },
        options: {
            indexAxis: 'y', responsive: true,
            plugins: { legend: { display: false },
                tooltip: { backgroundColor: '#1a1a1a', bodyColor: '#fff', padding: 8, cornerRadius: 6,
                    callbacks: { label: (ctx) => { const pct = srcTotal > 0 ? Math.round(ctx.raw / srcTotal * 100) : 0; return ctx.raw.toLocaleString() + ' visitors (' + pct + '%)'; } }
                }
            },
            scales: {
                x: { ticks: { color: '#aaa', font: { size: 9 } }, grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false } },
                y: { ticks: { color: '#666', font: { size: 10 } }, grid: { display: false, drawBorder: false } }
            }
        }
    });

    buildSummary(n);
    buildConversions(n);
    buildContentInsights(n);
    buildSourcesTime(n, agg);
}

function buildSourcesTime(n, agg) {
    if (charts.sourcesTime) charts.sourcesTime.destroy();
    const sliced = slice(SOURCES, n);
    const dates = slice(DATA.dates, n);

    // Get top 5 sources across the range
    const totals = {};
    for (const s of sliced) {
        for (let i = 0; i < s.labels.length; i++) {
            totals[s.labels[i]] = (totals[s.labels[i]] || 0) + (s.values[i] || 0);
        }
    }
    const topSources = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0]);
    const srcStyles = [
        { color: '#1a1a1a', dash: [] },
        { color: '#888', dash: [6,3] },
        { color: '#aaa', dash: [3,3] },
        { color: '#bbb', dash: [8,4,2,4] },
        { color: '#4a6fa5', dash: [4,3] },
    ];

    // Build per-source time series
    const datasets = topSources.map((src, idx) => {
        const series = dates.map((d, i) => {
            const snap = sliced[i];
            const j = snap.labels.indexOf(src);
            return j >= 0 ? snap.values[j] : 0;
        });
        const agged = aggregate(dates, series, agg, 'sum');
        const style = srcStyles[idx % srcStyles.length];
        return { label: src, data: agged.values, borderColor: style.color, borderDash: style.dash, tension: 0.4, fill: idx === 0, backgroundColor: idx === 0 ? 'rgba(0,0,0,0.03)' : undefined };
    });

    const labelsAgg = aggregate(dates, dates, agg, 'last');
    charts.sourcesTime = new Chart(document.getElementById('sourcesTimeChart'), {
        type: 'line',
        data: { labels: labelsAgg.values, datasets },
        options: chartOpts
    });
}

function buildConversions(n) {
    const sliced_sbs = slice(SIGNUPS_BY_SOURCE, n);
    const sliced_sbp = slice(SIGNUPS_BY_PAGE, n);
    const sliced_src = slice(SOURCES, n);

    // Aggregate signups by source across range
    const signupsBySrc = {};
    for (const snap of sliced_sbs) {
        for (const r of snap) { signupsBySrc[r.src] = (signupsBySrc[r.src] || 0) + r.n; }
    }
    // Aggregate visitors by source across range
    const visitorsBySrc = {};
    for (const snap of sliced_src) {
        for (let i = 0; i < snap.labels.length; i++) {
            visitorsBySrc[snap.labels[i]] = (visitorsBySrc[snap.labels[i]] || 0) + (snap.values[i] || 0);
        }
    }

    let srcHtml = '<h2>Source → Subscriber</h2><table><tr><th>Source</th><th>Visitors</th><th>Signups</th><th>Conv. Rate</th></tr>';
    const allSources = new Set([...Object.keys(signupsBySrc), ...Object.keys(visitorsBySrc)]);
    const srcRows = [];
    for (const src of allSources) {
        const v = visitorsBySrc[src] || 0;
        const s = signupsBySrc[src] || 0;
        const rate = v > 0 ? (s / v * 100).toFixed(2) : '0.00';
        srcRows.push({ src, v, s, rate: parseFloat(rate) });
    }
    srcRows.sort((a, b) => b.s - a.s || b.rate - a.rate);
    for (const r of srcRows.filter(r => r.v > 0).slice(0, 10)) {
        const highlight = r.s > 0 ? 'color:#1a1a1a;font-weight:500' : '';
        srcHtml += `<tr><td>${r.src}</td><td>${r.v.toLocaleString()}</td><td style="${highlight}">${r.s}</td><td style="${highlight}">${r.rate}%</td></tr>`;
    }
    srcHtml += '</table>';
    document.getElementById('convSourceCard').innerHTML = srcHtml;

    // Aggregate signups by page
    const signupsByPage = {};
    for (const snap of sliced_sbp) {
        for (const r of snap) { signupsByPage[r.page] = (signupsByPage[r.page] || 0) + r.n; }
    }
    // Aggregate visitors by page
    const visitorsByPage = {};
    const sliced_pe = slice(PAGE_ENGAGEMENT, n);
    for (const snap of sliced_pe) {
        for (const r of snap) { visitorsByPage[r.page] = (visitorsByPage[r.page] || 0) + r.visitors; }
    }

    let pageHtml = '<h2>Top Converting Pages</h2><table><tr><th>Page</th><th>Visitors</th><th>Signups</th><th>Conv. Rate</th></tr>';
    const pageRows = [];
    const allPages = new Set([...Object.keys(signupsByPage), ...Object.keys(visitorsByPage)]);
    for (const page of allPages) {
        const v = visitorsByPage[page] || 0;
        const s = signupsByPage[page] || 0;
        if (v < 2) continue;
        const rate = v > 0 ? (s / v * 100).toFixed(2) : '0.00';
        pageRows.push({ page, v, s, rate: parseFloat(rate) });
    }
    pageRows.sort((a, b) => b.s - a.s || b.rate - a.rate);
    for (const r of pageRows.slice(0, 10)) {
        const highlight = r.s > 0 ? 'color:#1a1a1a;font-weight:500' : '';
        pageHtml += `<tr><td style="font-size:0.75rem">${r.page}</td><td>${r.v.toLocaleString()}</td><td style="${highlight}">${r.s}</td><td style="${highlight}">${r.rate}%</td></tr>`;
    }
    pageHtml += '</table>';
    document.getElementById('convPageCard').innerHTML = pageHtml;
}

function buildContentInsights(n) {
    // Content by theme (from Ghost tags)
    const themes = {};
    for (const post of GHOST_POSTS) {
        const tags = (post.tags || []);
        for (const tag of tags) {
            if (!themes[tag]) themes[tag] = { posts: 0, views: 0, members: 0, open_rates: [], click_rates: [] };
            themes[tag].posts++;
            themes[tag].views += post.views || 0;
            themes[tag].members += post.members || 0;
            if (post.open_rate) themes[tag].open_rates.push(post.open_rate);
            if (post.click_rate) themes[tag].click_rates.push(post.click_rate);
        }
    }

    let themeHtml = '<h2>Content by Theme</h2>';
    const themeEntries = Object.entries(themes).sort((a, b) => b[1].views - a[1].views);
    if (themeEntries.length > 0) {
        themeHtml += '<table><tr><th>Theme</th><th>Posts</th><th>Views</th><th>Members</th><th>Avg Open</th><th>Avg Click</th></tr>';
        for (const [tag, data] of themeEntries.slice(0, 8)) {
            const avgOpen = data.open_rates.length > 0 ? (data.open_rates.reduce((a,b) => a+b, 0) / data.open_rates.length).toFixed(1) + '%' : 'N/A';
            const avgClick = data.click_rates.length > 0 ? (data.click_rates.reduce((a,b) => a+b, 0) / data.click_rates.length).toFixed(1) + '%' : 'N/A';
            themeHtml += `<tr><td>${tag}</td><td>${data.posts}</td><td>${data.views.toLocaleString()}</td><td>${data.members}</td><td>${avgOpen}</td><td>${avgClick}</td></tr>`;
        }
        themeHtml += '</table>';
    } else {
        themeHtml += '<p style="color:#aaa;font-size:0.8rem">No theme data available yet</p>';
    }

    // Posts published count
    const postsCount = GHOST_POSTS.length;
    if (postsCount > 0) {
        themeHtml += `<p style="color:#888;font-size:0.78rem;margin-top:0.75rem">${postsCount} posts tracked</p>`;
    }

    document.getElementById('contentThemeCard').innerHTML = themeHtml;

    // Page engagement table
    const latestEngagement = PAGE_ENGAGEMENT.length > 0 ? slice(PAGE_ENGAGEMENT, n) : [];
    const engAgg = {};
    for (const snap of latestEngagement) {
        for (const r of snap) {
            if (!engAgg[r.page]) engAgg[r.page] = { visitors: 0, bounces: [], durations: [] };
            engAgg[r.page].visitors += r.visitors;
            if (r.bounce !== null) engAgg[r.page].bounces.push(r.bounce);
            if (r.duration !== null) engAgg[r.page].durations.push(r.duration);
        }
    }

    let engHtml = '<h2>Page Engagement</h2><table><tr><th>Page</th><th>Visitors</th><th>Bounce</th><th>Avg Duration</th></tr>';
    const engRows = Object.entries(engAgg).sort((a, b) => b[1].visitors - a[1].visitors);
    for (const [page, data] of engRows.slice(0, 10)) {
        const avgBounce = data.bounces.length > 0 ? (data.bounces.reduce((a,b)=>a+b,0) / data.bounces.length).toFixed(0) + '%' : 'N/A';
        const avgDur = data.durations.length > 0 ? Math.round(data.durations.reduce((a,b)=>a+b,0) / data.durations.length) + 's' : 'N/A';
        engHtml += `<tr><td style="font-size:0.75rem">${page}</td><td>${data.visitors.toLocaleString()}</td><td>${avgBounce}</td><td>${avgDur}</td></tr>`;
    }
    engHtml += '</table>';
    document.getElementById('contentEngagementCard').innerHTML = engHtml;
}

function setRange(n, btn) {
    currentRange = n;
    document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderCharts(currentRange, currentAgg);
    buildSeoTables(currentRange);
}

function setAgg(agg, btn) {
    currentAgg = agg;
    document.querySelectorAll('.agg-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderCharts(currentRange, currentAgg);
    buildSeoTables(currentRange);
}

let trendsChart = null;

function getDateRange(n) {
    const allDates = DATA.dates;
    if (!n || n <= 0) return { start: allDates[0], end: allDates[allDates.length - 1] };
    const sliced = allDates.slice(-n);
    return { start: sliced[0], end: sliced[sliced.length - 1] };
}

function filterEntries(entries, start, end) {
    return entries.filter(e => e.d >= start && e.d <= end);
}

function buildSeoTables(n) {
    const { start, end } = getDateRange(n);
    const periodLen = n > 0 ? n : DATA.dates.length;
    const prevEnd = start;
    const prevStartIdx = Math.max(0, DATA.dates.indexOf(start) - periodLen);
    const prevStart = DATA.dates[prevStartIdx] || DATA.dates[0];
    const hasPrev = prevStart < prevEnd;

    // Queries
    const qRows = [];
    for (const [query, entries] of Object.entries(SEO_QUERIES)) {
        const curr = filterEntries(entries, start, end);
        const prev = hasPrev ? filterEntries(entries, prevStart, prevEnd) : [];
        if (curr.length === 0) continue;
        const imp = curr.reduce((a, e) => a + e.i, 0);
        const clk = curr.reduce((a, e) => a + e.c, 0);
        const avgPos = curr.reduce((a, e) => a + e.p, 0) / curr.length;
        const prevPos = prev.length > 0 ? prev.reduce((a, e) => a + e.p, 0) / prev.length : null;
        const prevImp = prev.reduce((a, e) => a + e.i, 0);
        qRows.push({ query, imp, clk, ctr: imp > 0 ? (clk / imp * 100).toFixed(1) : '0.0',
            pos: avgPos.toFixed(1), prevPos: prevPos !== null ? prevPos.toFixed(1) : null, prevImp });
    }
    qRows.sort((a, b) => b.imp - a.imp);

    // Calculate priority score: impression volume × (1/position) × CTR gap
    for (const r of qRows) {
        const pos = parseFloat(r.pos);
        const expectedCtr = pos <= 1 ? 30 : pos <= 3 ? 15 : pos <= 5 ? 8 : pos <= 10 ? 3 : 1;
        const actualCtr = parseFloat(r.ctr);
        const gap = Math.max(0, expectedCtr - actualCtr);
        r.priority = Math.round(r.imp * gap / pos);
    }

    let qHtml = '<h2>Queries</h2><table><tr><th>Query</th><th>Impressions</th><th>Clicks</th><th>CTR</th><th>Position</th><th>Prev</th><th>Trend</th><th>Priority</th><th>Opportunity</th></tr>';
    for (const r of qRows.slice(0, 15)) {
        const pos = parseFloat(r.pos);
        const pp = r.prevPos !== null ? parseFloat(r.prevPos) : null;
        let trend = '<td style="color:#ccc">—</td>';
        if (pp !== null && pos < pp) trend = `<td style="color:#16a34a">▲ ${(pp - pos).toFixed(1)}</td>`;
        else if (pp !== null && pos > pp) trend = `<td style="color:#dc2626">▼ ${(pos - pp).toFixed(1)}</td>`;

        let opp = '';
        if (pos <= 10 && r.clk === 0 && r.imp > 10) opp = 'Page 1, zero clicks — fix meta';
        else if (pp !== null && pos < pp && pp - pos > 2) opp = 'Rising — keep building';
        else if (pp !== null && pos > pp && pos - pp > 2) opp = 'Dropping — needs attention';
        else if (pp === null && r.imp > 5) opp = 'New query — monitor';

        qHtml += `<tr><td>${r.query}</td><td>${r.imp.toLocaleString()}</td><td>${r.clk}</td><td>${r.ctr}%</td><td>${r.pos}</td><td>${r.prevPos || 'N/A'}</td>${trend}<td style="font-weight:500">${r.priority || 0}</td><td style="color:#aaa;font-size:0.75rem">${opp}</td></tr>`;
    }
    qHtml += '</table>';
    document.getElementById('queryTableCard').innerHTML = qHtml;

    // Pages
    const pRows = [];
    for (const [page, entries] of Object.entries(SEO_PAGES)) {
        const curr = filterEntries(entries, start, end);
        const prev = hasPrev ? filterEntries(entries, prevStart, prevEnd) : [];
        if (curr.length === 0) continue;
        const imp = curr.reduce((a, e) => a + e.i, 0);
        const clk = curr.reduce((a, e) => a + e.c, 0);
        const avgPos = curr.reduce((a, e) => a + e.p, 0) / curr.length;
        const prevPos = prev.length > 0 ? prev.reduce((a, e) => a + e.p, 0) / prev.length : null;
        pRows.push({ page, imp, clk, ctr: imp > 0 ? (clk / imp * 100).toFixed(1) : '0.0',
            pos: avgPos.toFixed(1), prevPos: prevPos !== null ? prevPos.toFixed(1) : null });
    }
    pRows.sort((a, b) => b.imp - a.imp);

    let pHtml = '<h2>Pages</h2><table><tr><th>Page</th><th>Impressions</th><th>Clicks</th><th>CTR</th><th>Position</th><th>Prev period</th><th>Trend</th></tr>';
    for (const r of pRows.slice(0, 15)) {
        const pos = parseFloat(r.pos);
        const pp = r.prevPos !== null ? parseFloat(r.prevPos) : null;
        let trend = '<td style="color:#ccc">—</td>';
        if (pp !== null && pos < pp) trend = `<td style="color:#16a34a">▲ ${(pp - pos).toFixed(1)}</td>`;
        else if (pp !== null && pos > pp) trend = `<td style="color:#dc2626">▼ ${(pos - pp).toFixed(1)}</td>`;
        pHtml += `<tr><td style="font-size:0.75rem">${r.page}</td><td>${r.imp.toLocaleString()}</td><td>${r.clk}</td><td>${r.ctr}%</td><td>${r.pos}</td><td>${r.prevPos || 'N/A'}</td>${trend}</tr>`;
    }
    pHtml += '</table>';
    document.getElementById('pageTableCard').innerHTML = pHtml;

    // Query trends chart — top 5 by impressions, aggregated by current grouping
    if (trendsChart) trendsChart.destroy();
    const topQueries = qRows.slice(0, 5).map(r => r.query);
    const trendStyles = [
        { color: '#1a1a1a', dash: [], width: 2 },
        { color: '#888', dash: [6,3], width: 1.5 },
        { color: '#aaa', dash: [3,3], width: 1.5 },
        { color: '#bbb', dash: [8,4,2,4], width: 1.5 },
        { color: '#4a6fa5', dash: [4,3], width: 1.5 },
    ];
    const agg = currentAgg;

    // Aggregate each query's position data by the current grouping
    function aggregatePositions(entries, agg) {
        const buckets = {};
        const order = [];
        for (const e of entries) {
            const key = bucketKey(e.d, agg);
            if (!buckets[key]) { buckets[key] = []; order.push(key); }
            buckets[key].push(e.p);
        }
        return { labels: order, values: order.map(k => buckets[k].reduce((a, b) => a + b, 0) / buckets[k].length) };
    }

    // Collect all bucket labels across queries
    const allBuckets = new Set();
    const queryAgg = {};
    for (const q of topQueries) {
        const filtered = filterEntries(SEO_QUERIES[q], start, end);
        const agged = aggregatePositions(filtered, agg);
        queryAgg[q] = {};
        agged.labels.forEach((l, i) => { queryAgg[q][l] = agged.values[i]; allBuckets.add(l); });
    }
    const sortedBuckets = [...allBuckets].sort();

    const datasets = topQueries.map((q, i) => {
        const style = trendStyles[i % trendStyles.length];
        return {
            label: q,
            data: sortedBuckets.map(b => queryAgg[q][b] !== undefined ? queryAgg[q][b] : null),
            borderColor: style.color, borderDash: style.dash, borderWidth: style.width,
            tension: 0.4, spanGaps: true,
        };
    });

    trendsChart = new Chart(document.getElementById('queryTrendsChart'), {
        type: 'line',
        data: { labels: sortedBuckets, datasets },
        options: {
            ...chartOpts,
            scales: {
                x: { ticks: { color: '#aaa', font: { size: 10 }, maxTicksLimit: 20, maxRotation: 0 }, grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false } },
                y: { reverse: true, title: { display: true, text: 'Position', color: '#aaa', font: { size: 10 } }, ticks: { color: '#aaa', font: { size: 10 } }, grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false } }
            }
        }
    });
}

function makeSortable(table) {
    const headers = table.querySelectorAll('th');
    headers.forEach((th, colIdx) => {
        let asc = true;
        th.addEventListener('click', () => {
            const tbody = table.querySelector('tbody') || table;
            const rows = Array.from(tbody.querySelectorAll('tr')).filter(r => r.querySelector('td'));
            rows.sort((a, b) => {
                const aVal = a.children[colIdx]?.textContent.trim() || '';
                const bVal = b.children[colIdx]?.textContent.trim() || '';
                const aNum = parseFloat(aVal.replace(/[,%]/g, ''));
                const bNum = parseFloat(bVal.replace(/[,%]/g, ''));
                if (!isNaN(aNum) && !isNaN(bNum)) return asc ? aNum - bNum : bNum - aNum;
                return asc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            });
            rows.forEach(r => tbody.appendChild(r));
            asc = !asc;
            headers.forEach(h => { const arrow = h.querySelector('.sort-arrow'); if (arrow) arrow.textContent = ''; });
            let arrow = th.querySelector('.sort-arrow');
            if (!arrow) { arrow = document.createElement('span'); arrow.className = 'sort-arrow'; th.appendChild(arrow); }
            arrow.textContent = asc ? ' ▼' : ' ▲';
        });
    });
}

// Observe new tables and make them sortable
const observer = new MutationObserver(() => {
    document.querySelectorAll('table').forEach(t => {
        if (!t.dataset.sortable) { makeSortable(t); t.dataset.sortable = '1'; }
    });
});
observer.observe(document.body, { childList: true, subtree: true });

renderCharts(0, 'day');
buildSeoTables(0);
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
    # Also write as index.html for GitHub Pages
    index_path = Path(__file__).parent / "index.html"
    index_path.write_text(html)
    print(f"Dashboard written to {OUTPUT} + {index_path} ({len(snapshots)} snapshots)", file=sys.stderr)


if __name__ == "__main__":
    main()
