#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from dateutil import parser as dtparser

from xsum_api import (
    DEFAULT_API_BASE,
    JST,
    XClient,
    load_cache,
    read_accounts,
    save_cache,
    yesterday_range_jst,
)
import discord_poster

_SEEN_PATH = Path("seen_tweets.json")


def _load_seen(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top-level is not a dict")
        seen = data.get("seen", {})
        if not isinstance(seen, dict):
            raise ValueError("seen is not a dict")
        return {k: v for k, v in seen.items() if isinstance(k, str) and isinstance(v, str)}
    except (json.JSONDecodeError, ValueError, AttributeError):
        print("[dedup] seen_tweets.json is corrupt, starting fresh", file=sys.stderr)
        return {}


def _save_seen(path: Path, seen: dict[str, str]) -> None:
    data = {"seen": seen, "last_updated": datetime.now(timezone.utc).isoformat()}
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _cleanup_seen(seen: dict[str, str], days: int = 30) -> dict[str, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = {}
    for tweet_id, ts in seen.items():
        try:
            if datetime.fromisoformat(ts) > cutoff:
                result[tweet_id] = ts
        except (ValueError, TypeError):
            pass
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch tweets and post to Discord")
    ap.add_argument("--accounts", default="accounts.txt")
    ap.add_argument("--cache", default="user_cache.json")
    ap.add_argument("--seen", default="seen_tweets.json")
    ap.add_argument("--date", default="yesterday", help="yesterday or YYYY-MM-DD (JST)")
    ap.add_argument("--hours", type=int, default=None, help="fetch tweets from the last N hours (overrides --date)")
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--include-replies", action="store_true")
    ap.add_argument("--include-self-replies", action="store_true",
                    help="include replies to self (threads) but exclude replies to others")
    ap.add_argument("--include-rts", action="store_true")
    ap.add_argument("--api-base", default=DEFAULT_API_BASE)
    ap.add_argument("--output", default=None, help="write markdown summary to this file")
    ap.add_argument("--no-dedup", action="store_true",
                    help="ignore seen_tweets.json and do not update it (for testing)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bearer = os.getenv("X_BEARER_TOKEN")
    if not bearer:
        print("ERROR: X_BEARER_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not args.dry_run and not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(tz=JST)
    if args.hours is not None:
        end = now
        start = now - timedelta(hours=args.hours)
    elif args.date == "yesterday":
        start, end = yesterday_range_jst(now)
    else:
        d = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=JST)
        start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

    date_str = start.strftime("%Y-%m-%d")

    exclude_parts = []
    if not args.include_rts:
        exclude_parts.append("retweets")
    if not args.include_replies and not args.include_self_replies:
        exclude_parts.append("replies")
    exclude_str = ",".join(exclude_parts)

    client = XClient(bearer, api_base=args.api_base)
    cache_path = Path(args.cache)
    user_cache = load_cache(cache_path)
    seen_path = Path(args.seen)
    seen = {} if args.no_dedup else _cleanup_seen(_load_seen(seen_path))

    handles = read_accounts(Path(args.accounts))
    if not handles:
        print("[warn] accounts.txt is empty", file=sys.stderr)
        return

    user_cache_changed = False
    total_new = 0
    summary_sections: list[str] = []

    for handle in handles:
        if handle not in user_cache:
            try:
                user_cache[handle] = client.get_user_id(handle)
                user_cache_changed = True
            except Exception as e:
                print(f"[warn] @{handle}: failed to resolve user id: {e}", file=sys.stderr)
                continue

        user_id = user_cache[handle]

        try:
            tweets = client.get_tweets(
                user_id, start, end, max_results=args.max, exclude=exclude_str
            )
        except Exception as e:
            print(f"[warn] @{handle}: failed to fetch tweets: {e}", file=sys.stderr)
            continue

        tweets_sorted = sorted(
            tweets,
            key=lambda t: dtparser.isoparse(t["created_at"]).astimezone(JST),
        )

        if args.include_self_replies and not args.include_replies:
            tweets_sorted = [
                t for t in tweets_sorted
                if t.get("in_reply_to_user_id") is None
                or t.get("in_reply_to_user_id") == user_id
            ]

        new_tweets = [t for t in tweets_sorted if t["id"] not in seen]

        if not new_tweets:
            print(f"[info] @{handle}: no new tweets", file=sys.stderr)
            continue

        lines = []
        for t in new_tweets:
            created = dtparser.isoparse(t["created_at"]).astimezone(JST)
            hhmm = created.strftime("%H:%M")
            text = " ".join((t.get("text") or "").split())
            url = f"https://x.com/{handle}/status/{t['id']}"
            lines.append(f"- {hhmm} {text} {url}")

        posted_count = discord_poster.post_section(
            handle=handle,
            lines=lines,
            date_str=date_str,
            webhook_url=webhook_url or "",
            dry_run=args.dry_run,
        )

        if posted_count > 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            for t in new_tweets[:posted_count]:
                seen[t["id"]] = now_iso
            total_new += posted_count
            summary_sections.append(f"## @{handle}\n" + "\n".join(lines[:posted_count]))
            if not args.no_dedup:
                _save_seen(seen_path, seen)  # persist per-handle to survive mid-run interruption
            if posted_count < len(new_tweets):
                print(
                    f"[error] @{handle}: only {posted_count}/{len(new_tweets)} tweets posted,"
                    " rest will retry next run",
                    file=sys.stderr,
                )
        else:
            print(f"[error] @{handle}: Discord post failed, NOT marking as seen", file=sys.stderr)

    if user_cache_changed:
        save_cache(cache_path, user_cache)
    if not args.no_dedup:
        _save_seen(seen_path, seen)  # persist cleanup even when no new tweets

    if args.output:
        range_label = f"last {args.hours}h" if args.hours else date_str
        body = "\n\n".join(summary_sections) if summary_sections else "(no new tweets)"
        Path(args.output).write_text(
            f"# {date_str} ({range_label})\n\n{body}\n\n(total: {total_new} new tweets)\n",
            encoding="utf-8",
        )

    print(f"[done] posted {total_new} new tweets from {len(handles)} accounts", file=sys.stderr)


if __name__ == "__main__":
    main()
