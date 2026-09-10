# Security policy

Report a suspected vulnerability privately through GitHub's **Security → Report a vulnerability** flow. Do not open a public issue for secrets, path traversal, remote-code execution, or other exploitable findings.

Supported security fixes target the latest tagged release and `main`. TitanSkies binds to `127.0.0.1` by default. If you expose it to a LAN, place it behind an operator-managed TLS reverse proxy, restrict network access, and keep the data directory read-only for the web process.

Never put `AIRNOW_API_KEY` or other credentials in `NEXT_PUBLIC_*` variables, images, logs, or published data. The read-only HTTP application has no ingest or administrative endpoint.
