# Contributing

Use Node.js 22, Python 3.12, and `uv`. Install with `npm ci` and `uv sync --all-extras`, then run `./scripts/verify-fast` before opening a pull request.

Do not commit live data, credentials, generated local state, proprietary assets, or a new data source without its machine-readable registry entry and documented terms. Preserve the v8 forecast invariants in [`AGENTS.md`](AGENTS.md), add fixture-backed tests for changed behavior, and keep source failures isolated.

By contributing, you agree that your contribution is licensed under the MIT License.
