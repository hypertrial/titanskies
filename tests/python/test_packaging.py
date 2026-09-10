from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import shlex
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingest.context_contracts import CONTEXT_RASTER_BUDGET_BYTES

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
  case "$*" in *package.json*) echo 0.1.0 ;; *) echo 22 ;; esac
  exit 0
fi
if [ "${1:-}" = "-e" ]; then
  case "${2:-}" in *fetch*) exit "${FAKE_NODE_HEALTH_EXIT:-0}" ;; *) exit 0 ;; esac
fi
exit 0
""",
    )
    _executable(fake_bin / "python3", f'exec {shlex.quote(sys.executable)} "$@"\n')
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
    _executable(
        fake_bin / "systemctl",
        'printf "%s\\n" "$*" >> "$FAKE_COMMAND_LOG"\n'
        'if [ -n "${FAKE_INSTALL_SIGNAL:-}" ] && [ "$*" = "--user start titanskies-ingest.service" ]; then kill "-${FAKE_INSTALL_SIGNAL}" "$PPID"; exit 0; fi\n'
        'if [ "${FAKE_INGEST_EXIT:-0}" -ne 0 ] && [ "$*" = "--user start titanskies-ingest.service" ]; then exit "$FAKE_INGEST_EXIT"; fi\n'
        'if [ "${FAKE_SYSTEMCTL_EXIT:-0}" -ne 0 ]; then exit "$FAKE_SYSTEMCTL_EXIT"; fi\n'
        'if [ "$*" = "--user daemon-reload" ] && [ "${FAKE_DAEMON_RELOAD_EXIT:-0}" -ne 0 ]; then exit "$FAKE_DAEMON_RELOAD_EXIT"; fi\n'
        'case "$*" in "--user is-active "*) printf "%s\\n" "${FAKE_ACTIVE_STATE:-inactive}"; exit 0 ;; esac\n'
        "exit 0\n",
    )
    _executable(fake_bin / "sleep", "exit 0\n")
    _executable(
        fake_bin / "mv",
        'for destination do :; done\n'
        'case "$destination" in *.rollback) if [ "${FAKE_BACKUP_MOVE_EXIT:-0}" -ne 0 ]; then exit "$FAKE_BACKUP_MOVE_EXIT"; fi ;; esac\n'
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

    failed_ingest = subprocess.run(
        [ROOT / "scripts/install-user"],
        cwd=ROOT,
        env={**env, "FAKE_INGEST_EXIT": "1"},
        capture_output=True,
        text=True,
    )
    assert failed_ingest.returncode != 0
    assert current.readlink() == Path("releases/0.0.9")

    subprocess.run([ROOT / "scripts/uninstall-user"], cwd=ROOT, env=env, check=True)
    assert not install.exists()
    assert config.exists()
    assert (tmp_path / "state/titanskies").exists()
    assert (tmp_path / "cache/titanskies").exists()
    commands = log.read_text(encoding="utf-8")
    for unit in ("titanskies-web.service", "titanskies-ingest.service", "titanskies-ingest.timer"):
        assert f"--user is-active {unit}" in commands

    subprocess.run([ROOT / "scripts/uninstall-user", "--purge"], cwd=ROOT, env=env, check=True)
    assert not config.parent.exists()
    assert not (tmp_path / "state/titanskies").exists()
    assert not (tmp_path / "cache/titanskies").exists()


def test_failed_first_install_leaves_no_active_release(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    result = subprocess.run(
        [ROOT / "scripts/install-user"],
        cwd=ROOT,
        env={**env, "FAKE_INGEST_EXIT": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "data/titanskies/current").exists()


def test_same_version_reinstall_restores_the_previous_release_on_failure(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)
    install = tmp_path / "data/titanskies"
    marker = install / "releases/0.1.0/known-good"
    marker.write_text("keep", encoding="utf-8")

    failed = subprocess.run(
        [ROOT / "scripts/install-user"],
        cwd=ROOT,
        env={**env, "FAKE_NODE_HEALTH_EXIT": "1"},
        capture_output=True,
        text=True,
    )

    assert failed.returncode != 0
    assert (install / "current").readlink() == Path("releases/0.1.0")
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (install / "releases/.0.1.0.rollback").exists()

    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)
    assert not marker.exists()
    assert not (install / "releases/.0.1.0.rollback").exists()


def test_same_version_reinstall_preserves_release_when_backup_rename_fails(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)
    install = tmp_path / "data/titanskies"
    marker = install / "releases/0.1.0/known-good"
    marker.write_text("keep", encoding="utf-8")

    failed = subprocess.run(
        [ROOT / "scripts/install-user"],
        cwd=ROOT,
        env={**env, "FAKE_BACKUP_MOVE_EXIT": "1"},
        capture_output=True,
        text=True,
    )

    assert failed.returncode != 0
    assert (install / "current").readlink() == Path("releases/0.1.0")
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (install / "releases/.0.1.0.rollback").exists()


def test_install_and_uninstall_reject_unsafe_xdg_roots(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    root_alias = tmp_path / "root-alias"
    root_alias.symlink_to("/", target_is_directory=True)
    for value in ("/", "//", "/tmp/..", str(root_alias)):
        unsafe = {**env, "XDG_DATA_HOME": value}
        install = subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=unsafe, capture_output=True, text=True)
        uninstall = subprocess.run([ROOT / "scripts/uninstall-user", "--purge"], cwd=ROOT, env=unsafe, capture_output=True, text=True)
        assert install.returncode != 0, value
        assert uninstall.returncode != 0, value
        assert "root" in install.stderr
        assert "root" in uninstall.stderr


def test_installer_revalidates_xdg_path_after_resolving_symlinks(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    unsafe_target = tmp_path / "data&systemd-placeholder"
    unsafe_target.mkdir()
    alias = tmp_path / "data-alias"
    alias.symlink_to(unsafe_target, target_is_directory=True)

    result = subprocess.run(
        [ROOT / "scripts/install-user"],
        cwd=ROOT,
        env={**env, "XDG_DATA_HOME": str(alias)},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unsupported characters" in result.stderr
    assert not (unsafe_target / "titanskies/current").exists()


def test_failed_first_install_removes_activation_and_stops_web(tmp_path: Path) -> None:
    env, log = _fake_environment(tmp_path)
    result = subprocess.run(
        [ROOT / "scripts/install-user"],
        cwd=ROOT,
        env={**env, "FAKE_NODE_HEALTH_EXIT": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "data/titanskies/current").exists()
    commands = log.read_text(encoding="utf-8")
    assert "disable --now titanskies-web.service titanskies-ingest.timer" in commands
    assert "stop titanskies-ingest.service" in commands


def test_first_install_signals_roll_back_and_exit_nonzero(tmp_path: Path) -> None:
    for signal in ("HUP", "INT", "TERM"):
        case = tmp_path / signal.lower()
        case.mkdir()
        env, log = _fake_environment(case)

        result = subprocess.run(
            [ROOT / "scripts/install-user"],
            cwd=ROOT,
            env={**env, "FAKE_INSTALL_SIGNAL": signal},
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0, signal
        assert not (case / "data/titanskies/current").exists()
        commands = log.read_text(encoding="utf-8")
        assert "disable --now titanskies-web.service titanskies-ingest.timer" in commands
        assert "stop titanskies-ingest.service" in commands


def test_uninstall_rejects_symlinked_installation_directory(tmp_path: Path) -> None:
    env, log = _fake_environment(tmp_path)
    data_home = Path(env["XDG_DATA_HOME"])
    data_home.mkdir()
    external = tmp_path / "external"
    (external / "releases").mkdir(parents=True)
    marker = external / "releases/keep"
    marker.write_text("keep", encoding="utf-8")
    (data_home / "titanskies").symlink_to(external, target_is_directory=True)

    result = subprocess.run([ROOT / "scripts/uninstall-user"], cwd=ROOT, env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "must not be a symlink" in result.stderr
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not log.exists()


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


def test_uninstall_completes_when_systemd_user_manager_is_unavailable(tmp_path: Path) -> None:
    env, log = _fake_environment(tmp_path)
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)

    result = subprocess.run(
        [ROOT / "scripts/uninstall-user"],
        cwd=ROOT,
        env={**env, "FAKE_SYSTEMCTL_EXIT": "1"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert not (tmp_path / "data/titanskies").exists()
    assert (tmp_path / "config/titanskies/env").exists()
    assert "service shutdown could not be confirmed" in result.stderr
    commands = log.read_text(encoding="utf-8")
    assert "disable --now titanskies-web.service titanskies-ingest.timer" in commands
    assert "stop titanskies-ingest.service" in commands


def test_uninstall_preserves_files_when_a_service_remains_active(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)
    install = tmp_path / "data/titanskies"
    unit = tmp_path / "config/systemd/user/titanskies-web.service"

    result = subprocess.run(
        [ROOT / "scripts/uninstall-user"],
        cwd=ROOT,
        env={**env, "FAKE_ACTIVE_STATE": "active"},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "still active" in result.stderr
    assert (install / "current").exists()
    assert unit.exists()


def test_uninstall_warns_when_removed_units_cannot_be_reloaded(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)

    result = subprocess.run(
        [ROOT / "scripts/uninstall-user"],
        cwd=ROOT,
        env={**env, "FAKE_DAEMON_RELOAD_EXIT": "1"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "could not reload removed unit files" in result.stderr
    assert not (tmp_path / "data/titanskies").exists()


def test_installer_accepts_a_quoted_source_checkout_path(tmp_path: Path) -> None:
    source = tmp_path / "release o'connor $safe"
    (source / "scripts").mkdir(parents=True)
    (source / "app").mkdir()
    (source / "public").mkdir()
    shutil.copy2(ROOT / "scripts/install-user", source / "scripts/install-user")
    shutil.copy2(ROOT / "package.json", source / "package.json")
    shutil.copy2(ROOT / ".env.example", source / ".env.example")
    shutil.copytree(ROOT / "packaging", source / "packaging")
    shutil.copy2(ROOT / "app/icon.png", source / "app/icon.png")
    environment = tmp_path / "environment"
    environment.mkdir()
    env, _ = _fake_environment(environment)

    result = subprocess.run([source / "scripts/install-user"], cwd=source, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert (environment / "data/titanskies/current").readlink() == Path("releases/0.1.0")


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
    assert dockerfile.startswith("# syntax=docker/dockerfile:1.7@sha256:")
    assert dockerfile.count("FROM node:22-bookworm-slim@sha256:") == 2
    assert dockerfile.count("node:22-bookworm-slim@sha256:") == 2
    assert "FROM ghcr.io/astral-sh/uv:0.8.22@sha256:" in dockerfile
    for line in (line for line in dockerfile.splitlines() if line.startswith("FROM ")):
        assert line.split("@sha256:", 1)[1].split()[0].isalnum()
        assert len(line.split("@sha256:", 1)[1].split()[0]) == 64
    assert "pip install" not in dockerfile
    assert "USER node" in dockerfile
    assert "image: ghcr.io/hypertrial/titanskies:" in compose
    assert compose.count("<<: *service") == 2
    assert "host_ip: ${TITANSKIES_BIND_ADDR:-127.0.0.1}" in compose
    assert "published: ${TITANSKIES_PORT:-8080}" in compose
    assert sum(line.strip().startswith("AIRNOW_API_KEY:") for line in compose.splitlines()) == 1
    assert "FRAME_RETENTION_HOURS: ${FRAME_RETENTION_HOURS:-48}" in compose
    assert "read_only: true" in compose
    assert "cap_drop: [ALL]" in compose
    assert 'security_opt: ["no-new-privileges:true"]' in compose
    assert "data:/var/lib/titanskies:ro" in compose


def test_installer_applies_documented_runtime_configuration(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    config = tmp_path / "config/titanskies/env"
    config.parent.mkdir(parents=True)
    configured_data = tmp_path / "publications"
    configured_cache = tmp_path / "model-cache"
    config.write_text(
        f"TITANSKIES_DATA_DIR={configured_data}\n"
        f"TITANSKIES_CACHE_DIR={configured_cache}\n"
        "TITANSKIES_BIND_ADDR=0.0.0.0\n"
        "TITANSKIES_PORT=9090\n",
        encoding="utf-8",
    )

    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)

    web = (tmp_path / "config/systemd/user/titanskies-web.service").read_text(encoding="utf-8")
    ingest = (tmp_path / "config/systemd/user/titanskies-ingest.service").read_text(encoding="utf-8")
    desktop = (tmp_path / "data/applications/titanskies.desktop").read_text(encoding="utf-8")
    assert f'ReadOnlyPaths="{configured_data}"' in web
    assert f'ReadWritePaths="{configured_data}" "{configured_cache}"' in ingest
    assert "http://127.0.0.1:9090" in desktop
    assert configured_data.is_dir()
    assert configured_cache.is_dir()


def test_installer_rejects_invalid_runtime_configuration_before_activation(tmp_path: Path) -> None:
    cases = (
        "TITANSKIES_PORT=0\n",
        "TITANSKIES_PORT=65536\n",
        "TITANSKIES_PORT=eighty\n",
        "TITANSKIES_BIND_ADDR=192.0.2.1\n",
        "TITANSKIES_DATA_DIR=relative/path\n",
        'TITANSKIES_DATA_DIR=/tmp/data" "/tmp/extra\n',
        "TITANSKIES_DATA_DIR=//\n",
        "TITANSKIES_DATA_DIR=/tmp/..\n",
    )
    for index, config_text in enumerate(cases):
        case = tmp_path / str(index)
        case.mkdir()
        env, _ = _fake_environment(case)
        config = case / "config/titanskies/env"
        config.parent.mkdir(parents=True)
        config.write_text(config_text, encoding="utf-8")
        result = subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode != 0, config_text
        assert not (case / "data/titanskies/current").exists()


def test_installer_rejects_publications_or_cache_inside_versioned_releases(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    install_dir = tmp_path / "data/titanskies"
    paths = (
        install_dir / "releases",
        install_dir / "releases/publications",
        install_dir / "current",
        install_dir / "current/cache",
    )
    for index, configured in enumerate(paths):
        config = tmp_path / "config/titanskies/env"
        config.parent.mkdir(parents=True, exist_ok=True)
        key = "TITANSKIES_DATA_DIR" if index % 2 == 0 else "TITANSKIES_CACHE_DIR"
        config.write_text(f"{key}={configured}\n", encoding="utf-8")
        result = subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode != 0, configured
        assert "outside the versioned release directory" in result.stderr
        assert not (install_dir / "current").exists()


def test_installer_rejects_runtime_symlink_alias_into_versioned_releases(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    install_dir = tmp_path / "data/titanskies"
    forbidden = install_dir / "releases/0.0.9/publications"
    forbidden.mkdir(parents=True)
    alias = tmp_path / "publication-alias"
    alias.symlink_to(forbidden, target_is_directory=True)
    config = tmp_path / "config/titanskies/env"
    config.parent.mkdir(parents=True)
    config.write_text(f"TITANSKIES_DATA_DIR={alias}\n", encoding="utf-8")

    result = subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "outside the versioned release directory" in result.stderr
    assert not (install_dir / "current").exists()


def test_installer_revalidates_runtime_path_after_resolving_symlinks(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    unsafe_target = tmp_path / "publications&systemd-placeholder"
    unsafe_target.mkdir()
    alias = tmp_path / "publication-alias"
    alias.symlink_to(unsafe_target, target_is_directory=True)
    config = tmp_path / "config/titanskies/env"
    config.parent.mkdir(parents=True)
    config.write_text(f"TITANSKIES_DATA_DIR={alias}\n", encoding="utf-8")

    result = subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "unsupported characters" in result.stderr
    assert not (tmp_path / "data/titanskies/current").exists()


def test_normal_uninstall_preserves_state_when_xdg_roots_overlap(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    shared = tmp_path / "shared"
    env.update({"XDG_DATA_HOME": str(shared), "XDG_STATE_HOME": str(shared)})
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)
    marker = shared / "titanskies/operator-data"
    marker.write_text("keep", encoding="utf-8")

    subprocess.run([ROOT / "scripts/uninstall-user"], cwd=ROOT, env=env, check=True)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (shared / "titanskies/releases").exists()


def test_uninstall_preserves_custom_data_and_cache_even_with_purge(tmp_path: Path) -> None:
    env, _ = _fake_environment(tmp_path)
    custom_data = tmp_path / "custom-data"
    custom_cache = tmp_path / "custom-cache"
    config = tmp_path / "config/titanskies/env"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"TITANSKIES_DATA_DIR={custom_data}\nTITANSKIES_CACHE_DIR={custom_cache}\n",
        encoding="utf-8",
    )
    subprocess.run([ROOT / "scripts/install-user"], cwd=ROOT, env=env, check=True)
    (custom_data / "keep").write_text("data", encoding="utf-8")
    (custom_cache / "keep").write_text("cache", encoding="utf-8")

    result = subprocess.run([ROOT / "scripts/uninstall-user", "--purge"], cwd=ROOT, env=env, check=True, capture_output=True, text=True)

    assert (custom_data / "keep").read_text(encoding="utf-8") == "data"
    assert (custom_cache / "keep").read_text(encoding="utf-8") == "cache"
    assert "Custom data or cache paths are preserved" in result.stdout


def test_persistent_timer_uses_calendar_schedule() -> None:
    timer = (ROOT / "packaging/systemd/titanskies-ingest.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*:0/15" in timer
    assert "Persistent=true" in timer
    assert "OnUnitActiveSec" not in timer


def test_license_report_includes_every_locked_python_variant(tmp_path: Path) -> None:
    for name in ("package-lock.json", "uv.lock"):
        shutil.copy2(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / "shared", tmp_path / "shared")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts/license_report.py", scripts / "license_report.py")

    subprocess.run([ROOT / "scripts/run_python.sh", scripts / "license_report.py"], cwd=ROOT, check=True)

    report = json.loads((tmp_path / "artifacts/license-report.json").read_text(encoding="utf-8"))
    locked = {(item["name"], item["version"]) for item in report["python"]}
    assert ("numpy", "2.4.6") in locked
    assert ("numpy", "2.5.3") in locked
    assert ("tifffile", "2026.3.3") in locked
    assert ("tifffile", "2026.9.9") in locked
    accepted = runpy.run_path(str(scripts / "license_report.py"))["accepted"]
    assert accepted("Apache-2.0 AND LGPL-3.0-or-later AND MIT")
    assert accepted("Apache-2.0 OR BSD-2-Clause")
    assert accepted("Public Domain / CC0-1.0")
    for restricted in ("CC-BY-NC-4.0", "CC-BY-ND-4.0", "MIT-ish", "LicenseRef-Commercial", "PROPRIETARY", "UNKNOWN"):
        assert not accepted(restricted)


def test_public_release_check_scans_all_runtime_hosts(tmp_path: Path) -> None:
    root = tmp_path / "release-check"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/check_public_release.py", root / "scripts/check_public_release.py")
    shutil.copytree(ROOT / "shared", root / "shared")
    shutil.copytree(ROOT / "ingest", root / "ingest")
    shutil.copytree(ROOT / ".github", root / ".github")
    shutil.copy2(ROOT / "Dockerfile", root / "Dockerfile")
    check = [sys.executable, str(root / "scripts/check_public_release.py")]
    assert subprocess.run(check, cwd=root, capture_output=True, text=True).returncode == 0

    (root / ".pad.toml").write_text('workspace = "titanskies-smoke-engineering"\n', encoding="utf-8")
    assert subprocess.run(check, cwd=root, capture_output=True, text=True).returncode == 0
    (root / ".pad").mkdir()
    (root / ".pad" / "items.json").write_text("{}", encoding="utf-8")
    private_pad = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert private_pad.returncode == 1
    assert "private Pad metadata is present" in private_pad.stderr
    shutil.rmtree(root / ".pad")

    added = root / "ingest/new_runtime_source.py"
    added.write_text('DATA_URL = "https://example.invalid/provider"\n', encoding="utf-8")
    unknown = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert unknown.returncode == 1
    assert "unregistered runtime data host" in unknown.stderr

    added.write_text(
        'HELP_URL = "https://www.airnow.gov/"\nAIRNOW_HELP_HOSTS = frozenset({"www.airnow.gov"})\n',
        encoding="utf-8",
    )
    documentation_endpoint = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert documentation_endpoint.returncode == 1
    assert "non-endpoint host" in documentation_endpoint.stderr

    added.write_text('HELP_URL = "https://www.airnow.gov/"\n', encoding="utf-8")
    assert subprocess.run(check, cwd=root, capture_output=True, text=True).returncode == 0

    added.write_text('import requests\nrequests.get("https://www.airnow.gov/data")\n', encoding="utf-8")
    direct_client = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert direct_client.returncode == 1
    assert "direct network client import" in direct_client.stderr


def _publication_for_probe(tmp_path: Path) -> tuple[Path, Path, dict, bytes]:
    source_root = ROOT / "public/demo"
    source_pointer = json.loads((source_root / "context/latest.json").read_text(encoding="utf-8"))
    shutil.copytree(source_root / "context/assets", tmp_path / "context/assets")
    manifest = json.loads((source_root / source_pointer["manifestPath"]).read_text(encoding="utf-8"))
    generated = datetime.fromisoformat(manifest["generatedAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    target = now.replace(minute=0, second=0, microsecond=0)
    delta = target - generated

    def shift(value):
        if isinstance(value, dict):
            return {key: shift(child) for key, child in value.items()}
        if isinstance(value, list):
            return [shift(child) for child in value]
        if isinstance(value, str) and value.endswith("Z"):
            try:
                return (datetime.fromisoformat(value.replace("Z", "+00:00")) + delta).isoformat().replace("+00:00", "Z")
            except ValueError:
                return value
        return value

    manifest = shift(manifest)

    def localize(value):
        if isinstance(value, dict):
            return {key: localize(child) for key, child in value.items()}
        if isinstance(value, list):
            return [localize(child) for child in value]
        return value.replace("/demo/context/assets/", "/data/context/assets/") if isinstance(value, str) else value

    manifest = localize(manifest)
    encoded = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(encoded).hexdigest()[:20]
    manifest_path = tmp_path / f"context/manifests/{digest}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(encoded)
    pointer = {
        "version": 8,
        "manifestPath": f"context/manifests/{digest}.json",
        "manifestUrl": f"/data/context/manifests/{digest}.json",
        "updatedAt": target.isoformat().replace("+00:00", "Z"),
    }
    (tmp_path / "context/latest.json").write_text(json.dumps(pointer), encoding="utf-8")
    status = {
        "version": 1,
        "outcome": "fresh",
        "contextVersion": 8,
        "lastAttemptAt": (now - timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
        "lastCompleteForecastAt": (now - timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
    }
    (tmp_path / "context/status.json").write_text(json.dumps(status), encoding="utf-8")
    return manifest_path, tmp_path / "context/latest.json", manifest, encoded


def test_publication_probe_checks_configured_freshness_and_all_content_hashes(tmp_path: Path) -> None:
    manifest_path, pointer_path, manifest, encoded = _publication_for_probe(tmp_path)
    env = {**os.environ, "TITANSKIES_DATA_DIR": str(tmp_path), "CONTEXT_WATCH_SECONDS": "3600"}

    def probe() -> int:
        return subprocess.run(
            [sys.executable, ROOT / "scripts/check-publication.py"],
            cwd=ROOT,
            env=env,
            timeout=10,
        ).returncode

    assert probe() == 0

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer_path.write_text(json.dumps({**pointer, "manifestUrl": "https://example.invalid/manifest.json"}), encoding="utf-8")
    assert probe() == 1
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    manifest_path.write_bytes(encoded + b"\n")
    assert probe() == 1
    manifest_path.write_bytes(encoded)

    asset = tmp_path / manifest["forecast"]["legendUrl"].removeprefix("/data/")
    original_asset = asset.read_bytes()
    asset.write_bytes(b"corrupt")
    assert probe() == 1

    asset.unlink()
    asset.symlink_to("/etc/hosts")
    assert probe() == 1

    asset.unlink()
    os.mkfifo(asset)
    assert probe() == 1

    asset.unlink()
    asset.write_bytes(b"x" * (CONTEXT_RASTER_BUDGET_BYTES + 1))
    assert probe() == 1

    asset.write_bytes(original_asset)
    assert probe() == 0

    aqhi_asset = tmp_path / manifest["air"]["monitorSets"]["aqhi"]["url"].removeprefix("/data/")
    aqhi_bytes = aqhi_asset.read_bytes()
    aqhi_asset.unlink()
    assert probe() == 1
    aqhi_asset.write_bytes(aqhi_bytes)
    assert probe() == 0


def test_workflows_pin_every_third_party_action_to_a_full_commit() -> None:
    for workflow in (ROOT / ".github/workflows/ci.yml", ROOT / ".github/workflows/release.yml"):
        uses = [line.split("@", 1)[1].split()[0] for line in workflow.read_text(encoding="utf-8").splitlines() if "uses:" in line]
        assert uses
        assert all(len(revision) == 40 and all(character in "0123456789abcdef" for character in revision) for revision in uses)


def test_release_verification_isolated_from_publish_credentials() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    verify = workflow[workflow.index("  verify:\n"):workflow.index("  image:\n")]
    image = workflow[workflow.index("  image:\n"):workflow.index("  publish:\n")]
    publish = workflow[workflow.index("  publish:\n"):]

    assert "contents: read" in verify
    assert "persist-credentials: false" in verify
    assert "id-token: write" not in verify
    assert "contents: write" not in verify
    version_check = "test \"$(node -p 'require(\"./package.json\").version')\" = \"$version\""
    assert version_check in verify
    assert verify.index(version_check) < verify.index("npm ci")
    assert "needs: verify" in image
    assert "id-token: write" not in image
    assert "persist-credentials: false" in image
    assert "id-token: write" in publish
    assert "persist-credentials: false" in publish
    assert "npm ci" not in publish
    assert "uv sync" not in publish
    assert "verify_release.sh" not in publish
    assert workflow.count("persist-credentials: false") == 3
