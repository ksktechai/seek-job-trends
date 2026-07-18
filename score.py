#!/usr/bin/env python3
"""Score unscored jobs against profile.md via any OpenAI-compatible LLM endpoint.

For each row with score IS NULL, sends title/company/location/salary to the
local model and stores: score (0-100), work_type, seniority, skills, ai_related,
notes. Trend analysis only — no application drafting.

Env vars: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, SEEK_DB (see .env.example)
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

DB_PATH = os.environ.get("SEEK_DB", os.path.expanduser("/home/taihoro/.openclaw/workspace/jobmarket/seek.db"))
# Any OpenAI-compatible endpoint works: Ollama (default) or NVIDIA NIM etc.
#   Ollama:  LLM_BASE_URL=http://192.168.1.15:11434/v1  LLM_API_KEY=ollama
#   NVIDIA:  LLM_BASE_URL=https://integrate.api.nvidia.com/v1  LLM_API_KEY=$NVIDIA_API_KEY
BASE_URL = os.environ.get("LLM_BASE_URL", "http://192.168.1.15:11434/v1").rstrip("/")
API_KEY = os.environ.get("LLM_API_KEY", "ollama")
MODEL = os.environ.get("LLM_MODEL", "qwen3.6:35b-a3b-mxfp8")
PROFILE = Path(__file__).with_name("profile.md")
BATCH_LIMIT = int(os.environ.get("SCORE_BATCH", "40"))

PROMPT = """You are a job-market analyst. Given my profile and one job listing,
return ONLY a JSON object, no markdown fences, with keys:
  score        integer 0-100, relevance of this job to my profile
  work_type    "contract" | "permanent" | "unknown"
  seniority    "junior" | "intermediate" | "senior" | "lead" | "unknown"
  skills       array of up to 8 lowercase skill/tech keywords from the listing
  ai_related   true if the role involves AI/ML/LLM/agents, else false
  notes        one short sentence on why it is or isn't relevant

MY PROFILE:
{profile}

JOB LISTING:
title: {title}
company: {company}
location: {location}
salary: {salary_text}
alert: {alert_name}
"""


def log(msg: str) -> None:
    print(f">>> [score] {datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def ask_model(prompt: str) -> dict:
    r = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.1,
        },
        timeout=180,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    return json.loads(content)


def main() -> None:
    profile = PROFILE.read_text() if PROFILE.exists() else "Senior software engineer."
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM jobs WHERE score IS NULL ORDER BY first_seen LIMIT ?", (BATCH_LIMIT,)
    ).fetchall()
    if not rows:
        log("nothing to score")
        return

    ok = err = 0
    for row in rows:
        try:
            result = ask_model(PROMPT.format(profile=profile, **dict(row)))
            conn.execute(
                """UPDATE jobs SET score=?, work_type=?, seniority=?, skills_json=?,
                   ai_related=?, notes=?, scored_at=? WHERE job_id=?""",
                (
                    int(result.get("score", 0)),
                    result.get("work_type", "unknown"),
                    result.get("seniority", "unknown"),
                    json.dumps(result.get("skills", [])),
                    1 if result.get("ai_related") else 0,
                    result.get("notes", ""),
                    datetime.now(timezone.utc).isoformat(),
                    row["job_id"],
                ),
            )
            conn.commit()
            ok += 1
        except Exception as e:  # keep going; retry next run
            err += 1
            log(f"failed job {row['job_id']}: {e}")
    log(f"<<< scored={ok} errors={err}")


if __name__ == "__main__":
    sys.exit(main())