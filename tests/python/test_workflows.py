from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
ACTION = re.compile(r"^\s*-\s*uses:\s*([^\s#]+)", re.MULTILINE)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_all_third_party_actions_are_pinned_to_full_commit_shas() -> None:
    actions = [
        action
        for workflow in WORKFLOWS.glob("*.yml")
        for action in ACTION.findall(workflow.read_text(encoding="utf-8"))
    ]
    assert actions
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions)


def test_ci_is_read_only_and_mac_is_manual_only() -> None:
    ci = _workflow("ci.yml")
    assert "permissions:\n  contents: read\n" in ci
    assert "pull_request:" in ci and "branches: [main]" in ci
    assert "workflow_dispatch:" in ci
    assert "github.event_name == 'workflow_dispatch'" in ci
    assert "mac_label" not in ci
    assert "format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)" in ci
    assert ci.count("format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)") == 1
    assert "environment: ${{ inputs.runner == 'mac' && 'trusted-mac' || 'release-verification' }}" in ci
    assert ci.count("runs-on:") == 1
    assert "self-hosted" not in ci
    assert ci.count("./scripts/bootstrap-ci-tools") == 1
    assert ci.count("./scripts/verify_release.sh") == 1
    assert "brew install" not in ci
    assert "verify-hosted:" not in ci
    assert "verify-mac:" not in ci


def test_release_has_minimal_permissions_and_delegates_to_one_script() -> None:
    release = _workflow("release.yml")
    assert "permissions:\n  contents: read\n" in release
    assert "attestations: write" not in release
    assert "pull_request:" not in release
    assert "branches:" not in release
    assert 'tags:\n      - "v*"' in release
    assert "workflow_dispatch:" in release
    assert "mac_label" not in release
    assert "format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)" in release
    assert release.count("format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)") == 2
    assert "environment: ${{ inputs.runner == 'mac' && 'trusted-mac' || 'release-verification' }}" in release
    assert release.count("runs-on:") == 2
    assert "self-hosted" not in release
    assert release.count("./scripts/bootstrap-ci-tools") == 1
    assert release.count("./scripts/release verify") == 1
    assert release.count("./scripts/release publish") == 1
    assert "brew install" not in release
    for duplicated_policy in ("docker buildx build", "cosign sign ", "gh release create"):
        assert duplicated_policy not in release


def test_release_grants_write_and_oidc_only_to_publish_after_verified_artifact() -> None:
    release = _workflow("release.yml")
    verify = release.split("  verify:", 1)[1].split("\n  publish:", 1)[0]
    publish = release.split("\n  publish:", 1)[1]

    assert "permissions:\n      contents: read\n" in verify
    assert "contents: write" not in verify
    assert "packages: write" not in verify
    assert "id-token: write" not in verify
    assert "actions/upload-artifact@" in verify
    assert 'artifact_name=release-metadata-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}' in verify
    assert "artifact_name: ${{ steps.release_metadata.outputs.artifact_name }}" in verify
    assert "name: ${{ steps.release_metadata.outputs.artifact_name }}" in verify
    assert "artifacts/sbom.cdx.json" in verify
    assert "artifacts/THIRD_PARTY_NOTICES.md" in verify

    assert "needs: verify" in publish
    assert "permissions:\n      contents: write\n      packages: write\n      id-token: write\n" in publish
    assert "actions/download-artifact@" in publish
    assert "name: ${{ needs.verify.outputs.artifact_name }}" in publish
    assert "path: ${{ runner.temp }}/release-artifacts" in publish
    assert "TITANSKIES_VERIFIED_SHA: ${{ needs.verify.outputs.revision }}" in publish
    assert "TITANSKIES_RELEASE_ARTIFACTS: ${{ runner.temp }}/release-artifacts" in publish
    assert "environment: release" in publish
