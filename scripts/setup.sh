#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Installing Python dependencies..."
pip3 install -r requirements.txt

echo "Installing Playwright Chromium..."
playwright install chromium

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — fill in your credentials."
fi

export PYTHONPATH=.
python3 -m src.main feed-generate
python3 -m src.main status

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. PYTHONPATH=. python3 -m src.main discover --headed"
echo "  3. PYTHONPATH=. python3 -m src.main chat-once --headed"
