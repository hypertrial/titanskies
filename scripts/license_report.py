#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import fnmatch
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/license-report.json"
NOTICES = ROOT / "artifacts/THIRD_PARTY_NOTICES.md"
OVERRIDES = json.loads((ROOT / "shared/license-overrides.json").read_text(encoding="utf-8"))["packages"]
ASSET_REGISTRY = json.loads((ROOT / "shared/bundled-assets.json").read_text(encoding="utf-8"))
OPEN_MARKERS = ("MIT", "BSD", "ISC", "Apache", "MPL", "LGPL", "CC0", "CC-BY", "BlueOak", "Python", "PSF", "Zlib", "0BSD", "Public Domain")
BLOCKED_MARKERS = ("UNKNOWN", "UNLICENSED", "PROPRIETARY", "COMMERCIAL")


def accepted(value: str) -> bool:
    normalized = value.upper()
    return bool(value) and not any(marker in normalized for marker in BLOCKED_MARKERS) \
        and any(marker.upper() in normalized for marker in OPEN_MARKERS)


lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
npm = []
failures = []
for location, package in lock["packages"].items():
    if not location or package.get("link"):
        continue
    name = location.rsplit("node_modules/", 1)[-1]
    license_name = package.get("license") or OVERRIDES.get(name, {}).get("license", "UNKNOWN")
    npm.append({"name": name, "version": package.get("version"), "license": license_name})
    if not accepted(license_name):
        failures.append(f"npm:{name}:{license_name}")

python = []
for distribution in sorted(importlib.metadata.distributions(), key=lambda item: (item.metadata.get("Name") or "").lower()):
    name = distribution.metadata.get("Name") or "UNKNOWN"
    license_name = OVERRIDES.get(name.lower(), {}).get("license") or distribution.metadata.get("License-Expression") or distribution.metadata.get("License") or "UNKNOWN"
    python.append({"name": name, "version": distribution.version, "license": license_name})
    if not accepted(license_name):
        failures.append(f"python:{name}:{license_name}")

assets = ASSET_REGISTRY.get("assets", [])
if not isinstance(assets, list):
    raise SystemExit("invalid bundled asset registry")
for asset in assets:
    if not isinstance(asset, dict) or not isinstance(asset.get("patterns"), list) or not asset["patterns"]:
        failures.append("asset-registry:invalid-patterns")
        continue
    if not accepted(str(asset.get("license") or "")):
        failures.append(f"asset:{asset.get('origin', 'unknown')}:{asset.get('license', 'UNKNOWN')}")
static_files = [
    path.relative_to(ROOT).as_posix()
    for directory in (ROOT / "public", ROOT / "shared")
    for path in directory.rglob("*")
    if path.is_file()
]
for pathname in static_files:
    if not any(fnmatch.fnmatch(pathname, pattern) for asset in assets for pattern in asset["patterns"]):
        failures.append(f"asset:{pathname}:UNREGISTERED")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({"schemaVersion": 1, "npm": npm, "python": python, "assets": assets, "failures": failures}, indent=2) + "\n", encoding="utf-8")
if failures:
    raise SystemExit("proprietary or unknown licenses: " + ", ".join(failures))
notice_lines = [
    "# TitanSkies release dependency notices",
    "",
    "This generated inventory covers the dependencies locked for this release. See each package distribution for its complete license text.",
    "",
    "## JavaScript packages",
    "",
    "| Package | Version | License |",
    "| --- | --- | --- |",
    *(f"| `{item['name']}` | `{item['version']}` | {item['license']} |" for item in sorted(npm, key=lambda value: value["name"].lower())),
    "",
    "## Python packages",
    "",
    "| Package | Version | License |",
    "| --- | --- | --- |",
    *(f"| `{item['name']}` | `{item['version']}` | {item['license']} |" for item in python),
    "",
    "## Bundled assets",
    "",
    "| Origin | Paths | License | Terms |",
    "| --- | --- | --- | --- |",
    *(f"| {item['origin']} | `{'`, `'.join(item['patterns'])}` | {item['license']} | {item['terms']} |" for item in assets),
    "",
]
NOTICES.write_text("\n".join(notice_lines), encoding="utf-8")
print(OUT)
print(NOTICES)
