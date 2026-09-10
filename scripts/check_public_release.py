#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = json.loads((ROOT / "shared/data-sources.json").read_text(encoding="utf-8"))
CONTRACT = json.loads((ROOT / "shared/context-contract-v8.json").read_text(encoding="utf-8"))
EXPECTED = {"firework", "hrrr", "airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis"}
RETIRED = {"tempo", "hms", "firms"}
RETIRED_HOSTS = {
    "cmr.earthdata.nasa.gov",
    "firms.modaps.eosdis.nasa.gov",
    "satepsanone.nesdis.noaa.gov",
    "www.star.nesdis.noaa.gov",
}
URL = re.compile(r"https://[^\"'\s)]+")
ACTION = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)


def fail(message: str) -> None:
    print(f"public-release check: {message}", file=sys.stderr)
    raise SystemExit(1)


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

allowed_hosts = {
    host
    for item in sources
    for key in ("endpointHosts", "documentationHosts")
    for host in item.get(key, [])
}
runtime_files = [ROOT / "ingest/config.py"] + [
    ROOT / f"ingest/sources/{name}.py"
    for name in ("airnow", "aqhi", "bc_air", "sinaica", "wildfires", "firework", "hrrr")
]
for path in runtime_files:
    for raw in URL.findall(path.read_text(encoding="utf-8")):
        host = urlparse(raw.rstrip(".,")).hostname
        if host in RETIRED_HOSTS:
            fail(f"retired data host {host} in {path.relative_to(ROOT)}")
        if host not in allowed_hosts:
            fail(f"unregistered runtime data host {host} in {path.relative_to(ROOT)}")

for path in (ROOT / "ingest").rglob("*.py"):
    text = path.read_text(encoding="utf-8").lower()
    for host in RETIRED_HOSTS:
        if host in text:
            fail(f"retired data host {host} in {path.relative_to(ROOT)}")

for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in {
        ".git", ".venv", "node_modules", ".next", "public", "tests", "artifacts", "test-results", "playwright-report"
    } for part in path.parts):
        continue
    relative = path.relative_to(ROOT).as_posix()
    if ".test." in path.name:
        continue
    if relative in {"scripts/check_public_release.py", "DATA_SOURCES.md", "PRODUCT.md", "README.md"}:
        continue
    if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml", ".sh"}:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    if any(re.search(rf"(?<![a-z0-9]){term}(?![a-z0-9])", text) for term in RETIRED):
        fail(f"retired integration reference in {relative}")
    if "@vercel/" in text or "vercel-storage.com" in text or "blob_read_write_token" in text:
        fail(f"proprietary deployment reference in {relative}")

if (ROOT / ".pad").exists() or (ROOT / ".pad.toml").exists():
    fail("private Pad metadata is present")
for workflow in (ROOT / ".github/workflows").glob("*.y*ml"):
    for action in ACTION.findall(workflow.read_text(encoding="utf-8")):
        if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", action):
            fail(f"workflow action is not pinned to a commit: {action}")
docker_lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
if not docker_lines or not re.fullmatch(r"# syntax=[^@]+@sha256:[0-9a-f]{64}", docker_lines[0]):
    fail("Dockerfile syntax frontend is not pinned to a digest")
for line in docker_lines:
    if line.startswith("FROM ") and "@sha256:" not in line:
        fail(f"container base is not pinned to a digest: {line}")
print("public-release checks passed")
