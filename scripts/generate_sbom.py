#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote

root = Path(__file__).resolve().parent.parent
target = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/sbom.cdx.json")
package = json.loads((root / "package.json").read_text(encoding="utf-8"))
lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
components = []
for location, item in lock["packages"].items():
    if not location or item.get("link"):
        continue
    name = location.rsplit("node_modules/", 1)[-1]
    version = item.get("version", "unknown")
    components.append(
        {
            "type": "library",
            "group": name.split("/", 1)[0] if name.startswith("@") else "",
            "name": name.split("/", 1)[-1],
            "version": version,
            "purl": f"pkg:npm/{quote(name, safe='/')}@{version}",
        }
    )
uv_lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
for item in uv_lock.get("package", []):
    name = item.get("name")
    version = item.get("version")
    if name == package["name"] or not isinstance(name, str) or not isinstance(version, str):
        continue
    components.append(
        {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{quote(name)}@{version}",
        }
    )
payload = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "version": 1,
    "metadata": {"component": {"type": "application", "name": package["name"], "version": package["version"]}},
    "components": components,
}
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(target)
