# Release procedure

Releases are manual. No GitHub Actions workflow or signing credential belongs in this repository.

1. Merge the release-hardening change and create an annotated `vX.Y.Z` tag whose version matches `package.json`.
2. Push the tag to `origin` and ensure the public repository is visible without authentication.
3. Select a Docker Buildx builder that supports `linux/amd64` and `linux/arm64`.
4. Keep the private Cosign key outside Git and set `COSIGN_KEY` to its path. It must match `packaging/release-cosign.pub`.
5. From a clean checkout at the tag, run `./scripts/release`.

The command runs every release gate, creates the versioned source archive, SBOM, third-party notices, checksums and checksum signature, builds and pushes the two-architecture GHCR manifest, signs its immutable digest, verifies both signatures, and creates an immutable GitHub Release. It refuses an existing release rather than overwriting assets.

Before announcing the release, inspect the published manifest platforms, verify its Cosign signature, test both architectures on representative or emulated hosts, run the Compose smoke, and complete the Omarchy install/update/rollback/uninstall acceptance sequence.
