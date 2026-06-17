#!/usr/bin/env python3
from __future__ import annotations

import os, sys, json, argparse, textwrap, re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from dateutil import parser as dtparser
from PIL import Image, ImageDraw, ImageFont

JST = timezone(timedelta(hours=9))
DEFAULT_API_BASE = "https://api.x.com/2"  # ダメなら --api-base https://api.twitter.com/2

# ---- basic utils ----

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
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

# ---- font ----

def load_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_path:
        return ImageFont.truetype(font_path, size=size)
    # Linuxで日本語が出やすい候補（無ければデフォルト）
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size=size)
        except Exception:
            pass
    return ImageFont.load_default()

# ---- X API client ----

class XClient:
    def __init__(self, bearer: str, api_base: str):
        self.api_base = api_base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {bearer}"})
        self.img = requests.Session()
        self.img.headers.update({"User-Agent": "xsum-api/1.0"})

    def get_user_id(self, username: str) -> str:
        url = f"{self.api_base}/users/by/username/{username}"
        r = self.s.get(url, params={"user.fields": "id"}, timeout=30)
        r.raise_for_status()
        return r.json()["data"]["id"]

    def get_tweets_with_media(
        self,
        username: str,
        user_id: str,
        start: datetime,
        end: datetime,
        max_results: int = 20,
        exclude: str = "retweets,replies",
    ) -> Tuple[List[dict], Dict[str, dict]]:
        """
        Returns (tweets, media_by_key)
        """
        url = f"{self.api_base}/users/{user_id}/tweets"
        params = {
            "start_time": iso_z(start),
            "end_time": iso_z(end),
            "max_results": str(max_results),
            "tweet.fields": "created_at,text,attachments",
            "expansions": "attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url,alt_text",
        }
        if exclude:
            params["exclude"] = exclude

        r = self.s.get(url, params=params, timeout=30)
        r.raise_for_status()
        j = r.json()

        tweets = j.get("data", [])
        media_by_key: Dict[str, dict] = {}
        inc = j.get("includes", {}) or {}
        media_list = inc.get("media", []) or []
        for m in media_list:
            if "media_key" in m:
                media_by_key[m["media_key"]] = m
        return tweets, media_by_key

    def download_image(self, url: str) -> Optional[Image.Image]:
        try:
            r = self.img.get(url, timeout=30)
            r.raise_for_status()
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            return None

# ---- PNG rendering (tweet text + 0..N images) ----

import io

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    # 単純な文字単位ラップ（日本語でも破綻しにくい）
    lines: List[str] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            lines.append("")
            continue
        cur = ""
        for ch in raw:
            trial = cur + ch
            if draw.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines

