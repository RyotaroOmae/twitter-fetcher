from __future__ import annotations

import time

import requests

DISCORD_MAX = 1900
_LINE_MAX = DISCORD_MAX - 100  # headroom for chunk header


def _post_message(content: str, webhook_url: str) -> bool:
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                webhook_url,
                json={"content": content, "allowed_mentions": {"parse": []}},
                timeout=10,
            )
            if resp.status_code == 429:
                try:
                    retry_after = float(resp.json().get("retry_after", 5))
                except (ValueError, KeyError):
                    retry_after = 5.0
                time.sleep(retry_after + 0.5)
                continue
            resp.raise_for_status()
            time.sleep(1)
            return True
        except requests.RequestException as e:
            last_exc = e
            print(f"[discord] post error (attempt {attempt + 1}/3): {e}")
    print(f"[discord] gave up after 3 attempts: {last_exc}")
    return False


def post_section(
    handle: str,
    lines: list[str],
    date_str: str,
    webhook_url: str,
    dry_run: bool = False,
) -> int:
    """Post one account's tweet lines to Discord.

    Returns the number of lines successfully posted. On partial chunk failure,
    returns the count of lines already posted so the caller can mark only those
    tweets as seen and retry the rest next run.
    """
    header = f"**@{handle}** — {date_str}"
    cont_header = f"**@{handle}** (cont.) — {date_str}"

    # Truncate any single line that exceeds the per-line budget.
    safe_lines = [
        line if len(line) <= _LINE_MAX else line[:_LINE_MAX - 3] + "..."
        for line in lines
    ]

    # Build chunks, tracking the number of source lines each contains.
    chunks: list[tuple[str, int]] = []  # (message_content, line_count)
    current_header = header
    current_lines: list[str] = []
    current_len = len(current_header) + 1  # +1 for newline

    for line in safe_lines:
        needed = len(line) + 1  # +1 for newline
        if current_lines and current_len + needed > DISCORD_MAX:
            chunks.append((current_header + "\n" + "\n".join(current_lines), len(current_lines)))
            current_header = cont_header
            current_lines = []
            current_len = len(current_header) + 1
        current_lines.append(line)
        current_len += needed

    if current_lines:
        chunks.append((current_header + "\n" + "\n".join(current_lines), len(current_lines)))

    if not chunks:
        return 0

    if dry_run:
        for content, _ in chunks:
            print(f"[dry-run] would post:\n{content}\n")
        return len(lines)

    posted = 0
    for content, line_count in chunks:
        if not _post_message(content, webhook_url):
            return posted
        posted += line_count
    return posted
