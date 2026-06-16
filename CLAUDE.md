# CLAUDE.md

## Repository Overview

Analytics dashboard and data collection for https://luisnatera.photo, a photography newsletter. Collects data from Ghost CMS, Plausible Analytics, and Google Search Console. Stores historical snapshots in SQLite and generates a static HTML dashboard.

## How to Access Analytics Data

### Step 1: Check data freshness

Always start here. The GitHub Action runs daily at 6am UTC, so local data may be behind.

```bash
uv run report.py freshness
```

If stale, pull latest from GitHub:
```bash
git pull
```

Or collect fresh data locally:
```bash
uv run site-report.py --days 7 --save
```

Or trigger the GitHub Action:
```bash
gh workflow run analytics.yml --repo Tyn-Studio/photo-analytics
```

### Step 2: Pull reports

**Markdown output** (default) — best for reading, reasoning, and presenting to the user:
```bash
uv run report.py brief --days 30       # Full context: summary + SEO + content + conversions
uv run report.py summary --days 7      # Quick KPI overview
uv run report.py trends                # Compare metrics across 7d/30d/90d
uv run report.py seo --days 30         # Rankings, opportunities, content gaps
uv run report.py content               # Performance by theme/tag
uv run report.py conversions --days 30  # Source and page conversion rates
```

**JSON output** — best for calculations, comparisons, or when you need raw data:
```bash
uv run report.py query "SELECT date, json_extract(plausible, '$.aggregate.metrics[0]') as visitors FROM snapshots ORDER BY date DESC LIMIT 7" --json
```

### Step 3: When answering user questions

- **"How are things going?"** → Run `brief --days 7` and `brief --days 30` for context
- **"What should I write about?"** → Run `seo` for search opportunities + `content` for theme performance
- **"How did the latest post do?"** → Run `summary --days 7` + check Ghost post metrics
- **"Are we growing?"** → Run `trends` to compare periods
- **"Where should I promote?"** → Run `conversions` for source conversion rates

## Database Schema

SQLite at `data/analytics.db`:

```sql
CREATE TABLE snapshots (
    date TEXT PRIMARY KEY,   -- YYYY-MM-DD
    days INTEGER,            -- period length used for collection
    ghost TEXT,              -- JSON: Ghost CMS data
    plausible TEXT,          -- JSON: Plausible Analytics data
    gsc TEXT,                -- JSON: Google Search Console data
    suggest TEXT             -- JSON: Google Suggest related searches
);
```

### Key JSON paths

**Plausible:**
- `plausible.aggregate.metrics` = [visitors, pageviews, bounce_rate, visit_duration, visits]
- `plausible.pages[]` = {dimensions: [page], metrics: [visitors, pageviews, bounce_rate, duration]}
- `plausible.sources[]` = {dimensions: [source], metrics: [visitors]}
- `plausible.signups_by_source[]` = {dimensions: [source], metrics: [count]}
- `plausible.signups_by_page[]` = {dimensions: [page], metrics: [count]}

**Google Search Console:**
- `gsc.totals` = {clicks, impressions, ctr, position}
- `gsc.queries[]` = {keys: [query], clicks, impressions, ctr, position}
- `gsc.pages[]` = {keys: [url], clicks, impressions, ctr, position}

**Ghost:**
- `ghost.growth.summary` = {total_members, member_delta, paid_members, ...}
- `ghost.email.newsletters[0]` = {open_rate, click_rate, subscribers, sent_posts, recipients, opened, clicked}
- `ghost.posts.posts[]` = {title, views, open_rate, click_rate, members, tags[], sent_count, ...}

**Google Suggest:**
- `suggest` = {"seed keyword": ["suggestion1", "suggestion2", ...]}

### Useful SQL queries

```sql
-- Visitors trend (last 14 days)
SELECT date, json_extract(plausible, '$.aggregate.metrics[0]') as visitors
FROM snapshots ORDER BY date DESC LIMIT 14;

-- Search position for a specific query over time
SELECT s.date, json_extract(value, '$.keys[0]') as query,
       json_extract(value, '$.position') as position
FROM snapshots s, json_each(json_extract(s.gsc, '$.queries'))
WHERE query = 'photography for developers'
ORDER BY s.date DESC LIMIT 30;

-- Total members over time
SELECT date, json_extract(ghost, '$.growth.summary.total_members') as members
FROM snapshots WHERE members IS NOT NULL ORDER BY date;

-- All snapshots count and date range
SELECT COUNT(*) as total, MIN(date) as oldest, MAX(date) as latest FROM snapshots;
```

## GitHub Actions

- `.github/workflows/analytics.yml` — daily at 6am UTC + manual `workflow_dispatch`
- Trigger manually: `gh workflow run analytics.yml --repo Tyn-Studio/photo-analytics`
- Check status: `gh run list --repo Tyn-Studio/photo-analytics --limit 5`
- Secrets: GSC_CLIENT_ID, GSC_CLIENT_SECRET, GSC_REFRESH_TOKEN, PLAUSIBLE_API_KEY, PLAUSIBLE_SITE_ID

## Files

| File | Purpose |
|------|---------|
| `site-report.py` | Data collection from Ghost, Plausible, GSC, Google Suggest |
| `dashboard.py` | Generates static HTML dashboard from SQLite |
| `report.py` | LLM-friendly CLI for querying analytics (markdown + JSON) |
| `backfill.py` | One-time historical data backfill |
| `gsc-auth.py` | One-time OAuth setup for Google Search Console |
| `data/analytics.db` | SQLite database with all daily snapshots |
| `dashboard.html` | Generated dashboard (open in browser or via GitHub Pages) |
