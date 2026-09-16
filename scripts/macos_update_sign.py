#!/usr/bin/env python3
"""Sign a macos-arm64 update manifest with an offline Ed25519 key."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from macos.packaging_lib import canonicalize_manifest_bytes, sign_ed25519, validate_update_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--key", required=True)
    parser.add_argument("--current-version", default="0.0.0")
    parser.add_argument("--channel", default="unsigned-beta")
    args = parser.parse_args()
    path = Path(args.manifest)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("signature", None)
    payload["signature"] = sign_ed25519(canonicalize_manifest_bytes(payload), Path(args.key))
    validate_update_manifest(payload, current_version=args.current_version, channel=args.channel)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o644)
    print(path)


if __name__ == "__main__":
    main()
