#!/bin/sh
set -eu

if [ -x .venv/bin/python ]; then
  exec .venv/bin/python "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$@"
fi
exec python3 "$@"
