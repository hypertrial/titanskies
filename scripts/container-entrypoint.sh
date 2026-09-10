#!/bin/sh
set -eu

case "${1:-web}" in
  web) exec node scripts/run-web.mjs ;;
  ingest) exec .venv/bin/python scripts/watch_context.py ;;
  ingest-once) exec .venv/bin/python scripts/watch_context.py --once ;;
  *) exec "$@" ;;
esac
