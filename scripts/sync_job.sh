#!/usr/bin/env bash
# Sync description file into jobs.json and regenerate feed + active project
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.

python3 -m src.main post-project

echo "Job synced to feed."
