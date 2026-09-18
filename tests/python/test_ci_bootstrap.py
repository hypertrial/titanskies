from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/bootstrap-ci-tools"


def _executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _bootstrap_environment(
    tmp_path: Path, system: str, machine: str
) -> tuple[dict[str, str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    github_path = tmp_path / "github-path"
    for command in ("npm", "uv", "npx", "docker"):
        _executable(fake_bin / command, f'printf "{command} %s\\n" "$*" >> "$FAKE_LOG"\n')
    _executable(
        fake_bin / "uname",
        f'case "${{1:-}}" in -s) echo {system} ;; -m) echo {machine} ;; *) echo {system} ;; esac\n',
    )
    _executable(
        fake_bin / "curl",
        'printf "curl %s\\n" "$*" >> "$FAKE_LOG"\n'
        'previous=\n'
        'for argument do\n'
        '  if [ "$previous" = --output ]; then printf archive > "$argument"; fi\n'
        '  previous=$argument\n'
        'done\n',
    )
    _executable(
        fake_bin / "sha256sum",
        'printf "sha256sum %s\\n" "$*" >> "$FAKE_LOG"\n'
        'cat >> "$FAKE_LOG"\n'
        '[ "${FAKE_CHECKSUM_FAIL:-0}" != 1 ]\n',
    )
    _executable(
        fake_bin / "tar",
        'printf "tar %s\\n" "$*" >> "$FAKE_LOG"\n'
        'destination=\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = -C ]; then shift; destination=$1; fi\n'
        '  shift\n'
        'done\n'
        ': > "$destination/gitleaks"\n',
    )
    env = {
        **os.environ,
        "FAKE_LOG": str(log),
        "GITHUB_PATH": str(github_path),
        "PATH": f"{fake_bin}:/bin:/usr/bin",
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
    }
    return env, log, github_path


def test_ci_bootstrap_pins_and_verifies_supported_gitleaks_archives() -> None:
    script = (ROOT / "scripts/bootstrap-ci-tools").read_text(encoding="utf-8")

    assert "version=8.30.1" in script
    for platform in ("Linux:x86_64", "Linux:aarch64", "Darwin:arm64", "Darwin:x86_64"):
        assert platform in script
    assert script.count("checksum=") == 4
    assert "sha256sum --check" in script
    assert "shasum -a 256 --check" in script
    assert "GITHUB_PATH is required" in script
    assert "curl --fail --location --silent --show-error" in script


@pytest.mark.parametrize(
    ("system", "machine", "archive", "checksum"),
    (
        ("Linux", "x86_64", "gitleaks_8.30.1_linux_x64.tar.gz", "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"),
        ("Linux", "aarch64", "gitleaks_8.30.1_linux_arm64.tar.gz", "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"),
        ("Darwin", "arm64", "gitleaks_8.30.1_darwin_arm64.tar.gz", "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"),
        ("Darwin", "x86_64", "gitleaks_8.30.1_darwin_x64.tar.gz", "dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709"),
    ),
)
def test_ci_bootstrap_selects_and_checks_exact_archive(
    tmp_path: Path, system: str, machine: str, archive: str, checksum: str
) -> None:
    env, log, github_path = _bootstrap_environment(tmp_path, system, machine)

    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="utf-8")
    assert f"/v8.30.1/{archive}" in commands
    assert f"{checksum}  {tmp_path / 'runner-temp/titanskies-tools' / archive}" in commands
    assert "tar -xzf" in commands
    assert github_path.read_text(encoding="utf-8").strip() == str(
        tmp_path / "runner-temp/titanskies-tools"
    )


def test_ci_bootstrap_fails_closed_before_extracting_bad_archive(tmp_path: Path) -> None:
    env, log, github_path = _bootstrap_environment(tmp_path, "Darwin", "arm64")
    env["FAKE_CHECKSUM_FAIL"] = "1"

    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    commands = log.read_text(encoding="utf-8")
    assert "sha256sum --check -" in commands
    assert "tar -xzf" not in commands
    assert "npx playwright install" not in commands
    assert not github_path.exists()


def test_ci_bootstrap_rejects_unsupported_runner_before_download(tmp_path: Path) -> None:
    env, log, _ = _bootstrap_environment(tmp_path, "Darwin", "mips64")

    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    assert "Unsupported Gitleaks bootstrap platform" in result.stderr
    assert "curl " not in log.read_text(encoding="utf-8")
