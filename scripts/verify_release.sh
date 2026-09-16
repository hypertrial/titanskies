#!/bin/sh
set -eu

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

npm run audit:prod
npm run check:public
command -v gitleaks >/dev/null 2>&1 || { echo "gitleaks is required" >&2; exit 1; }
gitleaks git --redact --log-opts="--all"
npm run lint
npm run typecheck
npm test
./scripts/run_python.sh -m mypy
./scripts/run_python.sh -m pytest tests/python -q
./scripts/run_python.sh scripts/generate_demo.py

generated_status=$(git status --short -- public/demo public/geo shared/smoke-coverage-mask-v1.png shared/smoke-coverage-mask-v2.png shared/smoke-coverage-mask-v3.png)
if [ -n "$generated_status" ]; then
  echo "Generated assets are not current:" >&2
  echo "$generated_status" >&2
  exit 1
fi

./scripts/run_python.sh scripts/benchmark_context.py
./scripts/run_python.sh scripts/license_report.py
./scripts/run_python.sh scripts/generate_sbom.py artifacts/sbom.cdx.json
NEXT_PUBLIC_CONTEXT_URL=/demo/context/latest.json \
NEXT_PUBLIC_PERF_DIAGNOSTICS=1 \
npm run build
npm run benchmark:frontend:run
npm run test:e2e
./scripts/verify-container
