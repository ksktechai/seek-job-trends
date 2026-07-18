#!/usr/bin/env python3
"""Ingest Seek job-alert emails from Gmail (IMAP) into SQLite.

Reads UNSEEN messages from the Seek alert sender, extracts job cards
(job id, title, company, location, salary text, url), and upserts them
into seek.db. Safe to run repeatedly — dedupes on job_id.

Env vars (see .env.example):
  GMAIL_USER, GMAIL_APP_PASSWORD, SEEK_DB (optional)
"""

import email
import email.message
import imaplib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from email.header import decode_header

from bs4 import BeautifulSoup

IMAP_HOST = "imap.gmail.com"
SEEK_SENDERS = ["noreply@s.seek.co.nz", "noreply@s.seek.com.au", "seek.co.nz", "seek.com.au"]
DB_PATH = os.environ.get("SEEK_DB", os.path.expanduser("~/.openclaw/workspace/jobmarket/seek.db"))

JOB_URL_RE = re.compile(r"https?://www\.seek\.(?:co\.nz|com\.au)/job/(\d+)[^\s\"'<>]*")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    source      TEXT,              -- seek.co.nz | seek.com.au
    title       TEXT,
    company     TEXT,
    location    TEXT,
    salary_text TEXT,
    url         TEXT,
    alert_name  TEXT,              -- which saved search produced it
    first_seen  TEXT,              -- ISO timestamp
    -- filled in by score.py:
    score       INTEGER,
    work_type   TEXT,              -- contract | permanent | unknown
    seniority   TEXT,
    skills_json TEXT,
    ai_related  INTEGER,
    notes       TEXT,
    scored_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
"""


def log(msg: str) -> None:
    print(f">>> [ingest] {datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def decode_subject(raw) -> str:
    parts = decode_header(raw or "")
    out = ""
    for text, enc in parts:
        out += text.decode(enc or "utf-8", "replace") if isinstance(text, bytes) else text
    return out


def html_body(msg: email.message.Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def parse_jobs(html: str, alert_name: str) -> list[dict]:
    """Extract job cards from a Seek alert email. Seek's template changes
    occasionally, so this anchors on job URLs (stable) and walks up for text."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        m = JOB_URL_RE.search(a["href"])
        if not m:
            continue
        job_id = m.group(1)
        source = "seek.co.nz" if ".co.nz" in m.group(0) else "seek.com.au"
        entry = jobs.setdefault(job_id, {
            "job_id": job_id,
            "source": source,
            "url": f"https://www.{source}/job/{job_id}",
            "title": "", "company": "", "location": "", "salary_text": "",
            "alert_name": alert_name,
        })
        link_text = a.get_text(" ", strip=True)
        if link_text and len(link_text) > len(entry["title"]):
            entry["title"] = link_text
        # Company/location/salary usually live in sibling text of the card
        card = a.find_parent(["td", "div", "table"])
        if card is not None:
            lines = [t for t in card.get_text("\n", strip=True).split("\n") if t]
            for i, line in enumerate(lines):
                if line == entry["title"] and i + 1 < len(lines):
                    entry["company"] = entry["company"] or lines[i + 1]
                if re.search(r"\$\s?\d", line):
                    entry["salary_text"] = entry["salary_text"] or line
                if re.search(r"(Auckland|Wellington|Christchurch|Hamilton|Sydney|Melbourne|Brisbane|Remote|Hybrid)", line, re.I):
                    entry["location"] = entry["location"] or line
    return list(jobs.values())


def upsert(conn: sqlite3.Connection, jobs: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    for j in jobs:
        cur = conn.execute(
            """INSERT INTO jobs (job_id, source, title, company, location, salary_text, url, alert_name, first_seen)
               VALUES (:job_id, :source, :title, :company, :location, :salary_text, :url, :alert_name, :first_seen)
               ON CONFLICT(job_id) DO NOTHING""",
            {**j, "first_seen": now},
        )
        new += cur.rowcount
    conn.commit()
    return new


def main() -> None:
    user = os.environ["GMAIL_USER"]
    pw = os.environ["GMAIL_APP_PASSWORD"]

    conn = get_db()
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(user, pw)
    imap.select("INBOX")

    total_new = 0
    for sender in SEEK_SENDERS:
        status, data = imap.search(None, f'(UNSEEN FROM "{sender}")')
        if status != "OK" or not data or not data[0]:
            continue
        for num in data[0].split():
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            alert_name = decode_subject(msg.get("Subject"))
            jobs = parse_jobs(html_body(msg), alert_name)
            n = upsert(conn, jobs)
            total_new += n
            log(f"parsed {len(jobs)} jobs ({n} new) from: {alert_name!r}")
            imap.store(num, "+FLAGS", "\\Seen")

    imap.logout()
    unscored = conn.execute("SELECT COUNT(*) FROM jobs WHERE score IS NULL").fetchone()[0]
    log(f"done. new={total_new}, awaiting scoring={unscored}")
    print(json.dumps({"new": total_new, "unscored": unscored}))


if __name__ == "__main__":
    sys.exit(main())
