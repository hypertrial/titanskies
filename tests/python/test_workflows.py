from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
ACTION = re.compile(r"^\s*-\s*uses:\s*([^\s#]+)", re.MULTILINE)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _pinned_or_local(action: str) -> bool:
    return action.startswith("./") or bool(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action))


def test_all_third_party_actions_are_pinned_to_full_commit_shas() -> None:
    actions = [action for workflow in WORKFLOWS.glob("*.yml") for action in ACTION.findall(workflow.read_text(encoding="utf-8"))]
    composite = ROOT / ".github/actions"
    if composite.is_dir():
        actions.extend(action for path in composite.rglob("action.yml") for action in ACTION.findall(path.read_text(encoding="utf-8")))
    assert actions
    assert all(_pinned_or_local(action) for action in actions)
    assert any(action.startswith("./.github/actions/") for action in actions)
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions if not action.startswith("./"))


def test_ci_is_the_fast_check_only() -> None:
    ci = _workflow("ci.yml")
    setup = (ROOT / ".github/actions/setup/action.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read\n" in ci
    assert "pull_request:" in ci and "branches: [main]" in ci
    assert "workflow_dispatch:" in ci
    assert "schedule:" not in ci
    assert "inputs:" not in ci
    assert "mac_label" not in ci
    assert "self-hosted" not in ci
    assert "./scripts/bootstrap-ci-tools" in setup
    assert "actions/checkout@" not in setup
    assert ci.index("actions/checkout@") < ci.index("./.github/actions/setup")
    assert ci.count("runs-on:") == 1
    assert ci.count("actions/checkout@") == 1
    assert ci.count("./scripts/verify-checks") == 1
    assert "./scripts/verify-web" not in ci
    assert "./scripts/verify-containers" not in ci
    assert "./scripts/verify_release.sh" not in ci
    assert "verify-hosted" not in ci
    assert "verify-mac" not in ci
    assert "brew install" not in ci


def test_heavy_runs_web_and_containers_weekly_and_mac_only_when_dispatched() -> None:
    heavy = _workflow("heavy.yml")
    assert "permissions:\n  contents: read\n" in heavy
    assert "pull_request:" not in heavy
    assert "push:" not in heavy
    assert "workflow_dispatch:" in heavy
    assert 'cron: "0 6 * * 1"' in heavy
    group = next(line.strip() for line in heavy.splitlines() if line.strip().startswith("group:"))
    assert group.startswith("group: heavy-${{ github.workflow }}-${{ github.ref }}-")
    assert group.endswith("inputs.runner || 'hosted' }}")
    assert "workflow_dispatch" in group
    assert "mac_label" not in heavy
    assert "self-hosted" not in heavy
    assert "format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)" in heavy
    assert heavy.count("format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)") == 1
    assert "environment: trusted-mac" in heavy
    assert "release-verification" not in heavy
    assert heavy.count("inputs.runner != 'mac'") == 2
    assert "github.event_name == 'workflow_dispatch' && inputs.runner == 'mac'" in heavy
    assert heavy.count("runs-on:") == 3
    assert heavy.count("actions/checkout@") == 3
    assert heavy.count("./scripts/verify-web") == 1
    assert heavy.count("./scripts/verify-containers") == 1
    assert heavy.count("./scripts/verify_release.sh") == 1
    assert "./scripts/verify-checks" not in heavy
    assert "always() && !cancelled()" not in heavy
    assert "needs:" not in heavy
    assert "\n  verify-mac:\n" in heavy
    assert "brew install" not in heavy


def test_release_has_minimal_permissions_and_delegates_to_one_script() -> None:
    release = _workflow("release.yml")
    assert "permissions:\n  contents: read\n" in release
    assert 'COREPACK_ENABLE_AUTO_PIN: "0"' in release
    assert "attestations: write" not in release
    assert "pull_request:" not in release
    assert "branches:" not in release
    assert "tags:" not in release
    assert "push:" not in release
    assert "workflow_dispatch:" in release
    assert "mac_label" not in release
    assert "format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)" in release
    assert release.count("format('titanskies-release-{0}-{1}', github.run_id, github.run_attempt)") == 2
    assert "environment: ${{ inputs.runner == 'mac' && 'trusted-mac' || 'release-verification' }}" in release
    assert release.count("runs-on:") == 2
    assert "self-hosted" not in release
    assert "./.github/actions/setup" in release
    assert release.index("actions/checkout@") < release.index("./.github/actions/setup")
    assert "./scripts/bootstrap-ci-tools" in (ROOT / ".github/actions/setup/action.yml").read_text(encoding="utf-8")
    assert release.count("./scripts/release verify") == 1
    assert release.count("./scripts/release publish") == 1
    assert "inputs.runner != 'mac'" in release
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
    assert "artifact_name=release-metadata-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in verify
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
