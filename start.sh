#!/usr/bin/env bash
set -euo pipefail

# Create profile on persistent disk if available
mkdir -p /var/data/profiles || true

# Only create if it doesn't already exist (so you don't overwrite edits)
if [ ! -f /var/data/profiles/almir.json ]; then
  cat > /var/data/profiles/almir.json <<'EOF'
{
  "agent_name": "Almir Bajric",
  "brokerage": "Compass Real Estate",
  "default_fee": "2.5%",
  "default_retainer": "0",
  "default_dual_agency": "no"
}
EOF
fi

# Start the web server
exec uvicorn app:app --host 0.0.0.0 --port "${PORT}" --no-access-log