def render_tweet_card(
    handle: str,
    created_at: datetime,
    text: str,
    tweet_url: str,
    images: List[Image.Image],
    out_path: Path,
    font_path: Optional[str] = None,
) -> None:
    # layout constants
    W = 1200
    PAD = 36
    GAP = 18
    TEXT_MAX_W = W - PAD * 2

    title_font = load_font(font_path, 26)
    body_font = load_font(font_path, 20)
    meta_font = load_font(font_path, 18)

    dummy = Image.new("RGB", (W, 10), (255, 255, 255))
    d = ImageDraw.Draw(dummy)

    header = f"@{handle}  {created_at.strftime('%Y-%m-%d %H:%M')} (JST)"
    text = text.strip() if text else "(text unavailable)"
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    header_lines = wrap_text(d, header, title_font, TEXT_MAX_W)
    body_lines = wrap_text(d, text, body_font, TEXT_MAX_W)
    url_lines = wrap_text(d, tweet_url, meta_font, TEXT_MAX_W)

    def line_h(font: ImageFont.ImageFont) -> int:
        return int(getattr(font, "size", 20) * 1.5)

    header_h = line_h(title_font) * max(1, len(header_lines))
    body_h = line_h(body_font) * max(1, len(body_lines))
    url_h = line_h(meta_font) * max(1, len(url_lines))

    # image block sizing (max 1 row, up to 2 images)
    # 0 images: nothing
    # 1 image: fit to width
    # 2 images: side-by-side
    img_block_h = 0
    rendered_imgs: List[Image.Image] = []

    if images:
        take = images[:2]
        if len(take) == 1:
            im = take[0]
            # fit to width, cap height
            target_w = TEXT_MAX_W
            target_h = int(im.height * (target_w / im.width))
            target_h = min(target_h, 900)
            im2 = im.resize((target_w, target_h))
            rendered_imgs = [im2]
            img_block_h = target_h
        else:
            # two images
            target_w_each = (TEXT_MAX_W - GAP) // 2
            ims2 = []
            max_h = 0
            for im in take:
                target_h = int(im.height * (target_w_each / im.width))
                target_h = min(target_h, 700)
                im2 = im.resize((target_w_each, target_h))
                ims2.append(im2)
                max_h = max(max_h, target_h)
            # pad to same height
            padded = []
            for im2 in ims2:
                if im2.height < max_h:
                    bg = Image.new("RGB", (im2.width, max_h), (245, 245, 245))
                    bg.paste(im2, (0, (max_h - im2.height)//2))
                    padded.append(bg)
                else:
                    padded.append(im2)
            rendered_imgs = padded
            img_block_h = max_h

    # total height
    H = PAD + header_h + GAP + body_h + GAP + url_h
    if img_block_h:
        H += GAP + img_block_h
    H += PAD

    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    y = PAD

    # header
    for ln in header_lines:
        draw.text((PAD, y), ln, font=title_font, fill=(0, 0, 0))
        y += line_h(title_font)

    y += GAP

    # body
    for ln in body_lines:
        draw.text((PAD, y), ln, font=body_font, fill=(0, 0, 0))
        y += line_h(body_font)

    # images
    if rendered_imgs:
        y += GAP
        if len(rendered_imgs) == 1:
            img.paste(rendered_imgs[0], (PAD, y))
        else:
            img.paste(rendered_imgs[0], (PAD, y))
            img.paste(rendered_imgs[1], (PAD + rendered_imgs[0].width + GAP, y))
        y += img_block_h

    y += GAP

    # url
    for ln in url_lines:
        draw.text((PAD, y), ln, font=meta_font, fill=(60, 60, 60))
        y += line_h(meta_font)

    img.save(out_path)

# ---- cache ----

def load_cache(cache_path: Path) -> Dict[str, str]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}

def save_cache(cache_path: Path, cache: Dict[str, str]) -> None:
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

# ---- main ----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", required=True, help="accounts.txt (one handle per line)")
    ap.add_argument("--date", default="yesterday", help="yesterday or YYYY-MM-DD (JST)")
    ap.add_argument("--cache", default="user_cache.json")
    ap.add_argument("--max", type=int, default=20, help="max_results per user (1-100)")
    ap.add_argument("--include-replies", action="store_true")
    ap.add_argument("--include-rts", action="store_true")
    ap.add_argument("--api-base", default=DEFAULT_API_BASE, help="e.g., https://api.twitter.com/2")
    ap.add_argument("--outdir", default="out_cards", help="output dir for per-tweet PNGs")
    ap.add_argument("--font", default=None, help="path to Japanese-capable font .ttf/.ttc")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    bearer = os.getenv("X_BEARER_TOKEN")
    if not bearer:
        print("ERROR: set env X_BEARER_TOKEN", file=sys.stderr)
        sys.exit(1)

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

    client = XClient(bearer, api_base=args.api_base)
    outdir = Path(args.outdir)
    ensure_dir(outdir)

    cache_path = Path(args.cache)
    cache = load_cache(cache_path)
    changed = False

    handles = read_accounts(Path(args.accounts))

    day = start.strftime("%Y-%m-%d")
    print(f"# {day} (JST)\n")

    total_cards = 0

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
            tweets, media_by_key = client.get_tweets_with_media(
                username=h,
                user_id=user_id,
                start=start,
                end=end,
                max_results=args.max,
                exclude=exclude_str,
            )
        except Exception as e:
            print(f"## @{h}\n- (failed to fetch tweets) {e}\n")
            continue

        print(f"## @{h}")
        if not tweets:
            print("- (no tweets in range)\n")
            continue

        tweets_sorted = sorted(tweets, key=lambda t: dtparser.isoparse(t["created_at"]).astimezone(JST))

        for t in tweets_sorted:
            created = dtparser.isoparse(t["created_at"]).astimezone(JST)
            hhmm = created.strftime("%H:%M")
            text = t.get("text") or ""
            tweet_id = t["id"]
            url = f"https://x.com/{h}/status/{tweet_id}"

            # collect media URLs from attachments.media_keys
            imgs: List[Image.Image] = []
            att = t.get("attachments") or {}
            keys = att.get("media_keys") or []
            for k in keys:
                m = media_by_key.get(k)
                if not m:
                    continue
                # photo: url, video/animated_gif: preview_image_url
                u = m.get("url") or m.get("preview_image_url")
                if not u:
                    continue
                # download
                try:
                    r = client.img.get(u, timeout=30)
                    r.raise_for_status()
                    im = Image.open(io.BytesIO(r.content)).convert("RGB")
                    imgs.append(im)
                except Exception:
                    if args.debug:
                        print(f"[debug] failed to download media: {u}", file=sys.stderr)

            # render card PNG per tweet
            safe_handle = re.sub(r"[^A-Za-z0-9_]+", "_", h)
            out_path = outdir / f"{safe_handle}_{created.strftime('%Y%m%d_%H%M')}_{tweet_id}.png"
            render_tweet_card(
                handle=h,
                created_at=created,
                text=text,
                tweet_url=url,
                images=imgs,
                out_path=out_path,
                font_path=args.font,
            )

            print(f"- {hhmm} {normalize_ws(text)} {url}  -> {out_path}")
            total_cards += 1

        print()

    if changed:
        save_cache(cache_path, cache)

    print(f"(cards generated: {total_cards})", file=sys.stderr)


if __name__ == "__main__":
    main()

