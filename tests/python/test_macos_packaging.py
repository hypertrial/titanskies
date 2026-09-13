from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shlex
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

from macos.packaging_lib import (
    ALLOWED_KEYS,
    BETA_INGEST_LABEL,
    BETA_WEB_LABEL,
    BUNDLE_ID,
    INGEST_MINUTES,
    NATIVE_USER_AGENT_TOKEN,
    PRODUCTION_INGEST_LABEL,
    PRODUCTION_WEB_LABEL,
    ROOT,
    PackagingError,
    assert_no_application_support_executables,
    bundle_paths,
    canonicalize_manifest_bytes,
    ed25519_public_pem_from_hex,
    ensure_directory,
    ingest_environment,
    inspect_production_plists,
    is_untrusted_install_source,
    load_runtime_lock,
    load_update_public_key,
    macho_is_forbidden,
    owned_purge_paths,
    parse_env,
    production_migration_plan,
    refuse_custom_purge,
    reject_unsafe_path,
    render_beta_plists,
    required_modes,
    restore_plan,
    rollback_plan,
    sha256_file,
    sign_ed25519,
    uninstall_plan,
    user_paths,
    validate_env,
    validate_plist_semantics,
    validate_runtime_lock,
    validate_update_manifest,
    verify_ed25519_signature,
    web_environment,
    write_env_atomic,
)

FIXTURE_KEY = Path(__file__).parent / "fixtures" / "macos-update-test-ed25519.pem"
FIXTURE_PUBLIC_HEX = "9e3964a96bb121891ee1feb782c53cb3bb96ec199b4692b1a3be94fe916d80dc"


def test_runtime_lock_pins_darwin_arm64_downloads() -> None:
    lock = load_runtime_lock()
    key = load_update_public_key()
    assert lock["platform"] == "macos-arm64"
    assert lock["minimumOS"] == "13.0"
    assert lock["bundleIdentifier"] == BUNDLE_ID
    assert key["publicKeyHex"] == lock["updatePublicKeyHex"]
    for name, item in lock["runtimes"].items():
        assert item["arch"] == "arm64"
        assert item["os"] == "darwin"
        assert len(item["sha256"]) == 64
        assert "x86_64" not in item["url"]
        assert name in {"node", "cpython", "python-build-standalone"}
    node = lock["runtimes"]["node"]
    assert node["version"].startswith("22.")
    assert "nodejs.org/dist/v22" in node["url"]
    assert lock["runtimes"]["cpython"]["version"].startswith("3.12.")


def test_production_and_beta_plist_semantics() -> None:
    inspect_production_plists()
    home = Path("/Users/example")
    app = home / "Applications/TitanSkies.app"
    rendered = render_beta_plists(home, app)
    web = validate_plist_semantics(rendered[BETA_WEB_LABEL], ingest=False)
    ingest = validate_plist_semantics(rendered[BETA_INGEST_LABEL], ingest=True)
    assert web["Label"] == BETA_WEB_LABEL
    assert ingest["Label"] == BETA_INGEST_LABEL
    assert web["Label"] != PRODUCTION_WEB_LABEL
    assert ingest["Label"] != PRODUCTION_INGEST_LABEL
    assert PRODUCTION_WEB_LABEL == "com.hypertrial.titanskies.web"
    assert PRODUCTION_INGEST_LABEL == "com.hypertrial.titanskies.ingest"
    assert str(app / "Contents/MacOS/TitanSkiesWeb") in web["ProgramArguments"]
    assert "Application Support" not in web["ProgramArguments"][0]
    assert "EnvironmentVariables" not in web
    assert "AIRNOW" not in json.dumps(web)
    assert web["KeepAlive"] is True
    assert web["RunAtLoad"] is True
    assert ingest.get("KeepAlive") in {None, False}
    assert ingest.get("RunAtLoad") in {None, False}
    assert "StartInterval" not in ingest
    assert [item["Minute"] for item in ingest["StartCalendarInterval"]] == list(INGEST_MINUTES)
    assert INGEST_MINUTES == (0, 15, 30, 45)


def test_env_parser_rejects_secrets_in_web_and_unsafe_paths(tmp_path: Path) -> None:
    home = tmp_path / "home"
    app = home / "Applications/TitanSkies.app"
    app.mkdir(parents=True)
    parsed = parse_env(
        "CONTEXT_SOURCE=demo\n"
        "TITANSKIES_BIND_ADDR=127.0.0.1\n"
        "TITANSKIES_PORT=8080\n"
        "AIRNOW_API_KEY=secret-value\n"
    )
    validated = validate_env(parsed, app=app, home=home)
    assert "AIRNOW_API_KEY" not in web_environment(validated)
    with pytest.raises(PackagingError, match="unsupported env key"):
        parse_env("PATH=/bin\n")
    with pytest.raises(PackagingError, match="control character"):
        parse_env("CONTEXT_SOURCE=live\nAIRNOW_API_KEY=abc\x00def\n")
    with pytest.raises(PackagingError, match="bind address"):
        validate_env({"TITANSKIES_BIND_ADDR": "0.0.0.0"}, app=app, home=home)
    with pytest.raises(PackagingError, match="inside the app bundle"):
        reject_unsafe_path(app / "Contents/Resources/Runtime/data", app=app, home=home)
    with pytest.raises(PackagingError, match="rollback"):
        reject_unsafe_path(user_paths(home)["rollback"] / "data", app=app, home=home)


def test_atomic_env_write_mode_and_no_secret_in_plist(tmp_path: Path) -> None:
    env_path = tmp_path / "config" / "env"
    write_env_atomic(env_path, {"CONTEXT_SOURCE": "demo", "AIRNOW_API_KEY": "abc"})
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert "AIRNOW_API_KEY=abc" in env_path.read_text(encoding="utf-8")
    home = tmp_path / "home"
    app = home / "Applications/TitanSkies.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    rendered = render_beta_plists(home, app)
    for body in rendered.values():
        assert "AIRNOW" not in body
        assert "secret" not in body


