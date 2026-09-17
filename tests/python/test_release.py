from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVISION = "93a0dbedd66a0643a638cff386aacf348304b063"
TAG_OBJECT = "13a0dbedd66a0643a638cff386aacf348304b063"


def _executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _release_checkout(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    state = tmp_path / "state"
    (repo / "scripts").mkdir(parents=True)
    (repo / "artifacts").mkdir()
    (repo / "docs/releases").mkdir(parents=True)
    fake_bin.mkdir()
    state.mkdir()
    shutil.copy2(ROOT / "scripts/release", repo / "scripts/release")
    (repo / "package.json").write_text('{"version":"0.1.0"}\n', encoding="utf-8")
    (repo / "packaging").mkdir()
    (repo / "packaging/release-cosign.pub").write_text("test public key\n", encoding="utf-8")
    (repo / "artifacts/THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    (repo / "docs/releases/v0.1.0.md").write_text("release notes\n", encoding="utf-8")
    _executable(repo / "scripts/run_python.sh", ': > "$2"\n')

    _executable(
        fake_bin / "git",
        'printf "git %s\\n" "$*" >> "$FAKE_LOG"\n'
        'case "$1" in\n'
        '  status) exit 0 ;;\n'
        '  describe) echo v0.1.0 ;;\n'
        '  cat-file) echo tag ;;\n'
        f'  ls-remote) printf "%s\\trefs/tags/v0.1.0\\n%s\\trefs/tags/v0.1.0^{{}}\\n" "${{FAKE_REMOTE_TAG:-{TAG_OBJECT}}}" "${{FAKE_REMOTE_REVISION:-{REVISION}}}" ;;\n'
        f'  rev-parse) case "$2" in refs/tags/*) echo {TAG_OBJECT} ;; *) echo {REVISION} ;; esac ;;\n'
        '  archive) printf archive ;;\n'
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
        '    if [ ! -f "$FAKE_STATE/release" ]; then echo "[]"\n'
        '    elif [ "$(cat "$FAKE_STATE/release")" = draft ]; then echo "[{\\"tagName\\":\\"v0.1.0\\",\\"isDraft\\":true}]"\n'
        '    else echo "[{\\"tagName\\":\\"v0.1.0\\",\\"isDraft\\":false}]"; fi ;;\n'
        '  "release view")\n'
        '    printf "%s\\n" titanskies-0.1.0-image-digest.txt titanskies-0.1.0-release-notes.md titanskies-0.1.0-sbom.cdx.json titanskies-0.1.0-third-party-notices.md titanskies-0.1.0.tar.gz SHA256SUMS SHA256SUMS.sig\n'
        '    [ "${FAKE_EXTRA_ASSET:-0}" != 1 ] || echo unexpected.pkg ;;\n'
        '  "release create") echo draft > "$FAKE_STATE/release" ;;\n'
        '  "release upload")\n'
        '    if [ "${FAKE_UPLOAD_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$FAKE_STATE/upload-failed" ]; then\n'
        '      : > "$FAKE_STATE/upload-failed"; exit 1\n'
        '    fi ;;\n'
        '  "release edit") case "$*" in *--draft=true*) echo draft > "$FAKE_STATE/release" ;; *) echo published > "$FAKE_STATE/release" ;; esac ;;\n'
        'esac\n',
    )
    _executable(
        fake_bin / "docker",
        'printf "docker %s\\n" "$*" >> "$FAKE_LOG"\n'
        'if [ "$1 $2 $3" = "buildx inspect --bootstrap" ]; then\n'
        '  printf "Platforms: linux/amd64,linux/arm64\\n"; exit 0\n'
        'fi\n'
        'if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then\n'
        '  for reference do :; done\n'
        '  case "$reference" in *:v0.1.0) marker=final-image ;; *) marker=candidate-image ;; esac\n'
        '  [ -f "$FAKE_STATE/$marker" ] || exit 1\n'
        '  revision="${FAKE_IMAGE_REVISION:-$FAKE_REVISION}"\n'
        '  printf "{\\"digest\\":\\"sha256:%064d\\",\\"annotations\\":{\\"org.opencontainers.image.revision\\":\\"%s\\",\\"org.opencontainers.image.version\\":\\"0.1.0\\",\\"org.opencontainers.image.source\\":\\"https://github.com/hypertrial/titanskies\\"},\\"manifests\\":[{\\"platform\\":{\\"os\\":\\"linux\\",\\"architecture\\":\\"amd64\\"}},{\\"platform\\":{\\"os\\":\\"linux\\",\\"architecture\\":\\"arm64\\"}}]}\\n" 0 "$revision"\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$1 $2" = "buildx build" ]; then : > "$FAKE_STATE/candidate-image"; exit 0; fi\n'
        'if [ "$1 $2 $3" = "buildx imagetools create" ]; then : > "$FAKE_STATE/final-image"; exit 0; fi\n'
        'exit 1\n',
    )
    _executable(
        fake_bin / "cosign",
        'printf "cosign %s\\n" "$*" >> "$FAKE_LOG"\n'
        'case "$1" in\n'
        '  sign-blob)\n'
        '    while [ "$#" -gt 0 ]; do\n'
        '      if [ "$1" = --output-signature ]; then shift; printf signature > "$1"; fi\n'
        '      shift\n'
        '    done ;;\n'
        '  verify-blob) [ "${FAKE_BAD_KEY:-0}" != 1 ] ;;\n'
        '  sign)\n'
        '    if [ "${FAKE_SIGN_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$FAKE_STATE/sign-failed" ]; then\n'
        '      : > "$FAKE_STATE/sign-failed"; exit 1\n'
        '    fi\n'
        '    : > "$FAKE_STATE/signed" ;;\n'
        '  verify) [ -f "$FAKE_STATE/signed" ] ;;\n'
        'esac\n',
    )
    key = tmp_path / "private.key"
    key.write_text("test private key\n", encoding="utf-8")
    log = tmp_path / "commands.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "COSIGN_KEY": str(key),
        "FAKE_LOG": str(log),
        "FAKE_REVISION": REVISION,
        "FAKE_STATE": str(state),
    }
    return repo, env, state


def _run_release(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [repo / "scripts/release"], cwd=repo, env=env, capture_output=True, text=True,
    )


def test_release_rejects_wrong_key_before_image_push(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    result = _run_release(repo, {**env, "FAKE_BAD_KEY": "1"})

    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert not (state / "candidate-image").exists()
    assert "docker buildx build" not in Path(env["FAKE_LOG"]).read_text(encoding="utf-8")


def test_release_fails_closed_when_release_lookup_fails(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    result = _run_release(repo, {**env, "FAKE_RELEASE_LOOKUP_ERROR": "1"})

    assert result.returncode != 0
    assert not (state / "candidate-image").exists()
    commands = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "docker buildx build" not in commands
    assert "cosign sign --yes" not in commands


def test_release_rebuilds_unsigned_candidate_after_signing_failure(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    first = _run_release(repo, {**env, "FAKE_SIGN_FAIL_ONCE": "1"})
    second = _run_release(repo, {**env, "FAKE_SIGN_FAIL_ONCE": "1"})

    assert first.returncode != 0
    assert second.returncode == 0, second.stderr
    commands = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert commands.count("docker buildx build") == 2
    assert "index:org.opencontainers.image.revision=" + REVISION in commands
    assert "index:org.opencontainers.image.version=0.1.0" in commands
    assert "index:org.opencontainers.image.source=https://github.com/hypertrial/titanskies" in commands
    assert (state / "release").read_text(encoding="utf-8").strip() == "published"


def test_release_rejects_existing_image_from_another_revision(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    (state / "final-image").touch()
    result = _run_release(repo, {**env, "FAKE_IMAGE_REVISION": "different"})

    assert result.returncode != 0
    assert "does not match this release" in result.stderr
    commands = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "cosign sign --yes" not in commands
    assert "gh release create" not in commands


def test_release_rejects_unsigned_existing_image_even_when_annotations_match(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    (state / "final-image").touch()
    result = _run_release(repo, env)

    assert result.returncode != 0
    assert "not signed" in result.stderr
    commands = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert "cosign sign --yes" not in commands
    assert "gh release create" not in commands


def test_release_rejects_divergent_remote_tag_before_push(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    result = _run_release(repo, {**env, "FAKE_REMOTE_REVISION": "different"})

    assert result.returncode != 0
    assert "does not match this checkout" in result.stderr
    assert not (state / "candidate-image").exists()


def test_release_resumes_unpublished_draft_after_upload_failure(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    first = _run_release(repo, {**env, "FAKE_UPLOAD_FAIL_ONCE": "1"})
    second = _run_release(repo, {**env, "FAKE_UPLOAD_FAIL_ONCE": "1"})

    assert first.returncode != 0
    assert second.returncode == 0, second.stderr
    commands = Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
    assert commands.count("gh release create") == 1
    assert commands.count("docker buildx build") == 1
    assert (state / "release").read_text(encoding="utf-8").strip() == "published"


def test_release_rejects_unexpected_draft_asset(tmp_path: Path) -> None:
    repo, env, state = _release_checkout(tmp_path)
    result = _run_release(repo, {**env, "FAKE_EXTRA_ASSET": "1"})

    assert result.returncode != 0
    assert "assets do not match" in result.stderr
    assert (state / "release").read_text(encoding="utf-8").strip() == "draft"
