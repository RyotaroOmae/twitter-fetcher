#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests
from dateutil import parser as dtparser

JST = timezone(timedelta(hours=9))

DEFAULT_API_BASE = "https://api.x.com/2"  # だめなら https://api.twitter.com/2 を指定


def read_accounts(p: Path) -> List[str]:
    out: List[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s[1:] if s.startswith("@") else s)
    return out


def yesterday_range_jst(ref: datetime) -> tuple[datetime, datetime]:
    ref = ref.astimezone(JST)
    today0 = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today0 - timedelta(days=1)
    end = today0
    return start, end


def iso_z(dt: datetime) -> str:
    """Convert dt to UTC ISO8601 with Z suffix (X API expects UTC)."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class XClient:
    def __init__(self, bearer: str, api_base: str):
        self.api_base = api_base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {bearer}"})

    def get_user_id(self, username: str) -> str:
        url = f"{self.api_base}/users/by/username/{username}"
        r = self.s.get(url, params={"user.fields": "id"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["data"]["id"]

    def get_tweets(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        max_results: int = 20,
        exclude: str = "retweets,replies",
    ) -> List[dict]:
        url = f"{self.api_base}/users/{user_id}/tweets"
        params = {
            "start_time": iso_z(start),
            "end_time": iso_z(end),
            "tweet.fields": "created_at,text",
            "max_results": str(max_results),
        }
        if exclude:
            params["exclude"] = exclude
        r = self.s.get(url, params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        return j.get("data", [])


def load_cache(cache_path: Path) -> Dict[str, str]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def save_cache(cache_path: Path, cache: Dict[str, str]) -> None:
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", required=True, help="accounts.txt (one handle per line)")
    ap.add_argument("--date", default="yesterday", help="yesterday or YYYY-MM-DD (JST)")
    ap.add_argument("--cache", default="user_cache.json")
    ap.add_argument("--max", type=int, default=20, help="max_results per user (1-100)")
    ap.add_argument("--include-replies", action="store_true")
    ap.add_argument("--include-rts", action="store_true")
    ap.add_argument("--api-base", default=DEFAULT_API_BASE, help="e.g., https://api.twitter.com/2")
    args = ap.parse_args()

    bearer = os.getenv("X_BEARER_TOKEN")
    if not bearer:
        print("ERROR: set env X_BEARER_TOKEN", file=sys.stderr)
        sys.exit(1)

    api_base = args.api_base.rstrip("/")

    now = datetime.now(tz=JST)
    if args.date == "yesterday":
        start, end = yesterday_range_jst(now)
    else:
        d = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=JST)
        start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

    exclude = []
    if not args.include_rts:
        exclude.append("retweets")
    if not args.include_replies:
        exclude.append("replies")
    exclude_str = ",".join(exclude)

    client = XClient(bearer, api_base=api_base)
    cache_path = Path(args.cache)
    cache = load_cache(cache_path)

    handles = read_accounts(Path(args.accounts))

    day = start.strftime("%Y-%m-%d")
    print(f"# {day} (JST)\n")

    changed = False
    total = 0

    for h in handles:
        if h not in cache:
            try:
                cache[h] = client.get_user_id(h)
                changed = True
            except Exception as e:
                print(f"## @{h}\n- (failed to resolve user id) {e}\n")
                continue

        user_id = cache[h]
        try:
            tweets = client.get_tweets(user_id, start, end, max_results=args.max, exclude=exclude_str)
        except Exception as e:
            print(f"## @{h}\n- (failed to fetch tweets) {e}\n")
            continue

        print(f"## @{h}")
        if not tweets:
            print("- (no tweets in range)\n")
            continue

        tweets_sorted = sorted(
            tweets,
            key=lambda t: dtparser.isoparse(t["created_at"]).astimezone(JST),
        )

        for t in tweets_sorted:
            created = dtparser.isoparse(t["created_at"]).astimezone(JST)
            hhmm = created.strftime("%H:%M")
            text = " ".join((t.get("text") or "").split())
            url = f"https://x.com/{h}/status/{t['id']}"
            print(f"- {hhmm} {text} {url}")
            total += 1
        print()

    if changed:
        save_cache(cache_path, cache)

    print(f"(total tweets: {total})", file=sys.stderr)


if __name__ == "__main__":
    main()

