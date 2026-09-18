# Security policy

Report a suspected vulnerability privately through GitHub's **Security → Report a vulnerability** flow. Do not open a public issue for secrets, path traversal, remote-code execution, or other exploitable findings.

Supported security fixes target the latest tagged release and `main`. TitanSkies binds to `127.0.0.1` by default. If you expose it to a LAN, place it behind an operator-managed TLS reverse proxy, restrict network access, and keep the data directory read-only for the web process.

Never put `AIRNOW_API_KEY`, `BLOB_READ_WRITE_TOKEN`, `CRON_SECRET`, or other credentials in `NEXT_PUBLIC_*` variables, images, logs, or published data. Docker exposes no ingest or administrative endpoint. Vercel exposes only the bearer-authenticated `/api/context` Cron function; its Blob token is restricted to server-side code and the Vercel Blob API host. Release image digests are signed keylessly by the tag-bound GitHub release workflow and verified against its exact OIDC identity.