def test_first_launch_refuses_dmg_and_translocation(tmp_path: Path) -> None:
    assert is_untrusted_install_source(Path("/Volumes/TitanSkies/TitanSkies.app"))
    assert is_untrusted_install_source(Path("/private/var/folders/zz/AppTranslocation/abc/d/TitanSkies.app"))
    assert is_untrusted_install_source(tmp_path / ".Trash/TitanSkies.app")
    assert not is_untrusted_install_source(tmp_path / "home/Applications/TitanSkies.app")


def test_update_manifest_signature_architecture_and_monotonic_version() -> None:
    lock = load_runtime_lock()
    payload = {
        "version": "0.1.1",
        "minimumOS": "13.0",
        "architecture": "arm64",
        "channel": "unsigned-beta",
        "dmgURL": "https://github.com/hypertrial/titanskies/releases/download/v0.1.1/TitanSkies-0.1.1-macos-arm64.dmg",
        "sha256": "a" * 64,
        "releaseNotesURL": "https://github.com/hypertrial/titanskies/releases/tag/v0.1.1",
        "keyId": lock["updateKeyId"],
    }
    payload["signature"] = sign_ed25519(canonicalize_manifest_bytes(payload), FIXTURE_KEY)
    validated = validate_update_manifest(payload, current_version="0.1.0", public_key_hex=FIXTURE_PUBLIC_HEX)
    assert validated["version"] == "0.1.1"
    assert verify_ed25519_signature(canonicalize_manifest_bytes(payload), payload["signature"], secret_pem=FIXTURE_KEY)
    with pytest.raises(PackagingError, match="signature"):
        validate_update_manifest(payload, current_version="0.1.0")
    with pytest.raises(PackagingError, match="not newer"):
        validate_update_manifest({**payload, "version": "0.1.0"}, current_version="0.1.0")
    with pytest.raises(PackagingError, match="architecture"):
        validate_update_manifest({**payload, "architecture": "x86_64"}, current_version="0.1.0")
    with pytest.raises(PackagingError, match="channel"):
        validate_update_manifest({**payload, "channel": "production"}, current_version="0.1.0")
    tampered = {**payload, "sha256": "b" * 64, "signature": payload["signature"]}
    assert not verify_ed25519_signature(canonicalize_manifest_bytes(tampered), payload["signature"], secret_pem=FIXTURE_KEY)


def test_rollback_uninstall_and_migration_ordering(tmp_path: Path) -> None:
    home = tmp_path / "home"
    live = home / "Applications/TitanSkies.app"
    staged = tmp_path / "stage/TitanSkies.app"
    rollback = user_paths(home)["rollback"]
    assert rollback_plan(live_app=live, staged_app=staged, rollback_dir=rollback)[:4] == [
        "bootout-web",
        "bootout-ingest",
        "rename-live-to-rollback",
        "rename-staged-to-live",
    ]
    restore = restore_plan()
    assert restore[0] == "bootout-failed-jobs"
    assert restore[1] == "verify-rollback-app"
    assert "rename-rollback-to-live" in restore
    preserve = uninstall_plan(purge=False)
    assert preserve[0:3] == ["bootout-web", "bootout-ingest", "confirm-inactive"]
    assert "delete-default-owned-paths" not in preserve
    assert uninstall_plan(purge=True)[-1] == "delete-default-owned-paths"
    migration = production_migration_plan()
    assert migration[:4] == [
        "detect-beta-labels",
        "bootout-beta-web",
        "bootout-beta-ingest",
        "remove-beta-plists",
    ]
    assert "register-production-web" in migration
    assert BETA_WEB_LABEL != PRODUCTION_WEB_LABEL
    custom = tmp_path / "custom-data"
    custom.mkdir()
    with pytest.raises(PackagingError, match="custom paths"):
        refuse_custom_purge(custom, home)
    for path in owned_purge_paths(home):
        path.mkdir(parents=True)
    refuse_custom_purge(owned_purge_paths(home)[0], home)


def test_info_plist_local_networking_only() -> None:
    info = plistlib.loads((ROOT / "packaging/macos/Info.plist").read_text(encoding="utf-8").encode("utf-8"))
    ats = info["NSAppTransportSecurity"]
    assert ats == {"NSAllowsLocalNetworking": True}
    assert "NSAllowsArbitraryLoads" not in ats
    assert info["CFBundleIdentifier"] == BUNDLE_ID
    entitlements = plistlib.loads((ROOT / "packaging/macos/entitlements-production.plist").read_bytes())
    assert entitlements.get("com.apple.security.cs.disable-library-validation") is not True
    beta = plistlib.loads((ROOT / "packaging/macos/entitlements-beta.plist").read_bytes())
    assert "com.apple.security.cs.disable-library-validation" not in beta


def test_native_user_agent_token_and_allowed_keys() -> None:
    assert NATIVE_USER_AGENT_TOKEN == "TitanSkiesNative/"
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0]
        for line in example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert documented == ALLOWED_KEYS


def test_macho_inventory_rejects_intel_slices() -> None:
    assert macho_is_forbidden("Mach-O 64-bit executable x86_64")
    assert not macho_is_forbidden("Mach-O 64-bit executable arm64")


def test_linux_installer_excludes_macos_build_products() -> None:
    text = (ROOT / "scripts/install-user").read_text(encoding="utf-8")
    assert "macos/TitanSkies/.build" in text
    assert "macos/TitanSkies/.swiftpm" in text
    tsconfig = json.loads((ROOT / "tsconfig.json").read_text(encoding="utf-8"))
    assert "dist" in tsconfig["exclude"]
    assert ".local" in tsconfig["exclude"]


