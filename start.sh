#!/usr/bin/env bash
# GTA 6 video factory - start everything and open the dashboard (http://127.0.0.1:8600).
#
#   bash start.sh              update the code (git), install what is missing, start Ollama + the dashboard
#   NO_UPDATE=1 bash start.sh  same, without updating the code
#
# Close the Terminal window (or Ctrl+C) to stop the dashboard. Jobs already running keep going.
set -uo pipefail
cd "$(dirname "$0")"

for b in /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew; do
  [ -x "$b" ] && eval "$("$b" shellenv)" && break   # double-clicked from Finder: no shell profile
done
export PATH="$HOME/.local/bin:$PATH"

if [ "${1:-}" != "--no-install" ]; then
  # 1. latest version of the code (skipped when there are local edits or no internet)
  if [ "${NO_UPDATE:-0}" != 1 ] && [ -d .git ] && command -v git >/dev/null 2>&1; then
    if git pull --ff-only -q 2>/dev/null; then echo "code: up to date ($(git log -1 --format=%h))"; else echo "code: not updated (no internet or local changes) - continuing"; fi
  fi
  # 2. nothing installed yet, or broken: the full installer
  if ! .venv/bin/python -c 'import gta_factory' >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
    echo "first start (or something is missing): running the installer ..."
    exec bash ./install.sh
  fi
  # 3. new dependencies after an update (fast when nothing changed)
  command -v uv >/dev/null 2>&1 && uv pip install --python .venv/bin/python -q -e ".[align,footage,dev]" || true
  if [ ! -d node_modules/@remotion/renderer ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
    npm install --no-audit --no-fund >/dev/null 2>&1 || echo "npm install failed: run it in this folder to see why"
  fi
fi

# 4. the local AI server
if ! curl -s -o /dev/null --max-time 2 http://127.0.0.1:11434/api/version; then
  if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1 && brew list --versions ollama >/dev/null 2>&1; then
    brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
  elif [ -d /Applications/Ollama.app ]; then
    open -g -a Ollama
  elif command -v ollama >/dev/null 2>&1; then
    nohup ollama serve >/dev/null 2>&1 &
  fi
  for _ in $(seq 1 40); do curl -s -o /dev/null --max-time 1 http://127.0.0.1:11434/api/version && break; sleep 0.5; done
fi
curl -s -o /dev/null --max-time 2 http://127.0.0.1:11434/api/version && echo "ollama: running" || echo "ollama: NOT running (the AI steps will fail) - open the Ollama app or run: ollama serve"

# 5. the dashboard (opens in the browser as soon as it answers)
PORT="$(.venv/bin/python -c 'from gta_factory.app.config.settings import get_settings; print(get_settings().get("dashboard.port", 8600))' 2>/dev/null || echo 8600)"
URL="http://127.0.0.1:${PORT}"
open_url() { (command -v open >/dev/null 2>&1 && open "$URL") || (command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL") || echo "open $URL in your browser"; }
if curl -s -o /dev/null --max-time 2 "$URL/api/jobs"; then
  echo "dashboard: already running"
  open_url
  exit 0
fi
( for _ in $(seq 1 60); do curl -s -o /dev/null --max-time 1 "$URL/api/jobs" && break; sleep 0.5; done; open_url ) &
echo "dashboard: $URL  (close this window or press Ctrl+C to stop it)"
exec .venv/bin/python -m gta_factory dashboard
