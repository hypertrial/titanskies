from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _fake_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _executable(
        fake_bin / "node",
        """if [ "${1:-}" = "-p" ]; then
  case "${2:-}" in *package.json*) echo 0.1.0 ;; *) echo 22 ;; esac
  exit 0
fi
if [ "${1:-}" = "-e" ]; then
  case "${2:-}" in *fetch*) exit "${FAKE_NODE_HEALTH_EXIT:-0}" ;; *) exit 0 ;; esac
fi
exit 0
""",
    )
    _executable(fake_bin / "python3", "exit 0\n")
    _executable(fake_bin / "uv", "exit 0\n")
    _executable(
        fake_bin / "npm",
        """if [ "${1:-}" = "run" ] && [ "${2:-}" = "build" ]; then
  mkdir -p .next/standalone/.next .next/static
  : > .next/standalone/server.js
fi
exit 0
""",
    )
    _executable(fake_bin / "systemctl", 'printf "%s\\n" "$*" >> "$FAKE_COMMAND_LOG"\nexit 0\n')
    _executable(fake_bin / "sleep", "exit 0\n")
    _executable(
        fake_bin / "mv",
        'if [ "${1:-}" = "-Tf" ]; then exec /bin/mv -f "$2" "$3"; fi\nexec /bin/mv "$@"\n',
    )
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_COMMAND_LOG": str(log),
    }
    return env, log


def test_user_install_rollback_and_uninstall(tmp_path: Path) -> None:
    env, log = _fake_environment(tmp_path)
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)

    install = tmp_path / "data/titanskies"
    current = install / "current"
    assert current.readlink() == Path("releases/0.1.0")
    assert (current / ".next/standalone/server.js").is_file()
    config = tmp_path / "config/titanskies/env"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert f"TITANSKIES_DATA_DIR={tmp_path / 'state/titanskies'}" in config.read_text(encoding="utf-8")
    assert "@CURRENT@" not in (tmp_path / "config/systemd/user/titanskies-web.service").read_text(encoding="utf-8")
    commands = log.read_text(encoding="utf-8")
    assert "enable titanskies-web.service titanskies-ingest.timer" in commands
    assert "restart titanskies-web.service" in commands
    assert "start titanskies-ingest.timer" in commands
    assert "start titanskies-ingest.service" in commands

    previous = install / "releases/0.0.9"
    previous.mkdir()
    current.unlink()
    current.symlink_to("releases/0.0.9")
    failed = subprocess.run(
        [ROOT / "scripts/install-user"],
        cwd=ROOT,
        env={**env, "FAKE_NODE_HEALTH_EXIT": "1"},
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert current.readlink() == Path("releases/0.0.9")
    assert "previous release was restored" in failed.stderr

    subprocess.run([ROOT / "scripts/uninstall-user"], cwd=ROOT, env=env, check=True)
    assert not install.exists()
    assert config.exists()
    assert (tmp_path / "state/titanskies").exists()
    assert (tmp_path / "cache/titanskies").exists()

    subprocess.run([ROOT / "scripts/uninstall-user", "--purge"], cwd=ROOT, env=env, check=True)
    assert not config.parent.exists()
    assert not (tmp_path / "state/titanskies").exists()
    assert not (tmp_path / "cache/titanskies").exists()


def test_install_and_uninstall_reject_unsafe_xdg_roots(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    unsafe = {**env, "XDG_DATA_HOME": "/"}
    install = subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=unsafe, capture_output=True, text=True)
    uninstall = subprocess.run([ROOT / "scripts/uninstall-user", "--purge"], cwd=ROOT, env=unsafe, capture_output=True, text=True)
    assert install.returncode != 0
    assert uninstall.returncode != 0
    assert "non-root absolute path" in install.stderr
    assert "non-root absolute path" in uninstall.stderr


def test_uninstall_rejects_unknown_arguments(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    result = subprocess.run(
        [ROOT / "scripts/uninstall-user", "--delete-everything"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not (tmp_path / "data/titanskies").exists()


def test_signed_update_verifies_then_invokes_release_installer(tmp_path: Path) -> None:
    env, log = _fake_environment(tmp_path)
    marker = tmp_path / "updated"
    _executable(env_path := Path(env["PATH"].split(":", 1)[0]) / "cosign", 'printf "%s\\n" "$*" >> "$FAKE_COMMAND_LOG"\n')
    assert env_path.is_file()

    release = tmp_path / "release/titanskies-0.1.0/scripts"
    release.mkdir(parents=True)
    _executable(release / "install-user", ': > "$FAKE_UPDATE_MARKER"\n')
    archive = tmp_path / "titanskies-0.1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(release.parents[1], arcname="titanskies-0.1.0")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    signature = tmp_path / "SHA256SUMS.sig"
    certificate = tmp_path / "SHA256SUMS.pem"
    signature.write_text("synthetic signature\n", encoding="utf-8")
    certificate.write_text("synthetic certificate\n", encoding="utf-8")

    subprocess.run(
        [ROOT / "scripts/update-user", archive, checksums, signature, certificate],
        cwd=ROOT,
        env={**env, "FAKE_UPDATE_MARKER": str(marker)},
        check=True,
    )
    assert marker.is_file()
    verification = log.read_text(encoding="utf-8")
    assert "verify-blob" in verification
    assert "github\\.com/hypertrial/titanskies" in verification


def test_signed_update_binds_checksum_to_exact_archive_name(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    marker = tmp_path / "updated"
    fake_bin = Path(env["PATH"].split(":", 1)[0])
    _executable(fake_bin / "cosign", "exit 0\n")

    legitimate = tmp_path / "titanskies-0.1.0.tar.gz"
    legitimate.write_bytes(b"signed release")
    digest = hashlib.sha256(legitimate.read_bytes()).hexdigest()
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text(f"{digest}  {legitimate.name}\n", encoding="utf-8")

    release = tmp_path / "malicious/titanskies-0.1.0/scripts"
    release.mkdir(parents=True)
    _executable(release / "install-user", ': > "$FAKE_UPDATE_MARKER"\n')
    crafted = tmp_path / "titanskies-0.1.0.tar.g.*"
    with tarfile.open(crafted, "w:gz") as bundle:
        bundle.add(release.parents[1], arcname="titanskies-0.1.0")

    signature = tmp_path / "SHA256SUMS.sig"
    certificate = tmp_path / "SHA256SUMS.pem"
    signature.write_text("synthetic signature\n", encoding="utf-8")
    certificate.write_text("synthetic certificate\n", encoding="utf-8")
    result = subprocess.run(
        [ROOT / "scripts/update-user", crafted, checksums, signature, certificate],
        cwd=ROOT,
        env={**env, "FAKE_UPDATE_MARKER": str(marker)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unique checksum entry" in result.stderr
    assert not marker.exists()


def test_container_services_share_one_hardened_image_and_local_port() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "FROM node:22-bookworm-slim AS build" in dockerfile
    assert "FROM node:22-bookworm-slim AS runtime" in dockerfile
    assert "USER node" in dockerfile
    assert "image: ghcr.io/hypertrial/titanskies:" in compose
    assert compose.count("<<: *service") == 2
    assert '"127.0.0.1:${TITANSKIES_PORT:-8080}:8080"' in compose
    assert "read_only: true" in compose
    assert "cap_drop: [ALL]" in compose
    assert 'security_opt: ["no-new-privileges:true"]' in compose
    assert "data:/var/lib/titanskies:ro" in compose
