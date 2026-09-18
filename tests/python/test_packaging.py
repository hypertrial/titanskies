from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingest.context_contracts import CONTEXT_RASTER_BUDGET_BYTES


ROOT = Path(__file__).resolve().parents[2]


def test_container_services_share_one_hardened_image_and_local_port() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert dockerfile.startswith("# syntax=docker/dockerfile:1.7@sha256:")
    assert dockerfile.count("FROM node:22-bookworm-slim@sha256:") == 2
    assert dockerfile.count("node:22-bookworm-slim@sha256:") == 2
    assert "FROM ghcr.io/astral-sh/uv:0.8.22@sha256:" in dockerfile
    for line in (line for line in dockerfile.splitlines() if line.startswith("FROM ")):
        digest = line.split("@sha256:", 1)[1].split()[0]
        assert digest.isalnum() and len(digest) == 64
    assert "pip install" not in dockerfile
    assert "USER node" in dockerfile
    assert "image: ghcr.io/hypertrial/titanskies:${TITANSKIES_VERSION:-latest}" in compose
    assert compose.count("<<: *service") == 2
    assert "host_ip: ${TITANSKIES_BIND_ADDR:-127.0.0.1}" in compose
    assert "published: ${TITANSKIES_PORT:-8080}" in compose
    assert sum(line.strip().startswith("AIRNOW_API_KEY:") for line in compose.splitlines()) == 1
    assert "FRAME_RETENTION_HOURS: ${FRAME_RETENTION_HOURS:-48}" in compose
    assert "read_only: true" in compose
    assert "cap_drop: [ALL]" in compose
    assert 'security_opt: ["no-new-privileges:true"]' in compose
    assert "data:/var/lib/titanskies:ro" in compose


def test_native_distribution_assets_are_absent() -> None:
    removed = (
        "scripts/install-user",
        "scripts/update-user",
        "scripts/uninstall-user",
        "packaging/release-cosign.pub",
        "packaging/titanskies.desktop",
        "packaging/systemd/titanskies-web.service",
        "packaging/systemd/titanskies-ingest.service",
        "packaging/systemd/titanskies-ingest.timer",
    )
    assert all(not (ROOT / relative).exists() for relative in removed)


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
    for restricted in (
        "CC-BY-NC-4.0",
        "CC-BY-ND-4.0",
        "MIT-ish",
        "LicenseRef-Commercial",
        "PROPRIETARY",
        "UNKNOWN",
    ):
        assert not accepted(restricted)


def _release_check_fixture(tmp_path: Path) -> tuple[Path, list[str]]:
    root = tmp_path / "release-check"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/check_public_release.py", root / "scripts/check_public_release.py")
    shutil.copy2(ROOT / "package.json", root / "package.json")
    shutil.copytree(ROOT / "shared", root / "shared")
    shutil.copytree(ROOT / "ingest", root / "ingest")
    (root / "docs/releases").mkdir(parents=True)
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    shutil.copy2(ROOT / f"docs/releases/v{version}.md", root / f"docs/releases/v{version}.md")
    (root / "src/data").mkdir(parents=True)
    (root / "src/data/contextSchema.ts").write_text(
        'const retired = new Set(["firms", "hms"]);\n', encoding="utf-8",
    )
    shutil.copy2(ROOT / "Dockerfile", root / "Dockerfile")
    return root, [sys.executable, str(root / "scripts/check_public_release.py")]


def test_public_release_check_scans_runtime_hosts_and_pad_contract(tmp_path: Path) -> None:
    root, check = _release_check_fixture(tmp_path)
    assert subprocess.run(check, cwd=root, capture_output=True, text=True).returncode == 0

    added = root / "ingest/new_runtime_source.py"
    added.write_text('DATA_URL = "https://example.invalid/provider"\n', encoding="utf-8")
    unknown = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert unknown.returncode == 1
    assert "unregistered runtime data host" in unknown.stderr

    added.write_text('HELP_URL = "https://www.airnow.gov/"\n', encoding="utf-8")
    assert subprocess.run(check, cwd=root, capture_output=True, text=True).returncode == 0

    (root / ".pad.toml").write_text('workspace = "titanskies-engineering"\n', encoding="utf-8")
    (root / ".pad").mkdir()
    (root / ".pad/universal.lock.json").write_text(
        json.dumps(
            {
                "repository": "titanskies",
                "version": "1.1.0",
                "workspace": "titanskies-engineering",
            }
        ),
        encoding="utf-8",
    )
    assert subprocess.run(check, cwd=root, capture_output=True, text=True).returncode == 0

    (root / ".pad/universal.lock.json").write_text(
        json.dumps({"repository": "titanskies", "version": "1.1.0", "workspace": "wrong"}),
        encoding="utf-8",
    )
    wrong_workspace = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert wrong_workspace.returncode == 1
    assert "Universal Pad lock is invalid" in wrong_workspace.stderr


def test_public_release_check_rejects_returned_native_assets_and_instructions(tmp_path: Path) -> None:
    root, check = _release_check_fixture(tmp_path)
    retired_asset = root / "packaging/systemd/titanskies-web.service"
    retired_asset.parent.mkdir(parents=True)
    retired_asset.write_text("[Service]\n", encoding="utf-8")

    asset_result = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert asset_result.returncode == 1
    assert "retired native distribution asset is present" in asset_result.stderr

    retired_asset.unlink()
    (root / "README.md").write_text(
        "Install the native service with systemd.\n", encoding="utf-8"
    )
    instruction_result = subprocess.run(check, cwd=root, capture_output=True, text=True)
    assert instruction_result.returncode == 1
    assert "retired native distribution instruction in README.md" in instruction_result.stderr


def test_public_release_check_requires_current_release_notes(tmp_path: Path) -> None:
    root, check = _release_check_fixture(tmp_path)
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    (root / f"docs/releases/v{version}.md").unlink()

    result = subprocess.run(check, cwd=root, capture_output=True, text=True)

    assert result.returncode == 1
    assert f"release notes are missing for v{version}" in result.stderr


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
                return (datetime.fromisoformat(value.replace("Z", "+00:00")) + delta).isoformat().replace(
                    "+00:00", "Z"
                )
            except ValueError:
                return value
        return value

    def localize(value):
        if isinstance(value, dict):
            return {key: localize(child) for key, child in value.items()}
        if isinstance(value, list):
            return [localize(child) for child in value]
        return value.replace("/demo/context/assets/", "/data/context/assets/") if isinstance(value, str) else value

    manifest = localize(shift(manifest))
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
    pointer_path = tmp_path / "context/latest.json"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    status = {
        "version": 1,
        "outcome": "fresh",
        "contextVersion": 8,
        "lastAttemptAt": (now - timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
        "lastCompleteForecastAt": (now - timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
    }
    (tmp_path / "context/status.json").write_text(json.dumps(status), encoding="utf-8")
    return manifest_path, pointer_path, manifest, encoded


def test_publication_probe_checks_freshness_and_content_hashes(tmp_path: Path) -> None:
    manifest_path, pointer_path, manifest, encoded = _publication_for_probe(tmp_path)
    env = {**os.environ, "TITANSKIES_DATA_DIR": str(tmp_path), "CONTEXT_WATCH_SECONDS": "3600"}

    def probe() -> int:
        return subprocess.run(
            [sys.executable, ROOT / "scripts/check-publication.py"], cwd=ROOT, env=env, timeout=10
        ).returncode

    assert probe() == 0

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer_path.write_text(
        json.dumps({**pointer, "manifestUrl": "https://example.invalid/manifest.json"}), encoding="utf-8"
    )
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
