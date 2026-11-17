#!/usr/bin/env bash
# GTA 6 video factory - install (or repair) EVERYTHING with one command, then start it.
#
#   bash install.sh               install what is missing, then open the dashboard
#   bash install.sh --no-start    install only
#   bash install.sh --no-footage  skip downloading the official trailers (GTA 6, and Red Dead 2 / GTA V for comparisons)
#
# Safe to run again at any time: what is already installed is kept, only what is
# missing or broken is (re)installed. macOS (Homebrew) or Linux (apt). Free tools only.
set -euo pipefail
cd "$(dirname "$0")"

START=1
FOOTAGE=1
for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    --no-footage) FOOTAGE=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  esac
done

say()  { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '   \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

brew_env() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}

# ------------------------------------------------------------------ 1. system tools
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  say "Homebrew (the macOS package manager)"
  command -v brew >/dev/null 2>&1 || brew_env || true
  if ! command -v brew >/dev/null 2>&1; then
    echo "   Homebrew is not installed: installing it now (it asks for your Mac password once)."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew_env || die "Homebrew did not install. Install it from https://brew.sh then run: bash install.sh"
  fi
  ok "$(brew --version | head -1)"

  say "Tools: ffmpeg, Node.js, Ollama, uv"
  for pkg in ffmpeg node ollama uv; do
    if brew list --versions "$pkg" >/dev/null 2>&1; then ok "$pkg"; else echo "   installing $pkg ..."; brew install "$pkg"; ok "$pkg"; fi
  done
  # keep the local AI server running in the background (also after a restart of the Mac)
  brew services start ollama >/dev/null 2>&1 || true
elif [ "$OS" = "Linux" ]; then
  say "Tools: ffmpeg, Node.js, Ollama, uv"
  missing=0
  for b in ffmpeg ffprobe node npm curl git; do command -v "$b" >/dev/null 2>&1 || missing=1; done
  if [ "$missing" = 1 ]; then
    command -v apt-get >/dev/null 2>&1 || die "Install ffmpeg, Node.js 18+, npm, curl and git with your package manager, then run: bash install.sh"
    sudo apt-get update && sudo apt-get install -y ffmpeg nodejs npm curl git
  fi
  ok "ffmpeg, node $(node --version)"
  if ! command -v ollama >/dev/null 2>&1; then
    (curl -fsSL https://ollama.com/install.sh | sh) || warn "Ollama could not be installed now (the AI steps need it): https://ollama.com/download"
  fi
  command -v ollama >/dev/null 2>&1 && ok "ollama"
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    (curl -LsSf https://astral.sh/uv/install.sh | sh) || python3 -m pip install --user -q uv || die "uv could not be installed: https://docs.astral.sh/uv/"
  fi
  ok "uv"
else
  die "Unsupported system: $OS. Use macOS or Linux (on Windows: WSL)."
fi

# ------------------------------------------------------------------ 2. Python environment
# uv makes the .venv (and downloads Python 3.12 itself if needed): no pip / ensurepip problems.
say "Python environment (.venv, Python 3.12)"
if ! .venv/bin/python -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' >/dev/null 2>&1; then
  [ -e .venv ] && warn "the old .venv is broken or uses an unsupported Python: making a new one"
  rm -rf .venv
  uv venv --python 3.12 .venv || uv venv --python 3.13 .venv || uv venv --python 3.11 .venv || die "could not make the Python environment (uv venv)"
fi
uv pip install --python .venv/bin/python -q -e ".[align,footage,dev]"
ok "$(.venv/bin/python --version) with the project installed"
PY=.venv/bin/python

# ------------------------------------------------------------------ 3. renderer, models, music
say "Video renderer (Remotion)"
if [ ! -d node_modules/@remotion/renderer ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  npm install --no-audit --no-fund
fi
ok "Remotion"

say "AI models, voice, music and sound effects (big downloads the first time only)"
$PY -m gta_factory setup || warn "setup reported a problem (see above). Fix it and run: bash install.sh"

# ------------------------------------------------------------------ 4. real GTA 6 footage
if [ "$FOOTAGE" = 1 ]; then
  say "Real GTA 6 footage (official trailers + any new one, tagged automatically)"
  $PY -m gta_factory footage update || warn "the footage could not be downloaded now; run later: $PY -m gta_factory footage update"
  say "Footage of the games GTA 6 is compared with (official Red Dead 2 and GTA V trailers)"
  for game in rdr2 gta5; do
    $PY -m gta_factory footage update --game "$game" --no-new || warn "the $game footage could not be downloaded now; run later: $PY -m gta_factory footage update --game $game"
  done
fi

# ------------------------------------------------------------------ 5. check, start
say "Check"
$PY -m gta_factory doctor || warn "fix the ✗ lines above, then run: bash install.sh"

if [ "$START" = 1 ]; then
  say "Starting the factory"
  exec bash ./start.sh --no-install
fi
printf '\nDone. Start the factory any time with:  bash start.sh\n'
