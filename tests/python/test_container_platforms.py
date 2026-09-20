from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify-container-platforms"


def test_container_verifiers_do_not_require_gnu_seq() -> None:
    helper = (ROOT / "scripts" / "lib" / "wait-for-health.sh").read_text(encoding="utf-8")
    assert "seq " not in helper
    assert 'while [ "$attempt" -lt 90 ]' in helper
    for name in ("verify-container", "verify-container-platforms"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "seq " not in source
        assert "scripts/lib/wait-for-health.sh" in source
        assert "wait_for_health" in source
        assert 'while [ "$attempt" -lt 90 ]' not in source


def test_platform_verifier_builds_and_health_checks_both_architectures(tmp_path: Path) -> None:
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$1 ${2:-}" in
  "buildx inspect") printf 'linux/arm64,linux/amd64\\n' ;;
  "image inspect")
    case "$3" in
      *arm64*) printf 'arm64\\n' ;;
      *amd64*) printf 'amd64\\n' ;;
    esac
    ;;
  "run --rm") exit 0 ;;
  "run --detach") printf 'container-id\\n' ;;
  "exec "*) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    for arch in ("arm64", "amd64"):
        assert f"buildx build --platform linux/{arch} --load" in calls
        assert f"run --rm --platform linux/{arch}" in calls
        assert f"run --detach --name titanskies-platform-web-{arch}-" in calls
        assert f"exec titanskies-platform-web-{arch}-" in calls
        assert f"rm --force titanskies-platform-web-{arch}-" in calls
        assert f"volume rm titanskies-platform-data-{arch}-" in calls
        assert f"volume rm titanskies-platform-cache-{arch}-" in calls
        assert f"image rm titanskies-platform-{arch}:verify-" in calls
    assert calls.index("buildx build --platform linux/arm64") < calls.index("buildx build --platform linux/amd64")


def test_platform_verifier_fails_when_builder_lacks_a_target(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
if [ "$1 ${2:-}" = "buildx inspect" ]; then
  printf 'linux/arm64\\n'
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}

    result = subprocess.run([str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "does not support linux/amd64" in result.stderr


def test_platform_verifier_reports_health_failure_and_cleans_every_resource(tmp_path: Path) -> None:
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$1 ${2:-}" in
  "buildx inspect") printf 'linux/arm64,linux/amd64\n' ;;
  "image inspect") printf 'arm64\n' ;;
  "run --detach") printf 'container-id\n' ;;
  "exec "*) exit 1 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    env = {
        **os.environ,
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    result = subprocess.run([str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "linux/arm64 health smoke failed" in result.stderr
    calls = log.read_text(encoding="utf-8")
    assert calls.count("exec titanskies-platform-web-arm64-") == 90
    assert "logs titanskies-platform-web-arm64-" in calls
    for resource in (
        "rm --force titanskies-platform-web-arm64-",
        "volume rm titanskies-platform-data-arm64-",
        "volume rm titanskies-platform-cache-arm64-",
        "image rm titanskies-platform-arm64:verify-",
    ):
        assert resource in calls
    assert "buildx build --platform linux/amd64" not in calls


def test_platform_verifier_rejects_loaded_image_architecture_and_cleans_image(tmp_path: Path) -> None:
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$1 ${2:-}" in
  "buildx inspect") printf 'linux/arm64,linux/amd64\n' ;;
  "image inspect") printf 'amd64\n' ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "DOCKER_LOG": str(log),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    result = subprocess.run([str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "expected arm64" in result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "image rm titanskies-platform-arm64:verify-" in calls
    assert "run --rm --platform linux/arm64" not in calls
