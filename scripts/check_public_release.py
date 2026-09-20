#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "shared/data-sources.json").read_text(encoding="utf-8"))
CONTRACT = json.loads((ROOT / "shared/context-contract-v8.json").read_text(encoding="utf-8"))
EXPECTED = {"firework", "hrrr", "airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis"}
RETIRED = {"tempo", "hms", "firms"}
RETIRED_COMPATIBILITY = {
    "src/data/contextSchema.ts": 'new set(["firms", "hms"])',
}
RETIRED_HOSTS = {
    "cmr.earthdata.nasa.gov",
    "firms.modaps.eosdis.nasa.gov",
    "satepsanone.nesdis.noaa.gov",
    "www.star.nesdis.noaa.gov",
}
URL = re.compile(r"https://[^\"'\s)]+")
ACTION = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
RUNTIME_METADATA_URLS = {
    "https://github.com/hypertrial/titanskies",
    "https://vercel.com/api/blob",
}
NETWORK_CLIENT_MODULES = {"aiohttp", "http.client", "httpx", "requests", "urllib.request"}
VERCEL_ADAPTER_FILES = {
    "app/api/context-data/route.ts",
    "app/api/context-health/route.ts",
    "ingest/auth.py",
    "ingest/blob_store.py",
    "ingest/config.py",
    "scripts/check_public_release.py",
    "src/server/blobData.ts",
    "vercel.json",
}
REMOVED_NATIVE_PATHS = {
    "packaging/release-cosign.pub",
    "packaging/systemd/titanskies-ingest.service",
    "packaging/systemd/titanskies-ingest.timer",
    "packaging/systemd/titanskies-web.service",
    "packaging/titanskies.desktop",
    "scripts/install-user",
    "scripts/uninstall-user",
    "scripts/update-user",
}
USER_FACING_DISTRIBUTION_DOCS = {
    ".env.example",
    "PRODUCT.md",
    "PROJECT_AGENT.md",
    "README.md",
    "SECURITY.md",
    "docs/OPERATIONS.md",
    "docs/RELEASE.md",
    *(path.relative_to(ROOT).as_posix() for path in (ROOT / "docs/releases").glob("v*.md")),
}
RETIRED_NATIVE_INSTRUCTION = re.compile(r"(?i)\b(?:omarchy|systemd|install-user|update-user|uninstall-user|release-cosign|cosign_key)\b")


def fail(message: str) -> NoReturn:
    print(f"public-release check: {message}", file=sys.stderr)
    raise SystemExit(1)


def _ingest_version() -> str:
    source = (ROOT / "ingest/__init__.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source, filename="ingest/__init__.py")):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    fail("ingest.__version__ is missing")


pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
pyproject_version = pyproject.get("project", {}).get("version")
ingest_version = _ingest_version()
if PACKAGE.get("version") != pyproject_version or PACKAGE.get("version") != ingest_version:
    fail(
        "package.json, pyproject.toml, and ingest.__version__ must match "
        f"({PACKAGE.get('version')!r}, {pyproject_version!r}, {ingest_version!r})"
    )

sources = REGISTRY.get("sources")
if not isinstance(sources, list) or {item.get("id") for item in sources} != EXPECTED:
    fail("data registry must contain exactly the eight v8 providers")
for item in sources:
    for key in ("owner", "attribution", "termsUrl", "dataTerms", "credentials", "transformations", "lastReviewed"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            fail(f"{item.get('id')} is missing {key}")
    if not isinstance(item.get("freshnessHours"), int) or item["freshnessHours"] <= 0:
        fail(f"{item.get('id')} has invalid freshnessHours")
    if item["freshnessHours"] != CONTRACT["staleAfterHours"].get(item["id"]):
        fail(f"{item.get('id')} freshnessHours differs from the v8 runtime contract")
    if not isinstance(item.get("endpointHosts"), list) or not item["endpointHosts"]:
        fail(f"{item.get('id')} has no endpoint host allowlist")

endpoint_hosts = {host for item in sources for host in item.get("endpointHosts", [])}
registered_hosts = {host for item in sources for key in ("endpointHosts", "documentationHosts") for host in item.get(key, [])}
runtime_files = sorted((ROOT / "ingest").rglob("*.py"))
runtime_host_registries: list[set[str]] = []
for path in runtime_files:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for raw in URL.findall(source):
        normalized = raw.rstrip(".,")
        if normalized in RUNTIME_METADATA_URLS:
            continue
        host = urlparse(normalized).hostname
        if host in RETIRED_HOSTS:
            fail(f"retired data host {host} in {path.relative_to(ROOT)}")
        if host not in registered_hosts:
            fail(f"unregistered runtime data host {host} in {path.relative_to(ROOT)}")
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = [f"{node.module}.{alias.name}" for alias in node.names]
        if path not in {ROOT / "ingest/http.py", ROOT / "ingest/blob_store.py"} and any(
            name == module or name.startswith(module + ".") for name in imported for module in NETWORK_CLIENT_MODULES
        ):
            fail(f"direct network client import in {path.relative_to(ROOT)}")
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if node.value is None:
            continue
        if any(isinstance(target, ast.Name) and target.id == "REGISTERED_ENDPOINT_HOSTS" for target in targets):
            runtime_host_registries.append(
                {value.value for value in ast.walk(node.value) if isinstance(value, ast.Constant) and isinstance(value.value, str)}
            )
        if not any(isinstance(target, ast.Name) and target.id.endswith("HOSTS") for target in targets):
            continue
        for value in ast.walk(node.value):
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and "." in value.value
                and value.value not in endpoint_hosts
            ):
                fail(f"non-endpoint host {value.value} in {path.relative_to(ROOT)}")
if runtime_host_registries != [endpoint_hosts]:
    fail("shared HTTP endpoint hosts differ from the source registry")

for path in (ROOT / "ingest").rglob("*.py"):
    text = path.read_text(encoding="utf-8").lower()
    for host in RETIRED_HOSTS:
        if host in text:
            fail(f"retired data host {host} in {path.relative_to(ROOT)}")

try:
    tracked_output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, stderr=subprocess.DEVNULL)
    tracked = set(tracked_output.decode().rstrip("\0").split("\0"))
    release_output = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        stderr=subprocess.DEVNULL,
    )
    release_files = set(release_output.decode().rstrip("\0").split("\0"))
