# digest.sh — called by the cron job
#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; source .env; set +a
$HOME/.local/bin/uv run python trends.py "$@"