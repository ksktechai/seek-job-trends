#!/usr/bin/env python3
"""Emit a markdown market digest from seek.db — designed to be piped straight
into a Telegram message by the OpenClaw agent.

Usage:
  python trends.py            # last 7 days vs previous 7
  python trends.py --days 30  # last 30 vs previous 30
"""

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("SEEK_DB", os.path.expanduser("/home/taihoro/.openclaw/workspace/jobmarket/seek.db"))


def window(conn, start, end):
    return conn.execute(
        "SELECT * FROM jobs WHERE first_seen >= ? AND first_seen < ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=5, help="top matches to list")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    cur = window(conn, now - timedelta(days=args.days), now)
    prev = window(conn, now - timedelta(days=2 * args.days), now - timedelta(days=args.days))

    def pct(a, b):
        return "n/a" if not b else f"{(a - b) / b * +100:+.0f}%"

    contract = sum(1 for r in cur if r["work_type"] == "contract")
    ai = sum(1 for r in cur if r["ai_related"])
    skills = Counter()
    companies = Counter()
    for r in cur:
        companies[r["company"] or "?"] += 1
        for s in json.loads(r["skills_json"] or "[]"):
            skills[s] += 1

    lines = [
        f"# Seek market radar — last {args.days} days",
        "",
        f"New listings: **{len(cur)}** ({pct(len(cur), len(prev))} vs prior {args.days}d)",
        f"Contract share: **{contract}/{len(cur)}** · AI-related: **{ai}**",
        "",
        "**Top skills in demand:** " + ", ".join(f"{s} ({n})" for s, n in skills.most_common(8)),
        "**Most active companies:** " + ", ".join(f"{c} ({n})" for c, n in companies.most_common(5)),
        "",
        f"**Top {args.top} matches:**",
    ]
    top = conn.execute(
        "SELECT * FROM jobs WHERE first_seen >= ? ORDER BY score DESC LIMIT ?",
        ((now - timedelta(days=args.days)).isoformat(), args.top),
    ).fetchall()
    for r in top:
        sal = f" · {r['salary_text']}" if r["salary_text"] else ""
        lines.append(f"- [{r['score']}] {r['title']} — {r['company']} ({r['location']}){sal}\n  {r['url']}\n  _{r['notes']}_")

    print("\n".join(lines))


if __name__ == "__main__":
    main()