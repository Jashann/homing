#!/usr/bin/env bash
# Installs everything, builds the web app and starts Homing in demo mode on http://127.0.0.1:8787
#   ./scripts/demo.sh        (or: make demo)
# Demo mode (HOMING_DEMO=1) accepts the bundled sample documents, which are stamped as samples; the interface then
# shows one "demo documents" banner. Without it, a sample-stamped document always needs a person's review.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "Please install uv first: https://docs.astral.sh/uv/"; exit 1; }
command -v npm >/dev/null || { echo "Please install Node.js 20 or newer (it brings npm): https://nodejs.org/"; exit 1; }
uv sync
(cd web && npm ci && npm run build)
if [ ! -f "$HOME/.paypal_sandbox" ] && [ -z "${PAYPAL_CLIENT_ID:-}" ]; then
  echo "Note: no PayPal sandbox keys found (~/.paypal_sandbox). Searching and checking work; paying does not. See README."
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ] && ! command -v claude >/dev/null; then
  echo "Note: no language model found (claude command-line program or ANTHROPIC_API_KEY). The four sample cases use"
  echo "      saved readings; any other document cannot be read. See README."
fi
export HOMING_DEMO=1
echo "Homing is starting in demo mode on http://127.0.0.1:${HOMING_PORT:-8787}"
exec uv run python -m homing.server
