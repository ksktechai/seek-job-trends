# seek-job-trends

Job-market trend radar for [OpenClaw](https://openclaw.ai). No auto-apply — signal only.

Watches Seek (NZ/AU) job listings via official email alerts, scores each
listing against a profile with a local LLM, and delivers a daily trend digest
to Telegram: which skills are rising, which companies are hiring, what
contract rates are doing.

**Pipeline:** Seek email alerts → Gmail (IMAP) → SQLite → any
OpenAI-compatible LLM for scoring → daily Telegram digest via OpenClaw.

```
ingest.py   parse unseen Seek alert emails → upsert into seek.db
score.py    score/tag unscored rows via LLM (profile.md as context)
trends.py   aggregate → markdown digest (stdout)
run.sh      env wrapper: ingest + score (called by OpenClaw heartbeat)
digest.sh   env wrapper: trends (called by OpenClaw cron)
```

Why email alerts instead of scraping: Seek has no public job-seeker API, and
its internal endpoints break without warning. Saved-search alerts are an
official push channel that never does.

## 1. Gmail setup (once)

1. Create/choose a dedicated Gmail account.
2. Google Account → Security → enable **2-Step Verification** (required first).
3. Go directly to https://myaccount.google.com/apppasswords (not linked from
   the Security menu) → create one named `seek-job-trends`. Copy the 16-char
   password **without the display spaces**.
4. IMAP is enabled by default on personal Gmail accounts; nothing to toggle.

If the App Passwords page says the setting is unavailable: confirm 2SV is
fully on, and turn off "Skip password when possible" under Security → How
you sign in to Google.

## 2. Seek alerts (once)

On seek.co.nz / seek.com.au, create saved searches and set email alerts to
**daily**, sent to the dedicated Gmail. Suggested split — separate themes,
not just locations, since each alert's subject becomes `alert_name` in the
DB and acts as a segmentation axis:

- "java developer" <city> · "ai engineer" <city> · "java contract" <city>
- "machine learning engineer" remote AU

## 3. Install

```bash
mkdir -p ~/.openclaw/workspace/jobmarket
cd ~/.openclaw/workspace/jobmarket
# copy the repo files here, then:
uv init --bare && uv add beautifulsoup4 requests
cp env.example .env    # fill in Gmail credentials + scorer endpoint
chmod +x run.sh digest.sh
```

The scorer accepts any OpenAI-compatible endpoint via three env vars —
local Ollama by default, or a hosted API (e.g. NVIDIA) by switching them:

```bash
LLM_BASE_URL=http://<ollama-host>:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=<model>
```

Verify manually:

```bash
set -a; source .env; set +a    # plain `source` does not export — always use set -a
uv run python ingest.py        # expect: done. new=0 ... on an empty inbox
uv run python score.py
uv run python trends.py
```

Note: after editing `.env`, re-source it (and `unset` removed variables) —
the shell keeps old exported values.

## 4. Wiring into OpenClaw

Daemon-launched processes do not inherit an interactive shell's PATH or
environment, which is why the wrapper scripts exist: they `cd` to the
workspace, source `.env`, and call `uv` by absolute path. Point OpenClaw at
the wrappers, never at the Python files directly.

**Heartbeat** (`HEARTBEAT.md`) — ingest and score continuously, stay quiet:

```markdown
- Every heartbeat: run `bash /home/<user>/.openclaw/workspace/jobmarket/run.sh`
  with the exec tool (timeout 300). It ingests new Seek job alerts and scores
  them; the script handles its own env. Do not message me about routine runs
  or errors unless the same error repeats 3+ heartbeats in a row. If any job
  scores 90+, send it (title, company, score, URL, notes) to the Job Radar
  topic in Telegram immediately.
```

**Daily digest** — a cron job delivering to a Telegram forum topic:

```bash
openclaw cron add \
  --name "Daily Seek Job Listings Digest" \
  --cron "0 7 * * *" --tz "Pacific/Auckland" \
  --session isolated \
  --model <your-model> --timeout 300 \
  --message "This task is about JOB LISTINGS from Seek (employment ads), not financial markets. Do exactly this and nothing else:
1. Run with exec (timeout 120): bash /home/<user>/.openclaw/workspace/jobmarket/digest.sh
2. Send the script's output as the message, unchanged apart from Telegram-friendly formatting (no markdown tables), plus at most one sentence of your own observation about the job-listing numbers. If the digest shows zero or very few listings, state that plainly without inventing trends or giving advice. Only compare to prior weeks if prior-week numbers are non-zero.
If the script fails or outputs nothing, report the exact error instead.
Do not run any market, crypto, stock, or news scans. Do not use web_search, web_fetch, or any tool other than exec/process." \
  --announce --channel telegram \
  --to "-100<group-id>:topic:<topic-id>"
```

Then restrict the job's tools to `exec` and `process`. Current OpenClaw CLI
builds expose no flag for `toolsAllow`; set it in the job payload directly
(edit the cron jobs store, or ask the agent to update the job via its cron
tool) and verify with `openclaw cron show <job-id>`.

Weekly long view: duplicate the job with `--cron "0 8 * * 0"` calling
`digest.sh --days 30 --top 10`.

**Why the prompt is this defensive:** the first version of this job — loosely
worded, unrestricted tools, named "Market Radar" — was reinterpreted by the
agent as a crypto/stocks scanner on its first scheduled run. Scheduled agent
jobs work best as couriers, not analysts: locked tool list, one command, send
the script's output. The zero-listings clause exists because the model
otherwise invents trends ("second straight week of no listings") on a
day-old database.

## 5. Notes

- Reruns are safe: dedupe on `job_id`; scoring only touches `score IS NULL`
  rows, and failures retry next run.
- Seek changes its email template occasionally. The parser anchors on job
  URLs (stable) and heuristics for company/location/salary — if fields come
  through empty, tweak `parse_jobs()` in `ingest.py`.
- The alert email only carries title/company/location/salary. That's enough
  for trend analysis. For full JD text on top matches, add a fetch step for
  90+ scorers only (keeps traffic polite).
- Trend history compounds: after 4-6 weeks, `seek.db` supports real
  time-series (skills demand, contract share, salary drift).
- Do not commit `.env` (credentials) or `seek.db` (data); see `.gitignore`.