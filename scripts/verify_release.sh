#!/bin/sh
set -eu

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$(dirname "$0")/.."

./scripts/verify-checks
./scripts/verify-demo
./scripts/verify-ingest-artifacts
./scripts/verify-frontend
./scripts/verify-container-smokes