def test_version_files_stay_bound() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    info = plistlib.loads((ROOT / "packaging/macos/Info.plist").read_text(encoding="utf-8").encode("utf-8"))
    assert info["CFBundleShortVersionString"] == package["version"]
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{package["version"]}"' in pyproject
    assert os.environ.get("AIRNOW_API_KEY") is None or "packaging/macos" not in Path(__file__).as_posix()


def _signed_manifest(**overrides: object) -> dict[str, object]:
    lock = load_runtime_lock()
    payload: dict[str, object] = {
        "version": "0.1.1",
        "minimumOS": "13.0",
        "architecture": "arm64",
        "channel": "unsigned-beta",
        "dmgURL": "https://github.com/hypertrial/titanskies/releases/download/v0.1.1/TitanSkies-0.1.1-macos-arm64.dmg",
        "sha256": "a" * 64,
        "releaseNotesURL": "https://github.com/hypertrial/titanskies/releases/tag/v0.1.1",
        "keyId": lock["updateKeyId"],
    }
    payload.update(overrides)
    if "signature" not in overrides:
        payload["signature"] = sign_ed25519(canonicalize_manifest_bytes(payload), FIXTURE_KEY)
    return payload


def test_env_parser_never_executes_shell_and_is_ingest_only_for_airnow(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    substitution = f"$(touch {marker})"
    parsed = parse_env(
        "# export PATH=/bin\n"
        f"AIRNOW_API_KEY={substitution}\n"
        "CONTEXT_SOURCE=demo\n"
    )
    assert not marker.exists()
    assert parsed["AIRNOW_API_KEY"] == substitution
    assert "export" not in parsed
    app = tmp_path / "Applications/TitanSkies.app"
    app.mkdir(parents=True)
    validated = validate_env(parsed, app=app, home=tmp_path / "home")
    assert "AIRNOW_API_KEY" not in web_environment(validated)
    assert ingest_environment(validated)["AIRNOW_API_KEY"] == substitution
    env_path = tmp_path / "Library/Application Support/TitanSkies/config/env"
    write_env_atomic(env_path, validated)
    body = env_path.read_text(encoding="utf-8")
    assert not body.startswith("export ")
    assert "source " not in body
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert not os.access(env_path, os.X_OK)
    for invalid in (
        "export CONTEXT_SOURCE=demo\n",
        "source /tmp/evil\n",
        "PATH=/usr/bin\n",
        "LD_PRELOAD=/tmp/x.so\n",
        "DYLD_INSERT_LIBRARIES=/tmp/x.dylib\n",
        "CONTEXT_SOURCE\n",
        "airnow_api_key=secret\n",
    ):
        with pytest.raises(PackagingError):
            parse_env(invalid)
    backtick = parse_env("AIRNOW_API_KEY=`id`\n")
    assert backtick["AIRNOW_API_KEY"] == "`id`"
    assert not marker.exists()


def test_validate_env_rejects_non_loopback_port_invalid_source_and_symlinks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    app = home / "Applications/TitanSkies.app"
    app.mkdir(parents=True)
    with pytest.raises(PackagingError, match="port"):
        validate_env({"TITANSKIES_PORT": "8081"}, app=app, home=home)
    with pytest.raises(PackagingError, match="port"):
        validate_env({"TITANSKIES_PORT": "80"}, app=app, home=home)
    with pytest.raises(PackagingError, match="CONTEXT_SOURCE"):
        validate_env({"CONTEXT_SOURCE": "prod"}, app=app, home=home)
    alias = tmp_path / "data-alias"
    target = tmp_path / "data-real"
    target.mkdir()
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(PackagingError, match="symlink"):
        validate_env({"TITANSKIES_DATA_DIR": str(alias)}, app=app, home=home)


def test_runtime_executables_live_inside_the_app_bundle(tmp_path: Path) -> None:
    home = tmp_path / "home"
    app = home / "Applications/TitanSkies.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    bundle = bundle_paths(app)
    for key in ("ui", "web", "ingest", "updater", "node", "python", "runtime"):
        assert os.fspath(bundle[key]).startswith(os.fspath(app))
        assert "Application Support" not in os.fspath(bundle[key])
    paths = user_paths(home)
    assert paths["config"].parent == paths["support"]
    assert paths["data"].parent == paths["support"]
    assert paths["rollback"].parent == paths["support"]
    assert paths["cache"] == home / "Library/Caches/TitanSkies"
    assert paths["logs"] == home / "Library/Logs/TitanSkies"
    assert required_modes()["env"] == 0o600
    for name in ("data", "cache", "logs", "config", "support", "rollback"):
        assert required_modes()[name] == 0o700
    data = tmp_path / "Library/Application Support/TitanSkies/data"
    ensure_directory(data, 0o700)
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    launcher = app / "Contents/MacOS/TitanSkiesWeb"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    paths["config"].mkdir(parents=True)
    assert_no_application_support_executables(app, home)
    payload = paths["config"] / "python3"
    payload.write_text("#!/bin/sh\n", encoding="utf-8")
    payload.chmod(0o755)
    with pytest.raises(PackagingError, match="executable payload"):
        assert_no_application_support_executables(app, home)


def test_plist_validator_rejects_ingest_keepalive_and_startinterval() -> None:
    home = Path("/Users/example")
    app = home / "Applications/TitanSkies.app"
    rendered = render_beta_plists(home, app)
    ingest = plistlib.loads(rendered[BETA_INGEST_LABEL].encode("utf-8"))
    ingest["KeepAlive"] = True
    with pytest.raises(PackagingError, match="keep alive"):
        validate_plist_semantics(plistlib.dumps(ingest).decode("utf-8"), ingest=True)
    ingest = plistlib.loads(rendered[BETA_INGEST_LABEL].encode("utf-8"))
    ingest["StartInterval"] = 900
    with pytest.raises(PackagingError, match="StartInterval"):
        validate_plist_semantics(plistlib.dumps(ingest).decode("utf-8"), ingest=True)
    ingest = plistlib.loads(rendered[BETA_INGEST_LABEL].encode("utf-8"))
    ingest["RunAtLoad"] = True
    with pytest.raises(PackagingError, match="RunAtLoad"):
        validate_plist_semantics(plistlib.dumps(ingest).decode("utf-8"), ingest=True)
    ingest = plistlib.loads(rendered[BETA_INGEST_LABEL].encode("utf-8"))
    ingest["StartCalendarInterval"] = [{"Minute": 0}, {"Minute": 15}]
    with pytest.raises(PackagingError, match="four calendar intervals"):
        validate_plist_semantics(plistlib.dumps(ingest).decode("utf-8"), ingest=True)
    web = plistlib.loads(rendered[BETA_WEB_LABEL].encode("utf-8"))
    web["KeepAlive"] = False
    with pytest.raises(PackagingError, match="keep alive"):
        validate_plist_semantics(plistlib.dumps(web).decode("utf-8"), ingest=False)
    production_web = plistlib.loads((ROOT / "packaging/macos/launchd/com.hypertrial.titanskies.web.plist").read_bytes())
    production_ingest = plistlib.loads((ROOT / "packaging/macos/launchd/com.hypertrial.titanskies.ingest.plist").read_bytes())
    assert production_web["Label"] == PRODUCTION_WEB_LABEL
    assert production_ingest["Label"] == PRODUCTION_INGEST_LABEL
    assert production_web["BundleProgram"] == "Contents/MacOS/TitanSkiesWeb"
    assert production_ingest["BundleProgram"] == "Contents/MacOS/TitanSkiesIngest"
    assert "StartInterval" not in production_ingest
    assert [item["Minute"] for item in production_ingest["StartCalendarInterval"]] == [0, 15, 30, 45]


def test_untrusted_first_launch_must_copy_before_registering_agents() -> None:
    services = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Services.swift").read_text(encoding="utf-8")
    app_model = (ROOT / "macos/TitanSkies/Sources/TitanSkies/AppModel.swift").read_text(encoding="utf-8")
    start = services[services.index("public func start()") : services.index("public func stop()")]
    assert start.index("isUntrustedInstallSource") < start.index("writeBetaAgents")
    assert start.index("isUntrustedInstallSource") < start.index("bootstrap")
    assert "refuse to register services" in start
    bootstrap = app_model[app_model.index("func start() async") : app_model.index("func saveSettings()")]
    assert bootstrap.index("isUntrustedInstallSource") < bootstrap.index("services.start()")
    assert "needsInstall" in bootstrap
    installer = services[services.index("public func installIfNeeded()") :]
    assert "copyItem" in installer
    assert "installedApp" in installer
    assert installer.index("isUntrustedInstallSource") < installer.index("copyItem")


def test_wkwebview_policy_and_native_ua_source_contract() -> None:
    navigation = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Navigation.swift").read_text(encoding="utf-8")
    identity = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Identity.swift").read_text(encoding="utf-8")
    browser = (ROOT / "macos/TitanSkies/Sources/TitanSkies/BrowserView.swift").read_text(encoding="utf-8")
    explorer = (ROOT / "src/components/explorer/useExplorerEnvironment.ts").read_text(encoding="utf-8")
    assert 'bindAddress = "127.0.0.1"' in identity
    assert "port = 8080" in identity
    assert 'nativeUserAgentToken = "TitanSkiesNative/"' in identity
    assert "WKWebView" in browser
    assert 'URL(string: "http://127.0.0.1:\\(port)/")' in browser
    assert "NavigationPolicy.decide" in browser
    assert "NSWorkspace.shared.open" in browser
    assert "customUserAgent" in browser
    assert 'setValue(true, forKey: "WebKitWebGLEnabled")' in browser
    assert 'setValue(true, forKey: "WebKitWebGL2Enabled")' in browser
    assert 'setValue(false, forKey: "developerExtrasEnabled")' in browser
    assert 'setValue(false, forKey: "WebKitWebGL2Enabled")' not in browser
    assert "shouldPerformDownload" in browser
    assert "isInspectable = false" in browser
    assert 'scheme == "https"' in navigation
    assert "openInBrowser" in navigation
    assert "return .reject" in navigation
    assert "if (nativeShell) return;" in explorer
    assert "beforeinstallprompt" in explorer
    assert "event.preventDefault()" in explorer
    assert NATIVE_USER_AGENT_TOKEN == "TitanSkiesNative/"


def test_env_files_are_parsed_not_sourced_by_launchers() -> None:
    env_file = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/EnvFile.swift").read_text(encoding="utf-8")
    launcher = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/RuntimeLauncher.swift").read_text(encoding="utf-8")
    assert "/bin/sh" not in env_file
    assert "/bin/bash" not in env_file
    assert "Process(" not in env_file
    assert "EnvFile.parse" in env_file
    assert "webEnvironment()" in launcher
    assert "ingestEnvironment()" in launcher
    assert 'merged.removeValue(forKey: "AIRNOW_API_KEY")' in launcher
    assert '"PATH": "/usr/bin:/bin"' in launcher
    assert "ProcessInfo.processInfo.environment" not in launcher
    assert "DYLD_" not in launcher
    assert "paths.node" in launcher
    assert "paths.python" in launcher
    assert "Resources/Host/node/bin/node" in (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Paths.swift").read_text(
        encoding="utf-8"
    )
    assert "Resources/Host/python/bin/python3" in (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Paths.swift").read_text(
        encoding="utf-8"
    )


def test_update_manifest_requires_https_lowercase_sha256_and_arm64(tmp_path: Path) -> None:
    payload = _signed_manifest()
    with pytest.raises(PackagingError, match="https"):
        validate_update_manifest({**payload, "dmgURL": "http://example.invalid/TitanSkies.dmg"}, current_version="0.1.0")
    with pytest.raises(PackagingError, match="sha256"):
        validate_update_manifest({**payload, "sha256": "A" * 64}, current_version="0.1.0")
    with pytest.raises(PackagingError, match="sha256"):
        validate_update_manifest({**payload, "sha256": "a" * 63}, current_version="0.1.0")
    dmg = tmp_path / "TitanSkies-0.1.1-macos-arm64.dmg"
    dmg.write_bytes(b"synthetic-dmg")
    digest = sha256_file(dmg)
    assert digest == hashlib.sha256(b"synthetic-dmg").hexdigest()
    assert len(digest) == 64
    lock = json.loads(json.dumps(load_runtime_lock()))
    lock["runtimes"]["node"]["url"] = "https://nodejs.org/dist/v22.23.2/node-v22.23.2-darwin-x86_64.tar.gz"
    with pytest.raises(PackagingError, match="x86_64"):
        validate_runtime_lock(lock)
    intel_platform = json.loads(json.dumps(load_runtime_lock()))
    intel_platform["platform"] = "macos-x64"
    with pytest.raises(PackagingError, match="macos-arm64"):
        validate_runtime_lock(intel_platform)
    assert macho_is_forbidden("Mach-O 64-bit executable i386")


def test_rollback_never_executes_in_place_and_purge_refuses_symlinks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    live = home / "Applications/TitanSkies.app"
    staged = tmp_path / "stage/TitanSkies.app"
    rollback = user_paths(home)["rollback"]
    with pytest.raises(PackagingError, match="outside the live app"):
        rollback_plan(live_app=live, staged_app=staged, rollback_dir=live / "rollback")
    restore = restore_plan()
    assert restore.index("verify-rollback-app") < restore.index("rename-rollback-to-live")
    swap = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Update.swift").read_text(encoding="utf-8")
    restore_source = swap[swap.index("public var restoreSteps") :]
    assert restore_source.index("verify-rollback-app") < restore_source.index("rename-rollback-to-live")
    rendered = render_beta_plists(home, live)
    for body in rendered.values():
        assert os.fspath(rollback) not in body
        assert "Contents/MacOS/" in body
    owned = owned_purge_paths(home)
    assert {path.name for path in owned} == {"TitanSkies"}
    assert all("Applications" not in os.fspath(path) for path in owned)
    assert user_paths(home)["app"] not in owned
    uninstall = uninstall_plan(purge=True)
    assert uninstall.index("bootout-web") < uninstall.index("delete-default-owned-paths")
    assert uninstall[0] == "bootout-web"
    swift_uninstall = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Services.swift").read_text(encoding="utf-8")
    uninstall_fn = swift_uninstall[swift_uninstall.index("public func uninstall") :]
    assert uninstall_fn.index("launchd.bootout") < uninstall_fn.index("if purge")
    assert "isLoaded" in uninstall_fn
    assert "isSymbolicLink" in uninstall_fn
    assert "paths.support" in uninstall_fn
    custom_link = tmp_path / "custom-link"
    custom_target = tmp_path / "custom-target"
    custom_target.mkdir()
    (custom_target / "secret").write_text("keep", encoding="utf-8")
    custom_link.symlink_to(custom_target, target_is_directory=True)
    with pytest.raises(PackagingError, match="symlink"):
        refuse_custom_purge(custom_link, home)
    assert (custom_target / "secret").read_text(encoding="utf-8") == "keep"


def test_purge_refuses_when_a_default_owned_path_is_a_symlink(tmp_path: Path) -> None:
    home = tmp_path / "home"
    external = tmp_path / "elsewhere"
    external.mkdir()
    (external / "secret").write_text("keep", encoding="utf-8")
    support = user_paths(home)["support"]
    support.parent.mkdir(parents=True)
    support.symlink_to(external, target_is_directory=True)
    with pytest.raises(PackagingError, match="symlink"):
        refuse_custom_purge(support, home)


def test_production_signing_fails_closed_without_developer_id() -> None:
    script = ROOT / "scripts/sign-macos-release"
    text = script.read_text(encoding="utf-8")
    assert "MACOS_DEVELOPER_ID_IDENTITY is required" in text
    assert "unsigned beta must not claim production signing" in text
    assert 'IDENTITY=${MACOS_DEVELOPER_ID_IDENTITY:-}' in text
    identity_line = next(index for index, line in enumerate(text.splitlines()) if "MACOS_DEVELOPER_ID_IDENTITY is required" in line)
    first_sign = next(index for index, line in enumerate(text.splitlines()) if "codesign" in line and "--sign" in line)
    assert identity_line < first_sign
    for line in text.splitlines():
        if "codesign" in line and "--sign" in line:
            assert "--deep" not in line
    assert "MACOS_NOTARY_PROFILE:?" in text
    assert "Contents/Resources/Host" in text
    result = subprocess.run(
        ["sh", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "MACOS_DEVELOPER_ID_IDENTITY": "", "MACOS_NOTARY_PROFILE": ""},
    )
    assert result.returncode != 0
    assert "requires macOS" in result.stderr or "MACOS_DEVELOPER_ID_IDENTITY is required" in result.stderr
    assert "signed, notarized" not in result.stdout


def test_linux_installer_tar_omits_swift_build_products(tmp_path: Path) -> None:
    source = tmp_path / "src"
    planted = {
        "macos/TitanSkies/.build/release/TitanSkies": "swift-product",
        "macos/TitanSkies/.swiftpm/config/markers": "spm",
        "macos/TitanSkies/.build-icon/out/icon.icns": "icon",
        "macos/TitanSkies/Sources/keep.swift": "keep",
        "package.json": "{}\n",
    }
    for relative, body in planted.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    script = (ROOT / "scripts/install-user").read_text(encoding="utf-8")
    match = re.search(r"tar -C \"\$root\"(.*?)-cf - \.", script, re.S)
    assert match is not None
    excludes = [part for part in shlex.split(match.group(1)) if part.startswith("--exclude=")]
    assert "--exclude=macos/TitanSkies/.build" in excludes
    assert "--exclude=macos/TitanSkies/.swiftpm" in excludes
    archive = tmp_path / "payload.tar"
    subprocess.run(["tar", "-C", str(source), *excludes, "-cf", str(archive), "."], check=True)
    names = tarfile.open(archive).getnames()
    joined = "\n".join(names)
    assert "keep.swift" in joined
    assert "package.json" in joined
    assert ".build/" not in joined
    assert ".swiftpm" not in joined
    assert ".build-icon" not in joined


def test_beta_and_production_labels_match_smappservice_sources() -> None:
    identity = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Identity.swift").read_text(encoding="utf-8")
    app_model = (ROOT / "macos/TitanSkies/Sources/TitanSkies/AppModel.swift").read_text(encoding="utf-8")
    assert f'betaWebLabel = "{BETA_WEB_LABEL}"' in identity
    assert f'betaIngestLabel = "{BETA_INGEST_LABEL}"' in identity
    assert f'productionWebLabel = "{PRODUCTION_WEB_LABEL}"' in identity
    assert f'productionIngestLabel = "{PRODUCTION_INGEST_LABEL}"' in identity
    assert "SMAppService.agent(plistName: \"com.hypertrial.titanskies.web.plist\")" in app_model
    assert "SMAppService.agent(plistName: \"com.hypertrial.titanskies.ingest.plist\")" in app_model
    assert "registerProductionIfSigned()" in app_model[app_model.index("func start() async") : app_model.index("func saveSettings()")]
    assert "com.hypertrial.titanskies.beta.web.plist" not in app_model
    launchd = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Launchd.swift").read_text(encoding="utf-8")
    beta_web = launchd[launchd.index("public static func betaWeb") : launchd.index("public static func betaIngest")]
    beta_ingest = launchd[launchd.index("public static func betaIngest") : launchd.index("public static func write")]
    assert '"KeepAlive": true' in beta_web
    assert '"RunAtLoad": true' in beta_web
    assert "StartInterval" not in beta_ingest
    assert "KeepAlive" not in beta_ingest
    assert "Channel.load(fromApp:" in app_model
    assert "Channel.load(fromApp:" in (ROOT / "macos/TitanSkies/Sources/TitanSkiesUpdater/main.swift").read_text(encoding="utf-8")


def test_verified_update_downloads_hash_mounts_and_launches_helper() -> None:
    update = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Update.swift").read_text(encoding="utf-8")
    app_model = (ROOT / "macos/TitanSkies/Sources/TitanSkies/AppModel.swift").read_text(encoding="utf-8")
    settings = (ROOT / "macos/TitanSkies/Sources/TitanSkies/SettingsView.swift").read_text(encoding="utf-8")
    updater = (ROOT / "macos/TitanSkies/Sources/TitanSkiesUpdater/main.swift").read_text(encoding="utf-8")
    assert "hdiutil" in update
    assert "attach" in update
    assert "-readonly" in update
    assert "dmg hash mismatch" in update
    assert "TitanSkies.staged.app" in update
    assert "paths.updater" in update
    assert "func applyPendingUpdate()" in app_model
    assert "UpdateApplier().stage" in app_model
    assert "launchHelper" in app_model
    assert "Install verified update" in settings
    assert updater.index("StagedApp.validate(stagedURL)") < updater.index("moveItem(at: stagedURL, to: liveURL)")
    assert updater.index("StagedApp.validate(rollbackApp)") < updater.index("moveItem(at: rollbackApp, to: liveURL)")
    assert "never execute the rollback app from Application Support" in (
        ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Services.swift"
    ).read_text(encoding="utf-8")
    services = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Services.swift").read_text(encoding="utf-8")
    start = services[services.index("public func start()") : services.index("public func stop()")]
    assert start.index("Application Support") < start.index("writeBetaAgents")


def test_embedded_ed25519_key_is_bound_and_rejects_fixture_signatures(tmp_path: Path) -> None:
    lock = load_runtime_lock()
    key = load_update_public_key()
    identity = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Identity.swift").read_text(encoding="utf-8")
    update = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Update.swift").read_text(encoding="utf-8")
    app_model = (ROOT / "macos/TitanSkies/Sources/TitanSkies/AppModel.swift").read_text(encoding="utf-8")
    signer = (ROOT / "scripts/macos_update_sign.py").read_text(encoding="utf-8")
    assert key["algorithm"] == "Ed25519"
    assert key["publicKeyHex"] == lock["updatePublicKeyHex"]
    assert key["keyId"] == lock["updateKeyId"]
    assert FIXTURE_PUBLIC_HEX != lock["updatePublicKeyHex"]
    assert f'updatePublicKeyHex = "{lock["updatePublicKeyHex"]}"' in identity
    assert f'updateKeyId = "{lock["updateKeyId"]}"' in identity
    assert "publicKeyHex: String = TitanSkiesIdentity.updatePublicKeyHex" in update
    assert "embedded public key is invalid" in update
    assert 'lock["updatePublicKeyHex"]' in app_model
    assert "TitanSkiesIdentity.updatePublicKeyHex" in app_model[app_model.index("func checkUpdates()") :]
    assert "validate_update_manifest(payload" in signer
    assert "public_key_hex" not in signer
    pem = tmp_path / "embedded.pem"
    pem.write_bytes(ed25519_public_pem_from_hex(lock["updatePublicKeyHex"]))
    parsed = subprocess.run(
        ["openssl", "pkey", "-pubin", "-in", str(pem), "-text", "-noout"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ED25519" in (parsed.stdout + parsed.stderr)
    payload = _signed_manifest()
    assert b'"signature"' not in canonicalize_manifest_bytes(payload)
    with pytest.raises(PackagingError, match="signature"):
        validate_update_manifest(payload, current_version="0.1.0")
    validated = validate_update_manifest(payload, current_version="0.1.0", public_key_hex=FIXTURE_PUBLIC_HEX)
    assert validated["version"] == "0.1.1"
    with pytest.raises(PackagingError, match="embedded public key"):
        validate_update_manifest(payload, current_version="0.1.0", public_key_hex="abc")
    with pytest.raises(PackagingError, match="embedded public key"):
        validate_update_manifest(payload, current_version="0.1.0", public_key_hex="A" * 64)
    with pytest.raises(PackagingError, match="signature"):
        validate_update_manifest(payload, current_version="0.1.0", public_key_hex="0" * 64)
    with pytest.raises(PackagingError, match="key id"):
        validate_update_manifest({**payload, "keyId": "other-key"}, current_version="0.1.0", public_key_hex=FIXTURE_PUBLIC_HEX)
    with pytest.raises(PackagingError, match="signature"):
        validate_update_manifest({**payload, "signature": "00"}, current_version="0.1.0", public_key_hex=FIXTURE_PUBLIC_HEX)
    with pytest.raises(PackagingError, match="signature"):
        validate_update_manifest(
            {**payload, "signature": "g" * 128},
            current_version="0.1.0",
            public_key_hex=FIXTURE_PUBLIC_HEX,
        )


def test_uninstall_confirms_inactivity_before_purge_or_trash() -> None:
    preserve = uninstall_plan(purge=False)
    assert preserve.index("bootout-web") < preserve.index("confirm-inactive")
    assert preserve.index("bootout-ingest") < preserve.index("confirm-inactive")
    assert preserve.index("confirm-inactive") < preserve.index("remove-beta-plists")
    assert preserve.index("confirm-inactive") < preserve.index("remove-rollback")
    assert preserve.index("confirm-inactive") < preserve.index("move-app-to-trash")
    assert "delete-default-owned-paths" not in preserve
    purged = uninstall_plan(purge=True)
    assert purged.index("confirm-inactive") < purged.index("delete-default-owned-paths")
    services = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Services.swift").read_text(encoding="utf-8")
    uninstall_fn = services[services.index("public func uninstall") :]
    loaded_check = uninstall_fn[uninstall_fn.index("for label in") : uninstall_fn.index("for plist in")]
    identity_labels = (
        "TitanSkiesIdentity.betaWebLabel",
        "TitanSkiesIdentity.betaIngestLabel",
        "TitanSkiesIdentity.productionWebLabel",
        "TitanSkiesIdentity.productionIngestLabel",
    )
    for name in identity_labels:
        assert name in uninstall_fn
        assert name in loaded_check
        assert uninstall_fn.index(f"launchd.bootout({name})") < uninstall_fn.index("isLoaded")
    assert uninstall_fn.index("launchd.bootout") < uninstall_fn.index("isLoaded")
    assert uninstall_fn.index("isLoaded") < uninstall_fn.index("if purge")
    assert uninstall_fn.index("isLoaded") < uninstall_fn.index("removeItem")
    assert "is still loaded" in uninstall_fn
    launchd = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Launchd.swift").read_text(encoding="utf-8")
    is_loaded = launchd[launchd.index("public func isLoaded") : launchd.index("public enum LaunchdPlist")]
    assert '"print"' in is_loaded
    assert "result?.succeeded == true" in is_loaded
    app_model = (ROOT / "macos/TitanSkies/Sources/TitanSkies/AppModel.swift").read_text(encoding="utf-8")
    model_uninstall = app_model[app_model.index("func uninstall(purge: Bool)") : app_model.index("func checkUpdates()")]
    assert model_uninstall.index("unregisterProductionLoginItems") < model_uninstall.index("AppUninstaller")
    assert model_uninstall.index("channelIsProduction") < model_uninstall.index("unregisterProductionLoginItems")
    unregister = app_model[app_model.index("private func unregisterProductionLoginItems") :]
    assert "com.hypertrial.titanskies.web.plist" in unregister
    assert "com.hypertrial.titanskies.ingest.plist" in unregister
    assert ".unregister()" in unregister
    assert "beta.web.plist" not in unregister


def test_production_smappservice_registers_from_start_not_beta() -> None:
    app_model = (ROOT / "macos/TitanSkies/Sources/TitanSkies/AppModel.swift").read_text(encoding="utf-8")
    start = app_model[app_model.index("func start() async") : app_model.index("func saveSettings()")]
    assert start.index("isUntrustedInstallSource") < start.index("registerProductionIfSigned")
    assert start.index("channelIsProduction") < start.index("registerProductionIfSigned")
    assert start.index("registerProductionIfSigned") < start.index("services.start()")
    register = app_model[app_model.index("func registerProductionIfSigned()") : app_model.index("private func channelIsProduction")]
    assert register.index("channelIsProduction") < register.index("migrateBetaToProduction")
    assert register.index("migrateBetaToProduction") < register.index("web.register()")
    assert register.index("migrateBetaToProduction") < register.index("ingest.register()")
    assert 'SMAppService.agent(plistName: "com.hypertrial.titanskies.web.plist")' in register
    assert 'SMAppService.agent(plistName: "com.hypertrial.titanskies.ingest.plist")' in register
    assert "beta.web.plist" not in register
    assert "beta.ingest.plist" not in register
    assert ".requiresApproval" in register
    assert "openSystemSettingsLoginItems" in register
    channel = app_model[app_model.index("private func channelIsProduction") : app_model.index("private func currentVersion")]
    assert '["channel"]' in channel
    assert "loadRuntimeLock" in channel
    assert "Channel.production.rawValue" in channel
    assert "Channel.load(fromApp:" in app_model
    services = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Services.swift").read_text(encoding="utf-8")
    service_start = services[services.index("public func start()") : services.index("public func stop()")]
    beta_branch = service_start[service_start.index("if channel == .unsignedBeta") : service_start.index("} else {")]
    production_branch = service_start[service_start.index("} else {") : service_start.index("_ = try health.waitForWeb()")]
    assert "writeBetaAgents" in beta_branch
    assert "writeBetaAgents" not in production_branch
    assert "bootstrap" not in production_branch
    assert "productionWebLabel" in production_branch
    assert "productionIngestLabel" in production_branch
    assert "betaWebLabel" not in production_branch
    migration = production_migration_plan()
    assert migration.index("bootout-beta-web") < migration.index("register-production-web")
    assert migration.index("remove-beta-plists") < migration.index("register-production-web")
    assert "register-production-ingest" in migration


def test_update_apply_path_hashes_before_mount_and_sandboxes_helper() -> None:
    update = (ROOT / "macos/TitanSkies/Sources/TitanSkiesCore/Update.swift").read_text(encoding="utf-8")
    app_model = (ROOT / "macos/TitanSkies/Sources/TitanSkies/AppModel.swift").read_text(encoding="utf-8")
    settings = (ROOT / "macos/TitanSkies/Sources/TitanSkies/SettingsView.swift").read_text(encoding="utf-8")
    updater = (ROOT / "macos/TitanSkies/Sources/TitanSkiesUpdater/main.swift").read_text(encoding="utf-8")
    stage = update[update.index("public func stage(") : update.index("public func launchHelper")]
    assert stage.index("dmg URL must be https") < stage.index("downloader.download")
    assert stage.index("posixPermissions") < stage.index("downloader.download")
    assert "0o700" in stage
    assert stage.index("verifyDMG") < stage.index("hdiutil")
    assert stage.index('["attach"') < stage.index("copyItem")
    assert "-readonly" in stage
    assert "-nobrowse" in stage
    assert "detach" in stage
    assert stage.index("Application Support") < stage.index("copyItem")
    assert "TitanSkies.staged.app" in stage
    assert "staged app must stay on the Applications volume" in stage
    assert stage.index("StagedApp.validate(mountedApp") < stage.index("copyItem")
    assert "StagedApp.validate(staged" in stage[stage.index("copyItem") :]
    helper = update[update.index("public func launchHelper") :]
    assert "TitanSkiesUpdater" in helper
    assert helper.index("Application Support") < helper.index("Process()")
    assert helper.index("updater must stay inside the live app") < helper.index("process.run()")
    assert '"--live"' in helper
    assert '"--staged"' in helper
    assert '"--rollback"' in helper
    assert '"PATH": "/usr/bin:/bin"' in helper
    apply = app_model[app_model.index("func applyPendingUpdate()") : app_model.index("func registerProductionIfSigned()")]
    assert "Check for an update before installing" in apply
    assert apply.index("pendingUpdate") < apply.index("UpdateApplier().stage")
    assert apply.index("UpdateApplier().stage") < apply.index("launchHelper")
    assert "NSApp.terminate" in apply
    install = settings[settings.index("Install verified update") : settings.index("Unsigned beta")]
    assert ".disabled(model.pendingUpdate == nil)" in install
    assert updater.index('throw TitanSkiesError.updateRejected("updater requires --live --staged --rollback")') < updater.index(
        "StagedApp.validate(stagedURL)"
    )
    assert updater.index("live app must not be the rollback area") < updater.index("StagedApp.validate(stagedURL)")
    assert updater.index("StagedApp.validate(stagedURL)") < updater.index("moveItem(at: stagedURL, to: liveURL)")
    assert updater.index("StagedApp.validate(rollbackApp)") < updater.index("moveItem(at: rollbackApp, to: liveURL)")


def test_macos_host_smoke_skips_off_darwin_arm64_and_gates_host_checks(tmp_path: Path) -> None:
    script = ROOT / "scripts/macos-host-smoke"
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")
    assert "set -eu" in text
    assert "Darwin-arm64" in text
    assert "macos-host-smoke skipped" in text
    assert "is_untrusted_install_source" in text
    assert "codesign --verify --strict" in text
    assert "hdiutil attach -readonly -nobrowse" in text
    assert "LaunchAgent points at dist/macos" in text
    assert "github.com/actions" not in text
    assert ".github/workflows" not in text
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uname = bindir / "uname"
    uname.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-s" ]; then echo Linux; exit 0; fi\n'
        'if [ "$1" = "-m" ]; then echo x86_64; exit 0; fi\n'
        "echo Linux\n",
        encoding="utf-8",
    )
    uname.chmod(0o755)
    result = subprocess.run(
        ["sh", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"},
    )
    assert result.returncode == 0
    assert "macos-host-smoke skipped" in result.stdout
    assert "Darwin arm64 required" in result.stdout
    assert "passed" not in result.stdout
    assert "codesign" not in result.stdout
    assert "hdiutil" not in result.stdout


