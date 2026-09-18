#!/usr/bin/env python3
"""Update the Contacto theme's social-proof counters from live Ghost data.

Computes three numbers and writes them to the theme's custom settings
(shown on the homepage hero as "N readers | N countries | N issues"):

  social_proof_1  — subscribed member count
  social_proof_2  — distinct countries across subscribed members (geolocation)
  social_proof_3  — latest issue number (3-digit prefix of published post titles)

Runs in the daily GitHub Action after data collection. Idempotent: only
PUTs settings whose value actually changed. Auth matches site-report.py:
GHOST_URL/GHOST_STAFF_TOKEN env in CI, ghst keychain config locally.
"""

import json
import os
import re
import subprocess
import sys


def run_ghst(args: list[str], write: bool = False) -> dict:
    cmd = ["ghst"]
    if write:
        cmd.append("--enable-destructive-actions")
    cmd += args
    url = os.getenv("GHOST_URL", "")
    token = os.getenv("GHOST_STAFF_TOKEN", "")
    if url:
        cmd += ["--url", url]
    if token:
        cmd += ["--staff-token", token]
    cmd += ["--json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ghst {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def get_member_stats() -> tuple[int, int]:
    # subscribed:true must be filtered server-side; the client-side
    # `subscribed` flag reads false whenever `fields=` strips the
    # newsletters relation, and full member objects overflow ghst's
    # 64KB stdout, so keep the payload to geolocation only.
    meta = run_ghst([
        "api", "/members/",
        "--query", "limit=1", "filter=subscribed:true",
    ])
    readers = meta.get("meta", {}).get("pagination", {}).get("total", 0)

    data = run_ghst([
        "api", "/members/", "--paginate",
        "--query", "limit=100", "filter=subscribed:true", "fields=geolocation",
    ])
    countries = set()
    for m in data.get("members", []):
        geo = m.get("geolocation")
        if not geo:
            continue
        try:
            code = json.loads(geo).get("country_code")
        except (json.JSONDecodeError, AttributeError):
            continue
        if code:
            countries.add(code)
    return readers, len(countries)


def get_latest_issue() -> int:
    data = run_ghst([
        "api", "/posts/", "--paginate",
        "--query", "limit=100", "filter=status:published", "fields=title",
    ])
    numbers = []
    for post in data.get("posts", []):
        m = re.match(r"^(\d{3})\b", post.get("title", ""))
        if m:
            numbers.append(int(m.group(1)))
    if not numbers:
        sys.exit("no issue-numbered posts found; refusing to update counters")
    return max(numbers)


def main() -> None:
    readers, countries = get_member_stats()
    issues = get_latest_issue()
    if readers == 0 or countries == 0:
        sys.exit(f"implausible stats (readers={readers}, countries={countries}); "
                 "refusing to update counters")

    def plus(n: int) -> str:
        """Round down to the nearest ten: 95 -> '90+'. Exact below 10."""
        return f"{n // 10 * 10}+" if n >= 10 else str(n)

    wanted = {
        "social_proof_1": f"{plus(readers)} readers",
        "social_proof_2": f"{plus(countries)} countries",
        "social_proof_3": f"{issues} issues",
    }

    current = {
        s["key"]: s.get("value")
        for s in run_ghst(["api", "/custom_theme_settings/"]).get(
            "custom_theme_settings", []
        )
    }

    changed = {
        k: v for k, v in wanted.items() if k in current and current[k] != v
    }
    missing = [k for k in wanted if k not in current]
    if missing:
        print(f"  [warn] settings not in active theme, skipped: {missing}",
              file=sys.stderr)
    if not changed:
        print(f"Social proof up to date: {' | '.join(wanted.values())}")
        return

    body = {"custom_theme_settings": [{"key": k, "value": v}
                                      for k, v in changed.items()]}
    run_ghst(["api", "-X", "PUT", "/custom_theme_settings/",
              "--body", json.dumps(body)], write=True)
    print(f"Social proof updated: {' | '.join(wanted.values())}")


if __name__ == "__main__":
    main()
