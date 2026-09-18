# Release procedure

The canonical release implementation is [`scripts/release`](../scripts/release). GitHub-hosted and temporary Mac runners only bootstrap tools and invoke that script.

## Normal hosted release

1. Merge a release-ready PR with every required check green.
2. Confirm `package.json` contains the intended `X.Y.Z` version and `docs/releases/vX.Y.Z.md` exists.
3. Restrict approval on the `release` and `trusted-mac` environments to the repository owner (`mattfaltyn`). Allow owner self-review so releases triggered by the owner do not depend on another account; keep administrator bypass disabled. Protect `v*` tags with separate active rulesets: repository administrators may create them, while nobody may update or delete them.
4. Create and push an annotated `vX.Y.Z` tag at a commit contained in protected `main` history.
5. The tag-triggered Release workflow first runs the full release gate with read-only permissions. Only after that succeeds does the protected publish job receive package, release, and OIDC permissions. It builds `linux/amd64` and `linux/arm64`, validates platform-bound SBOM and `mode=max` provenance, signs the digest keylessly, verifies its exact workflow identity, proves anonymous visibility, moves `latest`, and creates the GitHub Release last.

The workflow publishes only `vX.Y.Z` and `latest`. The GitHub Release contains the CycloneDX SBOM, third-party notices, versioned release notes, and image digest record. GitHub supplies source archives.

If the first GHCR package is private, the workflow stops after the version digest is built and signed. Make the repository-linked container package public, then rerun the same tag workflow. The script accepts only the exact annotations, digest, attestations, and keyless signature before it resumes; `latest` and the GitHub Release remain unpublished until anonymous access succeeds.

Never replace a release tag or published GitHub Release. Fix a published defect with the next patch version.

## Verify and pin an image

```sh
docker buildx imagetools inspect ghcr.io/hypertrial/titanskies:vX.Y.Z
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity https://github.com/hypertrial/titanskies/.github/workflows/release.yml@refs/tags/vX.Y.Z \
  ghcr.io/hypertrial/titanskies@sha256:DIGEST
docker buildx imagetools inspect --format '{{json .SBOM}}' ghcr.io/hypertrial/titanskies@sha256:DIGEST
docker buildx imagetools inspect --format '{{json .Provenance}}' ghcr.io/hypertrial/titanskies@sha256:DIGEST
```

Set `TITANSKIES_VERSION=vX.Y.Z` for immutable Compose updates. For digest pinning, use an operator Compose override with `image: ghcr.io/hypertrial/titanskies@sha256:DIGEST`.

## Temporary Mac runner fallback

Use this only for a trusted ref already present in `hypertrial/titanskies` when hosted capacity is unavailable. The runner is repository-scoped, ephemeral, one-job, and never installed as a service.

1. Start the Mac's approved Linux Docker runtime (Docker Desktop or Colima), select its context, and confirm `docker info` and `docker buildx inspect --bootstrap` advertise both target platforms.
2. Cancel the queued hosted workflow run.
3. For a pull request, change the protected `main` ruleset's required check from `verify-hosted` to `verify-mac`. Both names run the same canonical gate, but separating them prevents a canceled hosted check from masking the Mac result. Restore `verify-hosted` after hosted capacity returns; never remove the required-check rule.
4. In GitHub repository Settings → Actions → Runners, choose **New self-hosted runner**, select macOS ARM64, and use the current package URL and checksum commands shown by GitHub. Do not reuse an older download or checksum from this document.
5. Manually dispatch `ci.yml` or `release.yml` at the trusted branch/tag with `runner=mac`. A release dispatch must select the exact annotated tag. Note the workflow run ID and attempt number, then have `mattfaltyn` approve the `trusted-mac` environment only after checking the queued workflow, ref, commit, and actor. The workflow derives its one-use runner label as `titanskies-release-RUN_ID-RUN_ATTEMPT`; callers cannot choose it.
6. Extract the runner into a new temporary directory and register it with the one-use token from GitHub and that exact derived label. Suppress every default self-hosted label so no other workflow can select the runner:

   ```sh
   label="titanskies-release-RUN_ID-RUN_ATTEMPT"
   ./config.sh --url https://github.com/hypertrial/titanskies \
     --token ONE_TIME_TOKEN \
     --ephemeral --unattended --no-default-labels --labels "$label"
   ```

7. Run `./run.sh`. The runner accepts the verification job and auto-deregisters. For a release, the protected publish job then queues on the same derived label; approve the `release` environment, extract a second fresh runner directory, register another ephemeral runner with the same label and a new one-use token, and run it once. A rerun increments `RUN_ATTEMPT`, so derive and register the new label shown by that attempt.
8. On failure, copy `_diag` to a private operator location. Remove every runner directory, then confirm the repository has no registered runner:

   ```sh
   gh api repos/hypertrial/titanskies/actions/runners --jq '.runners'
   ```

Keep the runner offline except for this explicit dispatch. Use a dedicated unprivileged macOS account with no personal credentials or unrelated repository access. Never use the ordinary workstation login, never use it for fork pull requests, never install it as a background service, and never store a signing key on the machine. GitHub OIDC gives hosted and Mac runs the same keyless Cosign identity.
