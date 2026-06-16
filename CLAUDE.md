# CLAUDE.md

## Repository Overview

Analytics dashboard and data collection for https://luisnatera.photo, a photography newsletter. Collects data from Ghost CMS, Plausible Analytics, and Google Search Console. Stores historical snapshots in SQLite and generates a static HTML dashboard.

## How to Use the Analytics Data

### Quick reports (run from this repo's directory)

```bash
# Full brief — use this for comprehensive context
uv run report.py brief --days 30

# Individual reports
uv run report.py summary --days 7     # KPI overview
uv run report.py trends               # Metric trends across periods
uv run report.py seo --days 30        # SEO opportunities and rankings
uv run report.py content              # Content performance by theme
uv run report.py conversions --days 30 # Source and page conversion data

# Arbitrary SQL against the database
uv run report.py query "SELECT date, json_extract(plausible, '$.aggregate.metrics[0]') as visitors FROM snapshots ORDER BY date DESC LIMIT 7"
```

### Collecting fresh data

```bash
uv run site-report.py --days 7 --save   # Collect and save to SQLite
uv run dashboard.py                      # Regenerate HTML dashboard
```

### Database schema

SQLite at `data/analytics.db`:
- Table: `snapshots(date TEXT PRIMARY KEY, days INT, ghost TEXT, plausible TEXT, gsc TEXT, suggest TEXT)`
- Each column (except date/days) contains JSON with the full API response
- One row per day, upserts on date

### Key data paths in the JSON

**Plausible:** `plausible.aggregate.metrics` = [visitors, pageviews, bounce_rate, visit_duration, visits]
**GSC:** `gsc.totals` = {clicks, impressions, ctr, position}, `gsc.queries[]`, `gsc.pages[]`
**Ghost:** `ghost.growth.summary` = {total_members, ...}, `ghost.email.newsletters[0]` = {open_rate, click_rate, subscribers, ...}, `ghost.posts.posts[]` = {title, views, open_rate, click_rate, members, tags[]}
**Suggest:** `suggest` = {"seed keyword": ["suggestion1", "suggestion2", ...]}

## GitHub Actions

- `.github/workflows/analytics.yml` runs daily at 6am UTC + manual dispatch
- Collects data, regenerates dashboard, commits and pushes
- Secrets: GSC_CLIENT_ID, GSC_CLIENT_SECRET, GSC_REFRESH_TOKEN, PLAUSIBLE_API_KEY, PLAUSIBLE_SITE_ID

## Files

- `site-report.py` — data collection from Ghost, Plausible, GSC, Google Suggest
- `dashboard.py` — generates static HTML dashboard from SQLite data
- `report.py` — LLM-friendly CLI for querying analytics data
- `backfill.py` — one-time script to backfill historical data
- `gsc-auth.py` — one-time OAuth setup for Google Search Console
- `data/analytics.db` — SQLite database with all snapshots
- `dashboard.html` — generated dashboard (open in browser)