except (FileNotFoundError, subprocess.CalledProcessError):
    tracked = {path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*") if path.is_file()}
    release_files = tracked
if any(".vercel" in Path(relative).parts for relative in tracked):
    fail("Vercel local project state is tracked")
if any(Path(relative).name.startswith(".env") and Path(relative).name != ".env.example" for relative in tracked):
    fail("a local environment file is tracked")
if any(relative.startswith((".local/", "artifacts/", "test-results/", "playwright-report/")) for relative in tracked):
    fail("generated or fetched deployment data is tracked")
returned_native_paths = sorted(path for path in REMOVED_NATIVE_PATHS if (ROOT / path).exists())
if returned_native_paths:
    fail(f"retired native distribution asset is present: {returned_native_paths[0]}")
current_release_notes = ROOT / f"docs/releases/v{PACKAGE['version']}.md"
if not current_release_notes.is_file():
    fail(f"release notes are missing for v{PACKAGE['version']}")
for relative in sorted(USER_FACING_DISTRIBUTION_DOCS):
    path = ROOT / relative
    if path.is_file() and RETIRED_NATIVE_INSTRUCTION.search(path.read_text(encoding="utf-8", errors="ignore")):
        fail(f"retired native distribution instruction in {relative}")

for relative in sorted(release_files):
    path = ROOT / relative
    if not path.is_file() or any(
        part
        in {
            ".git",
            ".venv",
            "node_modules",
            ".next",
            "public",
            "tests",
            "artifacts",
            "test-results",
            "playwright-report",
            ".local",
            "dist",
            ".build",
            ".build-icon",
            ".swiftpm",
        }
        for part in path.parts
    ):
        continue
    if ".test." in path.name:
        continue
    if relative in {"scripts/check_public_release.py", "DATA_SOURCES.md", "PRODUCT.md", "README.md"}:
        continue
    if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml", ".sh"}:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    compatibility = RETIRED_COMPATIBILITY.get(relative)
    if compatibility:
        if text.count(compatibility) != 1:
            fail(f"retired compatibility allowlist changed in {relative}")
        text = text.replace(compatibility, "")
    if any(re.search(rf"(?<![a-z0-9]){term}(?![a-z0-9])", text) for term in RETIRED):
        fail(f"retired integration reference in {relative}")
    if ("@vercel/" in text or "vercel-storage.com" in text or "blob_read_write_token" in text) and relative not in VERCEL_ADAPTER_FILES:
        fail(f"proprietary deployment reference in {relative}")

pad_directory = ROOT / ".pad"
if pad_directory.exists():
    pad_files = {path.relative_to(pad_directory).as_posix() for path in pad_directory.rglob("*") if path.is_file()}
    if pad_files != {"universal.lock.json"}:
        fail("private Pad metadata is present")
    lock = json.loads((pad_directory / "universal.lock.json").read_text(encoding="utf-8"))
    allowed_lock_keys = {"repository", "version", "workspace", "files"}
    if set(lock) - allowed_lock_keys or lock.get("repository") != "titanskies" or not isinstance(lock.get("version"), str):
        fail("Universal Pad lock is invalid")
    if "workspace" in lock and lock["workspace"] != "titanskies-engineering":
        fail("Universal Pad lock is invalid")
    if "files" in lock:
        expected_files = {
            ".agents/skills/pad-engineering/SKILL.md",
            "AGENTS.md",
            "PROJECT_AGENT.md",
            "scripts/verify",
            "scripts/verify-fast",
        }
        files = lock["files"]
        if (
            not isinstance(files, dict)
            or set(files) != expected_files
            or any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in files.values())
        ):
            fail("Universal Pad lock is invalid")


def _require_pinned_actions(directory: Path, pattern: str) -> None:
    if not directory.is_dir():
        return
    for path in directory.rglob(pattern):
        for action in ACTION.findall(path.read_text(encoding="utf-8")):
            if action.startswith("./"):
                continue
            if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", action):
                fail(f"workflow action is not pinned to a commit: {action}")


_require_pinned_actions(ROOT / ".github/workflows", "*.y*ml")
_require_pinned_actions(ROOT / ".github/actions", "action.yml")
docker_lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
if not docker_lines or not re.fullmatch(r"# syntax=[^@]+@sha256:[0-9a-f]{64}", docker_lines[0]):
    fail("Dockerfile syntax frontend is not pinned to a digest")
for line in docker_lines:
    if line.startswith("FROM ") and "@sha256:" not in line:
        fail(f"container base is not pinned to a digest: {line}")
print("public-release checks passed")
