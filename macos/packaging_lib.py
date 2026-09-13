"""Linux-safe macOS packaging contract used by tests and Darwin build scripts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging" / "macos"
RUNTIME_LOCK_PATH = PACKAGING / "runtime-lock.json"
UPDATE_KEY_PATH = PACKAGING / "update-public-key.json"

BUNDLE_ID = "com.hypertrial.titanskies"
BETA_WEB_LABEL = "com.hypertrial.titanskies.beta.web"
BETA_INGEST_LABEL = "com.hypertrial.titanskies.beta.ingest"
PRODUCTION_WEB_LABEL = "com.hypertrial.titanskies.web"
PRODUCTION_INGEST_LABEL = "com.hypertrial.titanskies.ingest"
NATIVE_USER_AGENT_TOKEN = "TitanSkiesNative/"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8080
INGEST_MINUTES = (0, 15, 30, 45)
ALLOWED_KEYS = {
    "CONTEXT_SOURCE",
    "TITANSKIES_DATA_DIR",
    "TITANSKIES_CACHE_DIR",
    "TITANSKIES_BIND_ADDR",
    "TITANSKIES_PORT",
    "CONTEXT_WATCH_SECONDS",
    "FRAME_RETENTION_HOURS",
    "AIRNOW_API_KEY",
    "INGEST_BUDGET_SECONDS",
    "INGEST_HTTP_CONCURRENCY",
    "CONTEXT_SOURCE_CONCURRENCY",
    "HRRR_CONCURRENCY",
    "FIREWORK_CONCURRENCY",
    "SINAICA_CONCURRENCY",
}
WEB_DENIED_KEYS = {"AIRNOW_API_KEY"}
SECRET_KEYS = {"AIRNOW_API_KEY"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class PackagingError(ValueError):
    """Invalid macOS packaging or configuration."""


def load_runtime_lock(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or RUNTIME_LOCK_PATH).read_text(encoding="utf-8"))
    validate_runtime_lock(payload)
    return payload


def validate_runtime_lock(payload: Mapping[str, Any]) -> None:
    if payload.get("schemaVersion") != 1:
        raise PackagingError("runtime-lock schemaVersion must be 1")
    if payload.get("platform") != "macos-arm64":
        raise PackagingError("runtime-lock platform must be macos-arm64")
    if payload.get("minimumOS") != "13.0":
        raise PackagingError("runtime-lock minimumOS must be 13.0")
    if payload.get("bundleIdentifier") != BUNDLE_ID:
        raise PackagingError("runtime-lock bundleIdentifier mismatch")
    public_key = payload.get("updatePublicKeyHex")
    if not isinstance(public_key, str) or not re.fullmatch(r"[0-9a-f]{64}", public_key):
        raise PackagingError("runtime-lock update public key must be 32-byte hex")
    runtimes = payload.get("runtimes")
    if not isinstance(runtimes, dict) or not runtimes:
        raise PackagingError("runtime-lock is missing runtimes")
    for name, item in runtimes.items():
        if not isinstance(item, dict):
            raise PackagingError(f"{name} runtime is not an object")
        for key in ("version", "url", "sha256", "license", "arch", "os"):
            if not isinstance(item.get(key), str) or not str(item[key]).strip():
                raise PackagingError(f"{name} is missing {key}")
        if item["arch"] != "arm64" or item["os"] != "darwin":
            raise PackagingError(f"{name} must be darwin/arm64")
        if not SHA256_RE.fullmatch(item["sha256"]):
            raise PackagingError(f"{name} sha256 is not 64 lowercase hex chars")
        host = urlparse(item["url"]).hostname or ""
        if host not in {"nodejs.org", "github.com"}:
            raise PackagingError(f"{name} download host {host} is not pinned")
        if "x86_64" in item["url"] or "amd64" in item["url"]:
            raise PackagingError(f"{name} URL must not reference x86_64")


def load_update_public_key() -> dict[str, str]:
    payload = json.loads(UPDATE_KEY_PATH.read_text(encoding="utf-8"))
    lock = load_runtime_lock()
    if payload.get("publicKeyHex") != lock["updatePublicKeyHex"]:
        raise PackagingError("update public key does not match runtime-lock")
    if payload.get("keyId") != lock["updateKeyId"]:
        raise PackagingError("update key id does not match runtime-lock")
    return payload


def user_paths(home: Path) -> dict[str, Path]:
    support = home / "Library" / "Application Support" / "TitanSkies"
    return {
        "home": home,
        "support": support,
        "config": support / "config",
        "env": support / "config" / "env",
        "data": support / "data",
        "rollback": support / "rollback",
        "cache": home / "Library" / "Caches" / "TitanSkies",
        "logs": home / "Library" / "Logs" / "TitanSkies",
        "agents": home / "Library" / "LaunchAgents",
        "applications": home / "Applications",
        "app": home / "Applications" / "TitanSkies.app",
        "web_log": home / "Library" / "Logs" / "TitanSkies" / "web.log",
        "ingest_log": home / "Library" / "Logs" / "TitanSkies" / "ingest.log",
        "beta_web_plist": home / "Library" / "LaunchAgents" / f"{BETA_WEB_LABEL}.plist",
        "beta_ingest_plist": home / "Library" / "LaunchAgents" / f"{BETA_INGEST_LABEL}.plist",
    }


def bundle_paths(app: Path) -> dict[str, Path]:
    contents = app / "Contents"
    return {
        "app": app,
        "contents": contents,
        "macos": contents / "MacOS",
        "ui": contents / "MacOS" / "TitanSkies",
        "web": contents / "MacOS" / "TitanSkiesWeb",
        "ingest": contents / "MacOS" / "TitanSkiesIngest",
        "updater": contents / "MacOS" / "TitanSkiesUpdater",
        "helpers": contents / "Resources" / "Host",
        "node": contents / "Resources" / "Host" / "node" / "bin" / "node",
        "python": contents / "Resources" / "Host" / "python" / "bin" / "python3",
        "runtime": contents / "Resources" / "Runtime",
        "info": contents / "Info.plist",
        "production_web_plist": contents / "Library" / "LaunchAgents" / f"{PRODUCTION_WEB_LABEL}.plist",
        "production_ingest_plist": contents / "Library" / "LaunchAgents" / f"{PRODUCTION_INGEST_LABEL}.plist",
    }


def required_modes() -> dict[str, int]:
    return {
        "env": 0o600,
        "data": 0o700,
        "cache": 0o700,
        "logs": 0o700,
        "config": 0o700,
        "support": 0o700,
        "rollback": 0o700,
    }


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if any(ord(char) < 32 for char in raw if char not in "\t"):
            raise PackagingError(f"control character on line {index}")
        if "=" not in line:
            raise PackagingError(f"malformed env line {index}")
        key, value = line.split("=", 1)
        if not KEY_RE.fullmatch(key):
            raise PackagingError(f"unsupported env key {key}")
        if key not in ALLOWED_KEYS:
            raise PackagingError(f"unsupported env key {key}")
        if any(ord(char) < 32 for char in value):
            raise PackagingError(f"control character in {key}")
        values[key] = value.strip().strip("'").strip('"')
    return values


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def reject_unsafe_path(path: Path, *, app: Path | None = None, home: Path | None = None) -> Path:
    raw = os.fspath(path)
    if not raw.startswith("/"):
        raise PackagingError("path must be absolute")
    if any(ord(char) < 32 for char in raw) or "\x00" in raw:
        raise PackagingError("path contains control characters")
    if raw != posixpath.normpath(raw):
        raise PackagingError("path must be normalized")
    resolved = Path(raw)
    if app is not None and (_is_relative_to(resolved, app) or raw.startswith(str(app))):
        raise PackagingError("path must not be inside the app bundle")
    if home is not None:
        rollback = user_paths(home)["rollback"]
        if _is_relative_to(resolved, rollback):
            raise PackagingError("path must not use the rollback area")
    return resolved


def validate_env(
    values: Mapping[str, str],
    *,
    app: Path,
    home: Path,
    follow_symlinks: bool = True,
) -> dict[str, str]:
    paths = user_paths(home)
    normalized = dict(values)
    source = normalized.get("CONTEXT_SOURCE", "live")
    if source not in {"live", "demo"}:
        raise PackagingError("CONTEXT_SOURCE must be live or demo")
    bind = normalized.get("TITANSKIES_BIND_ADDR", DEFAULT_BIND)
    if bind != DEFAULT_BIND:
        raise PackagingError("native macOS bind address must be 127.0.0.1")
    port_raw = normalized.get("TITANSKIES_PORT", str(DEFAULT_PORT))
    if not port_raw.isdigit() or int(port_raw) != DEFAULT_PORT:
        raise PackagingError("native macOS port must be 8080")
    data = Path(normalized.get("TITANSKIES_DATA_DIR") or paths["data"])
    cache = Path(normalized.get("TITANSKIES_CACHE_DIR") or paths["cache"])
    for label, candidate in (("data", data), ("cache", cache)):
        reject_unsafe_path(candidate, app=app, home=home)
        if follow_symlinks and candidate.exists() and candidate.is_symlink():
            raise PackagingError(f"{label} path must not be a symlink")
    normalized["CONTEXT_SOURCE"] = source
    normalized["TITANSKIES_BIND_ADDR"] = DEFAULT_BIND
    normalized["TITANSKIES_PORT"] = str(DEFAULT_PORT)
    normalized["TITANSKIES_DATA_DIR"] = str(data)
    normalized["TITANSKIES_CACHE_DIR"] = str(cache)
    return normalized


def web_environment(values: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in values.items() if key not in WEB_DENIED_KEYS}


def ingest_environment(values: Mapping[str, str]) -> dict[str, str]:
    return dict(values)


def write_env_atomic(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={values[key]}" for key in sorted(values)]
    body = "\n".join(lines) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="env.", dir=str(path.parent), text=True)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def is_translocated(path: Path) -> bool:
    text = os.fspath(path)
    return "AppTranslocation" in text or "/d/" in text and "Transloc" in text


def is_untrusted_install_source(path: Path) -> bool:
    text = os.fspath(path)
    return (
        is_translocated(path)
        or text.startswith("/Volumes/")
        or "/.Trash/" in text
        or text.endswith(".dmg")
    )


def render_template(template: str, replacements: Mapping[str, str]) -> str:
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(f"@{key}@", value)
    if "@" in rendered and re.search(r"@[A-Z_]+@", rendered):
        raise PackagingError("unsubstituted launchd placeholder")
    return rendered


def launchd_replacements(home: Path, app: Path) -> dict[str, str]:
    paths = user_paths(home)
    bundle = bundle_paths(app)
    return {
        "WEB_LAUNCHER": str(bundle["web"]),
        "INGEST_LAUNCHER": str(bundle["ingest"]),
        "RUNTIME": str(bundle["runtime"]),
        "WEB_LOG": str(paths["web_log"]),
        "INGEST_LOG": str(paths["ingest_log"]),
    }


def render_beta_plists(home: Path, app: Path) -> dict[str, str]:
    replacements = launchd_replacements(home, app)
    web = render_template(
        (PACKAGING / "launchd" / "com.hypertrial.titanskies.beta.web.plist.template").read_text(encoding="utf-8"),
        replacements,
    )
    ingest = render_template(
        (PACKAGING / "launchd" / "com.hypertrial.titanskies.beta.ingest.plist.template").read_text(encoding="utf-8"),
        replacements,
    )
    return {BETA_WEB_LABEL: web, BETA_INGEST_LABEL: ingest}


def validate_plist_semantics(text: str, *, ingest: bool) -> dict[str, Any]:
    payload = plistlib.loads(text.encode("utf-8"))
    if not isinstance(payload, dict):
        raise PackagingError("plist is not a dictionary")
    if ingest:
        if payload.get("KeepAlive"):
            raise PackagingError("ingest must not keep alive")
        intervals = payload.get("StartCalendarInterval")
        if not isinstance(intervals, list) or len(intervals) != 4:
            raise PackagingError("ingest requires four calendar intervals")
        minutes = [item.get("Minute") for item in intervals]
        if minutes != list(INGEST_MINUTES):
            raise PackagingError("ingest calendar minutes must be 0/15/30/45")
        if payload.get("RunAtLoad"):
            raise PackagingError("ingest must not set RunAtLoad")
        if payload.get("StartInterval") is not None:
            raise PackagingError("ingest must not use StartInterval")
    else:
        if payload.get("KeepAlive") is not True:
            raise PackagingError("web must keep alive")
        if payload.get("RunAtLoad") is not True:
            raise PackagingError("web must run at load")
        if int(payload.get("ThrottleInterval") or 0) < 1:
            raise PackagingError("web must throttle restarts")
    label = payload.get("Label")
    if not isinstance(label, str) or not label.startswith("com.hypertrial.titanskies"):
        raise PackagingError("plist label is not a TitanSkies label")
    if "EnvironmentVariables" in payload:
        env = payload["EnvironmentVariables"]
        if any(key in SECRET_KEYS for key in env):
            raise PackagingError("secrets must not appear in launchd environment")
    program = payload.get("Program") or (payload.get("ProgramArguments") or [None])[0]
    bundle_program = payload.get("BundleProgram")
    executable = bundle_program or program
    if not isinstance(executable, str) or not executable:
        raise PackagingError("plist is missing a program")
    if "Application Support" in executable:
        raise PackagingError("executable path must not be in Application Support")
    return payload


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise PackagingError(f"invalid version {value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def canonicalize_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    body = {key: payload[key] for key in payload if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_update_manifest(
    payload: Mapping[str, Any],
    *,
    current_version: str,
    current_os: str = "13.0",
    channel: str = "unsigned-beta",
    public_key_hex: str | None = None,
) -> dict[str, Any]:
    required = {
        "version": str,
        "minimumOS": str,
        "architecture": str,
        "channel": str,
        "dmgURL": str,
        "sha256": str,
        "releaseNotesURL": str,
        "keyId": str,
        "signature": str,
    }
    for key, expected in required.items():
        if not isinstance(payload.get(key), expected) or not str(payload[key]).strip():
            raise PackagingError(f"update manifest missing {key}")
    if payload["architecture"] != "arm64":
        raise PackagingError("update architecture must be arm64")
    if payload["channel"] != channel:
        raise PackagingError("update channel mismatch")
    if not SHA256_RE.fullmatch(payload["sha256"]):
        raise PackagingError("update sha256 is invalid")
    if urlparse(payload["dmgURL"]).scheme != "https" or urlparse(payload["releaseNotesURL"]).scheme != "https":
        raise PackagingError("update URLs must be https")
    if parse_version(payload["minimumOS"] + ".0" if payload["minimumOS"].count(".") == 1 else payload["minimumOS"]) > parse_version(
        current_os if current_os.count(".") >= 2 else f"{current_os}.0"
    ):
        raise PackagingError("update requires a newer macOS")
    if not version_is_newer(payload["version"], current_version):
        raise PackagingError("update version is not newer")
    key = load_update_public_key()
    if payload["keyId"] != key["keyId"]:
        raise PackagingError("update key id mismatch")
    expected_key = public_key_hex or key["publicKeyHex"]
    if not re.fullmatch(r"[0-9a-f]{64}", expected_key):
        raise PackagingError("embedded public key is invalid")
    with tempfile.TemporaryDirectory() as raw:
        pub = Path(raw) / "pub.pem"
        pub.write_bytes(ed25519_public_pem_from_hex(expected_key))
        if not verify_ed25519_signature(
            canonicalize_manifest_bytes(payload),
            str(payload["signature"]),
            public_pem=pub,
        ):
            raise PackagingError("update signature is invalid")
    return dict(payload)


def ed25519_public_pem_from_hex(public_key_hex: str) -> bytes:
    raw = bytes.fromhex(public_key_hex)
    if len(raw) != 32:
        raise PackagingError("embedded public key is invalid")
    spki = bytes.fromhex("302a300506032b6570032100") + raw
    encoded = base64.b64encode(spki).decode("ascii")
    wrapped = "\n".join(encoded[index : index + 64] for index in range(0, len(encoded), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{wrapped}\n-----END PUBLIC KEY-----\n".encode("ascii")


def verify_ed25519_signature(message: bytes, signature_hex: str, secret_pem: Path | None = None, public_pem: Path | None = None) -> bool:
    if not re.fullmatch(r"[0-9a-f]+", signature_hex) or len(signature_hex) != 128:
        raise PackagingError("signature must be 64-byte hex")
    signature = bytes.fromhex(signature_hex)
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        message_path = work / "message"
        signature_path = work / "message.sig"
        message_path.write_bytes(message)
        signature_path.write_bytes(signature)
        pub = public_pem
        if pub is None and secret_pem is not None:
            pub = work / "pub.pem"
            subprocess.run(
                ["openssl", "pkey", "-in", str(secret_pem), "-pubout", "-out", str(pub)],
                check=True,
                capture_output=True,
            )
        if pub is None:
            raise PackagingError("public key is required to verify")
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(pub),
                "-rawin",
                "-in",
                str(message_path),
                "-sigfile",
                str(signature_path),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0


def sign_ed25519(message: bytes, secret_pem: Path) -> str:
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        message_path = work / "message"
        signature_path = work / "message.sig"
        message_path.write_bytes(message)
        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(secret_pem),
                "-rawin",
                "-in",
                str(message_path),
                "-out",
                str(signature_path),
            ],
            check=True,
            capture_output=True,
        )
        return signature_path.read_bytes().hex()


def rollback_plan(*, live_app: Path, staged_app: Path, rollback_dir: Path) -> list[str]:
    if _is_relative_to(rollback_dir, live_app):
        raise PackagingError("rollback area must be outside the live app")
    return [
        "bootout-web",
        "bootout-ingest",
        "rename-live-to-rollback",
        "rename-staged-to-live",
        "register-agents",
        "start-web",
        "kickstart-ingest",
        "healthz",
    ]


def restore_plan() -> list[str]:
    return [
        "bootout-failed-jobs",
        "verify-rollback-app",
        "rename-failed-aside",
        "rename-rollback-to-live",
        "register-agents",
        "start-web",
        "retain-logs",
    ]


def uninstall_plan(*, purge: bool) -> list[str]:
    steps = [
        "bootout-web",
        "bootout-ingest",
        "confirm-inactive",
        "remove-beta-plists",
        "remove-update-artifacts",
        "remove-rollback",
        "move-app-to-trash",
    ]
    if purge:
        steps.append("delete-default-owned-paths")
    return steps


def production_migration_plan() -> list[str]:
    return [
        "detect-beta-labels",
        "bootout-beta-web",
        "bootout-beta-ingest",
        "remove-beta-plists",
        "register-production-web",
        "register-production-ingest",
        "confirm-no-duplicate-jobs",
    ]


def owned_purge_paths(home: Path) -> list[Path]:
    paths = user_paths(home)
    return [paths["support"], paths["cache"], paths["logs"]]


def refuse_custom_purge(path: Path, home: Path) -> None:
    allowed = owned_purge_paths(home)
    candidate = Path(os.fspath(path))
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != parent.parent):
        raise PackagingError("purge refuses to follow symlinks")
    resolved_allowed = {item.resolve() for item in allowed}
    resolved = candidate.resolve() if candidate.exists() else candidate
    if resolved not in resolved_allowed and not any(_is_relative_to(resolved, item) for item in resolved_allowed):
        raise PackagingError("purge refuses custom paths")


def assert_no_application_support_executables(app: Path, home: Path) -> None:
    support = user_paths(home)["support"]
    for executable in bundle_paths(app).values():
        if "Application Support" in os.fspath(executable) and executable.suffix != "":
            raise PackagingError("bundle executable resolved into Application Support")
    if support.exists():
        for path in support.rglob("*"):
            if path.is_file() and os.access(path, os.X_OK) and path.suffix in {"", ".dylib", ".so"}:
                if path.parent.name not in {"rollback"}:
                    raise PackagingError(f"executable payload in Application Support: {path}")


def inspect_production_plists() -> None:
    web = validate_plist_semantics(
        (PACKAGING / "launchd" / f"{PRODUCTION_WEB_LABEL}.plist").read_text(encoding="utf-8"),
        ingest=False,
    )
    ingest = validate_plist_semantics(
        (PACKAGING / "launchd" / f"{PRODUCTION_INGEST_LABEL}.plist").read_text(encoding="utf-8"),
        ingest=True,
    )
    if web.get("BundleProgram") != "Contents/MacOS/TitanSkiesWeb":
        raise PackagingError("production web plist must use BundleProgram")
    if ingest.get("BundleProgram") != "Contents/MacOS/TitanSkiesIngest":
        raise PackagingError("production ingest plist must use BundleProgram")
    if web.get("Label") == BETA_WEB_LABEL or ingest.get("Label") == BETA_INGEST_LABEL:
        raise PackagingError("production labels must not reuse beta labels")


def copy_app_preserving_metadata(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)


def rotate_log(path: Path, *, limit_bytes: int = 2 * 1024 * 1024) -> None:
    if not path.exists() or path.stat().st_size < limit_bytes:
        return
    rotated = path.with_suffix(path.suffix + ".1")
    if rotated.exists():
        rotated.unlink()
    os.replace(path, rotated)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_directory(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    if stat.S_IMODE(path.stat().st_mode) != mode:
        raise PackagingError(f"failed to set mode {oct(mode)} on {path}")


def macho_is_forbidden(file_output: str) -> bool:
    lowered = file_output.lower()
    if "x86_64" in lowered and "arm64" not in lowered:
        return True
    if "i386" in lowered:
        return True
    return False
