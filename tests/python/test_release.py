from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVISION = "93a0dbedd66a0643a638cff386aacf348304b063"
TAG_OBJECT = "13a0dbedd66a0643a638cff386aacf348304b063"
DIGEST = "sha256:" + "0" * 64
AMD64_DIGEST = "sha256:" + "1" * 64
ARM64_DIGEST = "sha256:" + "2" * 64
IDENTITY = "https://github.com/hypertrial/titanskies/.github/workflows/release.yml@refs/tags/v0.1.0"


def _executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _release_checkout(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    state = tmp_path / "state"
    log = tmp_path / "commands.log"
    (repo / "scripts").mkdir(parents=True)
    (repo / "artifacts").mkdir()
    (repo / "docs/releases").mkdir(parents=True)
    fake_bin.mkdir()
    state.mkdir()
    shutil.copy2(ROOT / "scripts/release", repo / "scripts/release")
    (repo / "package.json").write_text('{"version":"0.1.0"}\n', encoding="utf-8")
    (repo / "artifacts/sbom.cdx.json").write_text("{}\n", encoding="utf-8")
    (repo / "artifacts/THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    (repo / "docs/releases/v0.1.0.md").write_text("release notes\n", encoding="utf-8")

    _executable(
        fake_bin / "git",
        'printf "git %s\\n" "$*" >> "$FAKE_LOG"\n'
        'case "$1" in\n'
        '  status) [ "${FAKE_DIRTY:-0}" = 1 ] && echo " M dirty"; exit 0 ;;\n'
        '  describe) printf "%s\\n" "${FAKE_DESCRIBE:-v0.1.0}" ;;\n'
        '  cat-file)\n'
        '    if [ "${FAKE_TAG_TYPE:-tag}" = checkout-commit ] && [ -f "$FAKE_STATE/tag-refreshed" ]; then echo tag;\n'
        '    else printf "%s\\n" "${FAKE_TAG_TYPE:-tag}"; fi ;;\n'
        '  ls-remote)\n'
        '    count_file="$FAKE_STATE/remote-lookups"\n'
        '    count=0; [ ! -f "$count_file" ] || count=$(cat "$count_file")\n'
        '    count=$((count + 1)); printf "%s" "$count" > "$count_file"\n'
        f'    remote_revision="${{FAKE_REMOTE_REVISION:-{REVISION}}}"\n'
        '    if [ -n "${FAKE_REMOTE_DIVERGE_AFTER:-}" ] && [ "$count" -gt "$FAKE_REMOTE_DIVERGE_AFTER" ]; then remote_revision=different; fi\n'
        f'    printf "%s\\trefs/tags/v0.1.0\\n%s\\trefs/tags/v0.1.0^{{}}\\n" "${{FAKE_REMOTE_TAG:-{TAG_OBJECT}}}" "$remote_revision" ;;\n'
        '  fetch)\n'
        '    [ "${FAKE_FETCH_FAIL:-0}" != 1 ] || exit 1\n'
        '    case "$*" in *refs/tags/v0.1.0:refs/tags/v0.1.0*) : > "$FAKE_STATE/tag-refreshed" ;; esac ;;\n'
        '  merge-base) [ "${FAKE_NOT_ON_MAIN:-0}" != 1 ] ;;\n'
        f'  rev-parse) case "$2" in refs/tags/*) echo {TAG_OBJECT} ;; *) echo {REVISION} ;; esac ;;\n'
        'esac\n',
    )
    _executable(fake_bin / "npm", 'printf "npm %s\\n" "$*" >> "$FAKE_LOG"\n')
    _executable(
        fake_bin / "gh",
        'printf "gh %s\\n" "$*" >> "$FAKE_LOG"\n'
        'case "$1 $2" in\n'
        '  "repo view") echo PUBLIC ;;\n'
        '  "release list")\n'
        '    [ "${FAKE_RELEASE_LOOKUP_ERROR:-0}" != 1 ] || exit 2\n'
        '    if [ -f "$FAKE_STATE/release" ]; then echo "[{\\"tagName\\":\\"v0.1.0\\"}]"; else echo "[]"; fi ;;\n'
        '  "release create")\n'
        '    [ -f "$FAKE_STATE/latest" ] || { echo "release created before latest" >&2; exit 3; }\n'
        '    if [ "${FAKE_RELEASE_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$FAKE_STATE/release-failed" ]; then\n'
        '      : > "$FAKE_STATE/release-failed"; exit 4\n'
        '    fi\n'
        '    : > "$FAKE_STATE/release" ;;\n'
        'esac\n',
    )
    _executable(
        fake_bin / "docker",
        'printf "docker DOCKER_CONFIG=%s %s\\n" "${DOCKER_CONFIG:-}" "$*" >> "$FAKE_LOG"\n'
        'if [ "$1 $2 $3" = "buildx inspect --bootstrap" ]; then\n'
        '  printf "Platforms: %s\\n" "${FAKE_BUILDER_PLATFORMS:-linux/amd64,linux/arm64}"; exit 0\n'
        'fi\n'
        'if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then\n'
        '  format= reference=\n'
        '  while [ "$#" -gt 0 ]; do\n'
        '    case "$1" in --format) shift; format=$1 ;; ghcr.io/*) reference=$1 ;; esac\n'
        '    shift\n'
        '  done\n'
        '  case "$format" in\n'
        '    *SBOM*)\n'
        '      [ "${FAKE_MISSING_SBOM:-0}" != 1 ] || exit 1\n'
        '      packages="[{\\"name\\":\\"titanskies\\"}]"\n'
        '      [ "${FAKE_INVALID_SBOM:-0}" != 1 ] || packages="[]"\n'
        '      printf "{\\"linux/amd64\\":{\\"SPDX\\":{\\"SPDXID\\":\\"SPDXRef-DOCUMENT\\",\\"spdxVersion\\":\\"SPDX-2.3\\",\\"packages\\":%s}},\\"linux/arm64\\":{\\"SPDX\\":{\\"SPDXID\\":\\"SPDXRef-DOCUMENT\\",\\"spdxVersion\\":\\"SPDX-2.3\\",\\"packages\\":%s}}}\\n" "$packages" "$packages"\n'
        '      exit 0 ;;\n'
        '    *Provenance*)\n'
        '      [ "${FAKE_MISSING_PROVENANCE:-0}" != 1 ] || exit 1\n'
        '      materials="[{\\"uri\\":\\"https://github.com/hypertrial/titanskies\\"}]"\n'
        '      [ "${FAKE_INVALID_PROVENANCE:-0}" != 1 ] || materials="[]"\n'
        '      vcs="{\\"source\\":\\"${FAKE_PROVENANCE_SOURCE:-https://github.com/hypertrial/titanskies}\\",\\"revision\\":\\"${FAKE_PROVENANCE_REVISION:-$FAKE_REVISION}\\"}"\n'
        '      printf "{\\"linux/amd64\\":{\\"SLSA\\":{\\"buildType\\":\\"https://mobyproject.org/buildkit@v1\\",\\"invocation\\":{\\"environment\\":{\\"platform\\":\\"linux/amd64\\"}},\\"metadata\\":{\\"completeness\\":{\\"parameters\\":true},\\"https://mobyproject.org/buildkit@v1#metadata\\":{\\"vcs\\":%s}},\\"materials\\":%s}},\\"linux/arm64\\":{\\"SLSA\\":{\\"buildType\\":\\"https://mobyproject.org/buildkit@v1\\",\\"invocation\\":{\\"environment\\":{\\"platform\\":\\"linux/arm64\\"}},\\"metadata\\":{\\"completeness\\":{\\"parameters\\":true},\\"https://mobyproject.org/buildkit@v1#metadata\\":{\\"vcs\\":%s}},\\"materials\\":%s}}}\\n" "$vcs" "$materials" "$vcs" "$materials"\n'
        '      exit 0 ;;\n'
        '  esac\n'
        '  case "$reference" in\n'
        '    *:v0.1.0)\n'
        '      [ -f "$FAKE_STATE/version" ] || exit 1\n'
        '      if [ -n "${DOCKER_CONFIG:-}" ] && [ "${FAKE_ANON_FAIL:-0}" = 1 ]; then exit 1; fi\n'
        '      reference_digest=${FAKE_VERSION_DIGEST:-$FAKE_DIGEST} ;;\n'
        '    *:latest) [ -f "$FAKE_STATE/latest" ] || exit 1; reference_digest=${FAKE_LATEST_DIGEST:-$FAKE_DIGEST} ;;\n'
        '    *@sha256:*) [ -f "$FAKE_STATE/digest" ] || exit 1 ;;\n'
        '  esac\n'
        '  [ -n "$format" ] || exit 0\n'
        '  architecture=${FAKE_IMAGE_ARCH:-arm64}\n'
        '  revision=${FAKE_IMAGE_REVISION:-$FAKE_REVISION}\n'
        '  digest=${reference_digest:-${FAKE_IMAGE_DIGEST:-$FAKE_DIGEST}}\n'
        f'  amd64_digest={AMD64_DIGEST}\n'
        f'  arm64_digest={ARM64_DIGEST}\n'
        '  arm64_subject=${FAKE_ATTESTATION_SUBJECT:-$arm64_digest}\n'
        '  printf "{\\"digest\\":\\"%s\\",\\"annotations\\":{\\"org.opencontainers.image.revision\\":\\"%s\\",\\"org.opencontainers.image.version\\":\\"0.1.0\\",\\"org.opencontainers.image.source\\":\\"https://github.com/hypertrial/titanskies\\"},\\"manifests\\":[{\\"digest\\":\\"%s\\",\\"platform\\":{\\"os\\":\\"linux\\",\\"architecture\\":\\"amd64\\"}},{\\"digest\\":\\"%s\\",\\"platform\\":{\\"os\\":\\"linux\\",\\"architecture\\":\\"%s\\"}},{\\"digest\\":\\"sha256:3333333333333333333333333333333333333333333333333333333333333333\\",\\"platform\\":{\\"os\\":\\"unknown\\",\\"architecture\\":\\"unknown\\"},\\"annotations\\":{\\"vnd.docker.reference.type\\":\\"attestation-manifest\\",\\"vnd.docker.reference.digest\\":\\"%s\\"}},{\\"digest\\":\\"sha256:4444444444444444444444444444444444444444444444444444444444444444\\",\\"platform\\":{\\"os\\":\\"unknown\\",\\"architecture\\":\\"unknown\\"},\\"annotations\\":{\\"vnd.docker.reference.type\\":\\"attestation-manifest\\",\\"vnd.docker.reference.digest\\":\\"%s\\"}}]}\\n" "$digest" "$revision" "$amd64_digest" "$arm64_digest" "$architecture" "$amd64_digest" "$arm64_subject"\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$1 $2" = "buildx build" ]; then\n'
        '  metadata=\n'
        '  while [ "$#" -gt 0 ]; do [ "$1" = --metadata-file ] && { shift; metadata=$1; }; shift; done\n'
        '  : > "$FAKE_STATE/digest"\n'
        '  printf "{\\"containerimage.digest\\":\\"%s\\"}\\n" "$FAKE_DIGEST" > "$metadata"\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$1 $2 $3" = "buildx imagetools create" ]; then\n'
        '  target=\n'
        '  while [ "$#" -gt 0 ]; do [ "$1" = --tag ] && { shift; target=$1; }; shift; done\n'
        '  case "$target" in\n'
        '    *:v0.1.0)\n'
        '      if [ "${FAKE_VERSION_TAG_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$FAKE_STATE/version-tag-failed" ]; then : > "$FAKE_STATE/version-tag-failed"; exit 5; fi\n'
        '      : > "$FAKE_STATE/version" ;;\n'
        '    *:latest)\n'
        '      if [ "${FAKE_LATEST_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$FAKE_STATE/latest-failed" ]; then : > "$FAKE_STATE/latest-failed"; exit 6; fi\n'
        '      : > "$FAKE_STATE/latest" ;;\n'
        '  esac\n'
        '  exit 0\n'
        'fi\n'
        'exit 1\n',
    )
    _executable(
        fake_bin / "cosign",
        'printf "cosign %s\\n" "$*" >> "$FAKE_LOG"\n'
        'case "$1" in\n'
        '  sign)\n'
        '    if [ "${FAKE_SIGN_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$FAKE_STATE/sign-failed" ]; then\n'
        '      : > "$FAKE_STATE/sign-failed"; exit 1\n'
        '    fi\n'
        '    : > "$FAKE_STATE/signed" ;;\n'
        '  verify) [ "${FAKE_BAD_SIGNATURE:-0}" != 1 ] && [ -f "$FAKE_STATE/signed" ] ;;\n'
        'esac\n',
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "FAKE_REVISION": REVISION,
        "FAKE_DIGEST": DIGEST,
        "FAKE_STATE": str(state),
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "hypertrial/titanskies",
        "GITHUB_REF": "refs/tags/v0.1.0",
        "GITHUB_REF_NAME": "v0.1.0",
        "GITHUB_SHA": REVISION,
        "GITHUB_EVENT_NAME": "push",
        "TITANSKIES_VERIFIED_SHA": REVISION,
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid/oidc",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic",
        "GH_TOKEN": "synthetic",
    }
    return repo, env, state, log


def _run_release(
    repo: Path, env: dict[str, str], mode: str = "publish"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [repo / "scripts/release", mode],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _commands(log: Path) -> str:
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_release_rejects_dirty_checkout_before_external_calls(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    result = _run_release(repo, {**env, "FAKE_DIRTY": "1"})

    assert result.returncode != 0
    assert "checkout must be clean" in result.stderr
    assert list(state.iterdir()) == []
    assert "docker" not in _commands(log)


def test_release_rejects_lightweight_tag_and_version_mismatch(tmp_path: Path) -> None:
    repo, env, _, log = _release_checkout(tmp_path)
    lightweight = _run_release(repo, {**env, "FAKE_TAG_TYPE": "commit"})
    assert lightweight.returncode != 0
    assert "must be an annotated tag" in lightweight.stderr

    (repo / "package.json").write_text('{"version":"0.2.0"}\n', encoding="utf-8")
    mismatch = _run_release(repo, env)
    assert mismatch.returncode != 0
    assert "HEAD must be tagged v0.2.0" in mismatch.stderr
    assert "docker buildx build" not in _commands(log)


def test_release_refreshes_annotated_tag_object_synthesized_by_checkout(
    tmp_path: Path,
) -> None:
    repo, env, state, log = _release_checkout(tmp_path)

    result = _run_release(repo, {**env, "FAKE_TAG_TYPE": "checkout-commit"}, "verify")

    assert result.returncode == 0, result.stderr
    assert (state / "tag-refreshed").exists()
    assert (
        "git fetch --force origin refs/tags/v0.1.0:refs/tags/v0.1.0"
        in _commands(log)
    )


def test_release_rejects_missing_oidc_before_registry_mutation(tmp_path: Path) -> None:
    repo, env, _, log = _release_checkout(tmp_path)
    env.pop("ACTIONS_ID_TOKEN_REQUEST_URL")
    result = _run_release(repo, env)

    assert result.returncode != 0
    assert "OIDC token endpoint is unavailable" in result.stderr
    assert "docker buildx build" not in _commands(log)
    assert "cosign sign" not in _commands(log)


def test_verify_mode_needs_no_oidc_write_credentials_or_publish_handoff(
    tmp_path: Path,
) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    for name in (
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "GH_TOKEN",
        "TITANSKIES_VERIFIED_SHA",
    ):
        env.pop(name)
    output = tmp_path / "github-output"
    env["GITHUB_OUTPUT"] = str(output)

    result = _run_release(repo, env, "verify")

    assert result.returncode == 0, result.stderr
    commands = _commands(log)
    assert "npm run verify:release" in commands
    assert "gh " not in commands
    assert "cosign " not in commands
    assert "docker DOCKER_CONFIG= buildx build" not in commands
    assert not (state / "digest").exists()
    assert output.read_text(encoding="utf-8") == f"revision={REVISION}\n"


def test_publish_mode_requires_exact_verified_revision_before_write_access(
    tmp_path: Path,
) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    env.pop("TITANSKIES_VERIFIED_SHA")

    missing = _run_release(repo, env, "publish")
    assert missing.returncode != 0
    assert "not paired with verification of this exact revision" in missing.stderr

    wrong = _run_release(repo, {**env, "TITANSKIES_VERIFIED_SHA": "f" * 40}, "publish")
    assert wrong.returncode != 0
    assert "not paired with verification of this exact revision" in wrong.stderr
    assert "gh " not in _commands(log)
    assert "cosign " not in _commands(log)
    assert not (state / "digest").exists()


def test_verify_and_publish_reject_tag_outside_refreshed_origin_main(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)

    for mode in ("verify", "publish"):
        result = _run_release(repo, {**env, "FAKE_NOT_ON_MAIN": "1"}, mode)
        assert result.returncode != 0
        assert "not contained in reviewed origin/main history" in result.stderr
    commands = _commands(log)
    assert commands.count("git fetch --no-tags origin main") == 2
    assert "npm run verify:release" not in commands
    assert "gh " not in commands
    assert "cosign " not in commands
    assert not (state / "digest").exists()


def test_release_rejects_untrusted_workflow_context_before_registry_mutation(
    tmp_path: Path,
) -> None:
    repo, env, _, log = _release_checkout(tmp_path)
    invalid_contexts = (
        ("GITHUB_REPOSITORY", "attacker/titanskies", "unexpected GitHub repository"),
        ("GITHUB_REF", "refs/heads/main", "workflow ref must be the exact release tag"),
        ("GITHUB_SHA", "f" * 40, "workflow revision does not match"),
        ("GITHUB_EVENT_NAME", "pull_request", "tag push or trusted manual dispatch"),
    )

    for key, value, message in invalid_contexts:
        result = _run_release(repo, {**env, key: value})
        assert result.returncode != 0
        assert message in result.stderr
    assert "docker buildx build" not in _commands(log)


def test_release_fails_closed_when_github_release_lookup_fails(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    result = _run_release(repo, {**env, "FAKE_RELEASE_LOOKUP_ERROR": "1"})

    assert result.returncode != 0
    assert "could not inspect GitHub Releases" in result.stderr
    assert not (state / "digest").exists()
    assert "docker buildx build" not in _commands(log)


def test_release_rejects_divergent_remote_tag_before_push(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    result = _run_release(repo, {**env, "FAKE_REMOTE_REVISION": "different"})

    assert result.returncode != 0
    assert "does not match this checkout" in result.stderr
    assert not (state / "digest").exists()
    assert "docker buildx build" not in _commands(log)


def test_release_rejects_wrong_platform_inventory_and_missing_attestation(tmp_path: Path) -> None:
    repo, env, state, _ = _release_checkout(tmp_path)
    wrong_platform = _run_release(repo, {**env, "FAKE_IMAGE_ARCH": "ppc64le"})
    assert wrong_platform.returncode != 0
    assert "manifest does not match" in wrong_platform.stderr

    for child in state.iterdir():
        child.unlink()
    missing_sbom = _run_release(repo, {**env, "FAKE_MISSING_SBOM": "1"})
    assert missing_sbom.returncode != 0
    assert "could not inspect BuildKit SBOM" in missing_sbom.stderr
    assert not (state / "version").exists()


def test_release_rejects_unbound_attestation_subject_and_invalid_predicates(
    tmp_path: Path,
) -> None:
    repo, env, state, _ = _release_checkout(tmp_path)
    invalid_cases = (
        (
            {"FAKE_ATTESTATION_SUBJECT": "sha256:" + "9" * 64},
            "published image manifest does not match",
        ),
        ({"FAKE_INVALID_SBOM": "1"}, "BuildKit SBOM is invalid or missing"),
        (
            {"FAKE_INVALID_PROVENANCE": "1"},
            "BuildKit provenance is invalid or missing",
        ),
        (
            {"FAKE_PROVENANCE_REVISION": "different"},
            "BuildKit provenance is invalid or missing",
        ),
        (
            {"FAKE_PROVENANCE_SOURCE": "https://github.com/attacker/titanskies"},
            "BuildKit provenance is invalid or missing",
        ),
    )

    for extra_env, message in invalid_cases:
        for child in state.iterdir():
            child.unlink()
        result = _run_release(repo, {**env, **extra_env})
        assert result.returncode != 0
        assert message in result.stderr
        assert not (state / "version").exists()
        assert not (state / "latest").exists()
        assert not (state / "release").exists()


def test_release_rejects_mismatched_or_unsigned_existing_version(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    (state / "version").touch()
    (state / "digest").touch()
    mismatch = _run_release(repo, {**env, "FAKE_IMAGE_REVISION": "different"})
    assert mismatch.returncode != 0
    assert "manifest does not match" in mismatch.stderr

    unsigned = _run_release(repo, env)
    assert unsigned.returncode != 0
    assert "signature does not match" in unsigned.stderr
    assert "cosign sign --yes" not in _commands(log)
    assert not (state / "latest").exists()


def test_release_keyless_identity_and_release_are_exact_and_last(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    result = _run_release(repo, env)

    assert result.returncode == 0, result.stderr
    commands = _commands(log)
    assert "--provenance=mode=max" in commands
    assert "--sbom=true" in commands
    assert "BUILDX_GIT_LABELS=full BUILDX_GIT_CHECK_DIRTY=1 docker buildx build" in (
        repo / "scripts/release"
    ).read_text(encoding="utf-8")
    assert "push-by-digest=true" in commands
    assert "ghcr.io/hypertrial/titanskies:release-" not in commands
    assert f"--certificate-identity {IDENTITY}" in commands
    assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in commands
    assert state.joinpath("version").exists()
    assert state.joinpath("latest").exists()
    assert state.joinpath("release").exists()
    assert commands.index("imagetools create --tag ghcr.io/hypertrial/titanskies:latest") < commands.index("gh release create")


def test_release_retries_after_sign_failure_without_promoting(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    first = _run_release(repo, {**env, "FAKE_SIGN_FAIL_ONCE": "1"})

    assert first.returncode != 0
    assert not (state / "version").exists()
    second = _run_release(repo, {**env, "FAKE_SIGN_FAIL_ONCE": "1"})
    assert second.returncode == 0, second.stderr
    assert _commands(log).count("docker DOCKER_CONFIG= buildx build") == 2


def test_release_rejects_changed_version_tag_digest_before_latest(tmp_path: Path) -> None:
    repo, env, state, _ = _release_checkout(tmp_path)
    different_digest = "sha256:" + "1" * 64
    result = _run_release(repo, {**env, "FAKE_VERSION_DIGEST": different_digest})

    assert result.returncode != 0
    assert "version tag digest changed" in result.stderr
    assert (state / "version").exists()
    assert not (state / "latest").exists()
    assert not (state / "release").exists()


def test_release_retries_after_version_tag_failure_without_advancing_latest(
    tmp_path: Path,
) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    first = _run_release(repo, {**env, "FAKE_VERSION_TAG_FAIL_ONCE": "1"})

    assert first.returncode != 0
    assert (state / "signed").exists()
    assert not (state / "version").exists()
    assert not (state / "latest").exists()
    second = _run_release(repo, {**env, "FAKE_VERSION_TAG_FAIL_ONCE": "1"})
    assert second.returncode == 0, second.stderr
    assert _commands(log).count("docker DOCKER_CONFIG= buildx build") == 2


def test_release_visibility_failure_stops_before_latest_and_resumes_exact_version(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    first = _run_release(repo, {**env, "FAKE_ANON_FAIL": "1"})

    assert first.returncode != 0
    assert "not anonymously readable" in first.stderr
    assert (state / "version").exists()
    assert (state / "signed").exists()
    assert not (state / "latest").exists()
    assert not (state / "release").exists()

    second = _run_release(repo, env)
    assert second.returncode == 0, second.stderr
    assert _commands(log).count("docker DOCKER_CONFIG= buildx build") == 1
    assert "DOCKER_CONFIG=/" in _commands(log)


def test_release_never_overwrites_existing_github_release(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    (state / "release").touch()
    result = _run_release(repo, env)

    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert "docker buildx build" not in _commands(log)


def test_release_can_retry_when_github_release_creation_failed_last(tmp_path: Path) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    first = _run_release(repo, {**env, "FAKE_RELEASE_FAIL_ONCE": "1"})

    assert first.returncode != 0
    assert (state / "latest").exists()
    assert not (state / "release").exists()
    second = _run_release(repo, {**env, "FAKE_RELEASE_FAIL_ONCE": "1"})
    assert second.returncode == 0, second.stderr
    assert _commands(log).count("docker DOCKER_CONFIG= buildx build") == 1


def test_release_retries_latest_failure_from_verified_version_without_rebuild(
    tmp_path: Path,
) -> None:
    repo, env, state, log = _release_checkout(tmp_path)
    first = _run_release(repo, {**env, "FAKE_LATEST_FAIL_ONCE": "1"})

    assert first.returncode != 0
    assert (state / "version").exists()
    assert not (state / "latest").exists()
    assert not (state / "release").exists()
    second = _run_release(repo, {**env, "FAKE_LATEST_FAIL_ONCE": "1"})
    assert second.returncode == 0, second.stderr
    assert _commands(log).count("docker DOCKER_CONFIG= buildx build") == 1


def test_release_rejects_changed_latest_digest_and_remote_tag_race(tmp_path: Path) -> None:
    repo, env, state, _ = _release_checkout(tmp_path)
    changed_latest = _run_release(
        repo, {**env, "FAKE_LATEST_DIGEST": "sha256:" + "2" * 64}
    )
    assert changed_latest.returncode != 0
    assert "latest digest differs" in changed_latest.stderr
    assert not (state / "release").exists()

    for child in state.iterdir():
        child.unlink()
    remote_race = _run_release(repo, {**env, "FAKE_REMOTE_DIVERGE_AFTER": "1"})
    assert remote_race.returncode != 0
    assert "origin tag v0.1.0 does not match" in remote_race.stderr
    assert (state / "latest").exists()
    assert not (state / "release").exists()
