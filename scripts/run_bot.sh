#!/usr/bin/env bash
# Start the full freelancermap bot (feed server + chat loop)
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.

python3 -m src.main feed-generate
echo "Starting feed server on port 8080 and chat loop..."
python3 -m src.main feed-serve &
FEED_PID=$!
trap "kill $FEED_PID 2>/dev/null" EXIT
python3 -m src.main chat-loop
