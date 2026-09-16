# macOS production release runbook

This is the operator procedure for the public TitanSkies Apple Silicon release. It intentionally requires a human operator and credentials that never enter Git, logs, Pad, or release assets.

## One-time workstation setup

Install a full Xcode release and select it:

```sh
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
./scripts/macos-release-preflight --build-only
```

Import a `Developer ID Application` identity, configure an App Store Connect notarization profile for `notarytool`, and keep the existing Ed25519 update-signing private key offline. The release commands expect only these names and paths:

```sh
export MACOS_DEVELOPER_ID_IDENTITY='Developer ID Application: …'
export MACOS_TEAM_ID='…'
export MACOS_NOTARY_PROFILE='titanskies-notary'
export MACOS_UPDATE_PRIVATE_KEY='/secure/offline/path/titanskies-update-ed25519.pem'
export TITANSKIES_MACOS_CHANNEL='production'
export MACOS_BUILD_NUMBER='1'
```

Do not print, copy, upload, or commit credential contents.

## Prepare the immutable source

Start from the merged, clean `main` commit. Run the repository gates before tagging, then create an annotated tag:

```sh
git switch main
git pull --ff-only origin main
scripts/verify-fast
scripts/verify
npm run verify:release
gitleaks git --redact --log-opts="--all"
git tag -a v0.1.0 -m 'TitanSkies 0.1.0'
```

Do not move or reuse a published version tag. The signing script requires the checked-out commit to be the exact annotated `v0.1.0` tag and the source tree to be clean.

## Build and seal

Build the self-contained production app, then let the signing script create the DMG. Do not run `build-macos-dmg` between these commands: the release script first signs, notarizes, and staples the app, then builds a fresh DMG from that exact app.

```sh
./scripts/macos-release-preflight
./scripts/build-macos-runtime
./scripts/build-macos-app
./scripts/sign-macos-release
./scripts/verify-macos
```

`sign-macos-release` performs the ordered trust chain: nested Developer ID signing, app verification, app archive notarization, app stapling, fresh DMG creation, DMG signing/notarization/stapling, mounted-app equivalence checks, final checksum creation, and atomic Ed25519 manifest signing and verification. It also emits the versioned SBOM, third-party notices, and release notes in `dist/macos`.

Expected release assets are:

- `TitanSkies-0.1.0-macos-arm64.dmg`
- `TitanSkies-0.1.0-macos-arm64.dmg.sha256`
- `macos-arm64-update.json`
- `TitanSkies-0.1.0-sbom.cdx.json`
- `TitanSkies-0.1.0-THIRD_PARTY_NOTICES.md`
- `TitanSkies-0.1.0-release-notes.md`

Complete the disposable-user acceptance test from the release work item before publication. Download through a browser so quarantine and Gatekeeper behavior are real; test install, approval, service persistence, current-version status, signed 0.0.9-to-0.1.0 update, forced rollback, repair, and both uninstall modes. During update and rollback, confirm the embedded updater can unregister and re-register both `SMAppService` agents from the installed bundle and that the verified listener PID belongs to the expected launchd job.

## Publish manually

After all gates pass, change `hypertrial/titanskies` to public, push the immutable annotated tag, and create a non-draft, non-prerelease GitHub Release. Upload the six generated assets without renaming or overwriting them. The repository must be public before the update feed is announced so anonymous update checks work.

From a logged-out client, verify these three URLs without authentication:

```text
https://github.com/hypertrial/titanskies/releases/latest/download/macos-arm64-update.json
https://github.com/hypertrial/titanskies/releases/download/v0.1.0/TitanSkies-0.1.0-macos-arm64.dmg
https://github.com/hypertrial/titanskies/releases/tag/v0.1.0
```

Download the manifest and DMG again from those public URLs, verify the checksum and manifest signature, and rerun the mounted-artifact checks before announcing the release.

## Rollback policy

Published assets and `v0.1.0` remain immutable. If distribution must be superseded, fix forward with `v0.1.1` and a newly signed manifest. The in-app transaction handles a failed local replacement by restoring and relaunching the previous validated app; it does not mutate an already-published GitHub Release.
