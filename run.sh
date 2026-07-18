# run.sh — called by heartbeat
#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; source .env; set +a
UV=$HOME/.local/bin/uv   # adjust to `which uv`
$UV run python ingest.py && $UV run python score.py