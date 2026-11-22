#!/bin/bash
# Double-click this file in Finder (or run ./run_mac.command) to start BigSheet.
# The first run creates a private Python environment in .venv and installs dependencies.
cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/macos/"
  read -r -p "Press Enter to close."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: setting up (this takes a minute)..."
  "$PY" -m venv .venv || exit 1
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/python -m pip install -r requirements.txt || { read -r -p "Install failed. Press Enter."; exit 1; }
fi
exec ./.venv/bin/python main.py "$@"
