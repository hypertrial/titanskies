#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import fnmatch
import json
import tomllib
from pathlib import Path

from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/license-report.json"
NOTICES = ROOT / "artifacts/THIRD_PARTY_NOTICES.md"
OVERRIDES = json.loads((ROOT / "shared/license-overrides.json").read_text(encoding="utf-8"))["packages"]
ASSET_REGISTRY = json.loads((ROOT / "shared/bundled-assets.json").read_text(encoding="utf-8"))
OPEN_LICENSES = {
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BlueOak-1.0.0",
    "CC-BY-4.0", "CC0-1.0", "ISC", "LGPL-3.0-or-later", "MIT", "MIT-0",
    "MIT-CMU", "MPL-2.0", "PSF-2.0", "Python-2.0", "Zlib",
}
LICENSE_ALIASES = {
    "Apache License Version 2.0": "Apache-2.0",
    "Public Domain": "CC0-1.0",
    "Public Domain / CC0-1.0": "CC0-1.0",
}


def accepted(value: str) -> bool:
    try:
        expression = canonicalize_license_expression(LICENSE_ALIASES.get(value, value))
    except InvalidLicenseExpression:
        return False
    identifiers = expression.replace("(", " ").replace(")", " ").split()
    return all(identifier in {"AND", "OR"} or identifier in OPEN_LICENSES for identifier in identifiers)


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

installed = {
    (distribution.metadata.get("Name") or "").lower().replace("_", "-"): distribution
    for distribution in importlib.metadata.distributions()
}
python = []
uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
locked = sorted({
    (item["name"], item["version"])
    for item in uv_lock.get("package", [])
    if isinstance(item, dict) and isinstance(item.get("source"), dict) and "registry" in item["source"]
})
for name, version in locked:
    normalized = name.lower().replace("_", "-")
    distribution = installed.get(normalized)
    license_name = OVERRIDES.get(normalized, {}).get("license") \
        or (distribution.metadata.get("License-Expression") if distribution else None) \
        or (distribution.metadata.get("License") if distribution else None) \
        or "UNKNOWN"
    python.append({"name": name, "version": version, "license": license_name})
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
