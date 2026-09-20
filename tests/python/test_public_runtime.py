from __future__ import annotations

import hashlib
import io
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from ingest.config import Settings
from ingest.context_contracts import CONTEXT_JSON_BUDGET_BYTES, validate_context_manifest
from ingest.context_pipeline import run_context_ingest
from ingest.context_publish import previous_context, put_asset, seed_asset_memo
from ingest.context_sources import PROVENANCE, apply_light_sources, gather_light_sources
from ingest.context_types import SourceFailure, SourceOutcome
from ingest.local_store import LocalFrameStore
from ingest.perf import ingest_run
from ingest.sources.context_http import get
from ingest.sources.context_raster import image_png
from ingest.sources.firework import _validated_firework_sld_png, parse_firework_capabilities
from ingest.sources.wildfires import parse_cwfis, parse_wfigs

NOW = datetime(2026, 9, 10, 10, tzinfo=UTC)
EXPECTED_SOURCES = {"airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis", "firework", "hrrr"}


def test_perimeter_png_encoding_is_stable() -> None:
    image = Image.new("RGBA", (3, 2), (0, 0, 0, 0))
    image.putpixel((1, 0), (255, 188, 87, 210))
    assert hashlib.sha256(image_png(image)).hexdigest() == ("bf858141f594849a74707f9b6e33ac6fa58fd334fc7a6f02b6544e52d80d04c8")


@pytest.mark.parametrize(
    "contributors",
    [
        {},
        [{"source": "hrrr"}, {"source": "hrrr"}],
        [{"source": "tempo"}],
        [{"source": "hrrr", "modelRun": "not-a-time"}],
    ],
)
def test_python_contract_rejects_malformed_forecast_contributors(contributors: Any) -> None:
    pointer = json.loads(Path("public/demo/context/latest.json").read_text(encoding="utf-8"))
    manifest = json.loads((Path("public/demo") / pointer["manifestPath"]).read_text(encoding="utf-8"))
    manifest["forecast"]["frames"][0]["contributors"] = contributors
    with pytest.raises(ValueError, match="contributor"):
        validate_context_manifest(manifest)


def _monitor(source: str, system: str = "us-epa-pm25-aqi") -> dict[str, object]:
    return {
        "id": f"{source}:one",
        "name": "Fixture",
        "agency": "Fixture agency",
        "lat": 45.0,
        "lon": -100.0,
        "observedAt": "2026-09-10T09:00:00Z",
        "indexSystem": system,
        "indexValue": 10,
        "category": "Good" if system != "ca-aqhi" else "Low",
        "source": source,
        "sourceUrl": "https://example.invalid",
    }


def test_source_registry_and_runtime_provenance_cover_exactly_v8_sources() -> None:
    registry = json.loads(Path("shared/data-sources.json").read_text(encoding="utf-8"))
    assert {item["id"] for item in registry["sources"]} == EXPECTED_SOURCES == set(PROVENANCE)
    assert all(item["endpointHosts"] and item["termsUrl"].startswith("https://") for item in registry["sources"])
    assert next(item for item in registry["sources"] if item["id"] == "airnow")["credentials"] == "optional AIRNOW_API_KEY"


def test_shared_government_fetcher_rejects_unregistered_hosts() -> None:
    with pytest.raises(ValueError, match="blocked host"):
        get("https://example.invalid/private")


def test_all_enabled_light_sources_are_attempted_and_missing_airnow_isolated(tmp_path: Any) -> None:
    settings = Settings(local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache", airnow_api_key="")
    store = LocalFrameStore(tmp_path)
    manifest: dict[str, Any] = {"sources": {}, "air": {}, "fires": {}}
    calls: list[str] = []

    def missing_airnow(*_args: Any, **_kwargs: Any) -> None:
        calls.append("airnow")
        raise PermissionError("AIRNOW_API_KEY is not configured")

    def source(name: Any, result: Any) -> Any:
        def run(*_args: Any, **_kwargs: Any) -> Any:
            calls.append(name)
            return result

        return run

    incident = {"id": "one", "updatedAt": "2026-09-10T09:00:00Z"}
    with (
        patch("ingest.context_sources.fetch_airnow", side_effect=missing_airnow),
        patch("ingest.context_sources.fetch_bc_air", side_effect=source("bcair", [_monitor("bcair")])),
        patch("ingest.context_sources.fetch_sinaica", side_effect=source("sinaica", [_monitor("sinaica")])),
        patch("ingest.context_sources.fetch_aqhi", side_effect=source("aqhi", [_monitor("aqhi", "ca-aqhi")])),
        patch("ingest.context_sources.fetch_wfigs_parts", side_effect=source("wfigs", ([incident], [], None))),
        patch("ingest.context_sources.fetch_cwfis", side_effect=source("cwfis", ([incident], [], NOW))),
        patch("ingest.context_sources.fetch_cwfis_perimeter_texture", return_value=b"fixture-png"),
    ):
        outcomes = gather_light_sources(manifest, settings, store, NOW, None)
    apply_light_sources(manifest, outcomes, settings, NOW, None)

    assert set(calls) == {"airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis"}
    assert manifest["sources"]["airnow"]["status"] == "unavailable"
    assert all(manifest["sources"][name]["status"] == "ok" for name in ("bcair", "sinaica", "aqhi", "wfigs", "cwfis"))


def test_apply_light_sources_uses_fallback_when_gather_drops_a_source() -> None:
    settings = Settings()
    manifest: dict[str, Any] = {"sources": {}, "air": {}, "fires": {}}
    apply_light_sources(manifest, {}, settings, NOW, None)
    assert manifest["sources"]["airnow"]["status"] == "error"
    assert "unavailable" in str(manifest["sources"]["airnow"]["error"])


def test_multiple_source_failures_do_not_discard_other_sections(tmp_path: Any) -> None:
    settings = Settings(local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache")
    manifest: dict[str, Any] = {"sources": {}, "air": {}, "fires": {}}
    previous: dict[str, Any] = {
        "sources": {
            "airnow": {"observedAt": "2026-09-10T09:00:00Z"},
            "wfigs": {"observedAt": "2026-09-10T09:00:00Z"},
        },
        "air": {"monitorsUrl": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/airnow-monitors.json"},
        "fires": {"wfigsIncidentsUrl": "/data/context/assets/bbbbbbbbbbbbbbbbbbbb/wfigs-incidents.json"},
    }
    outcomes: dict[str, SourceOutcome] = {
        "airnow": SourceFailure(RuntimeError("air failed secret=redacted")),
        "bcair": SourceFailure(RuntimeError("bc failed")),
        "sinaica": SourceFailure(RuntimeError("mx failed")),
        "aqhi": SourceFailure(RuntimeError("aqhi failed")),
        "wfigs": SourceFailure(RuntimeError("us fire failed")),
        "cwfis": SourceFailure(RuntimeError("ca fire failed")),
    }
    apply_light_sources(manifest, outcomes, settings, NOW, previous)
    assert manifest["air"]["monitorsUrl"] == previous["air"]["monitorsUrl"]
    assert manifest["fires"]["wfigsIncidentsUrl"] == previous["fires"]["wfigsIncidentsUrl"]
    assert manifest["sources"]["airnow"]["status"] == "error"
    assert manifest["sources"]["wfigs"]["status"] == "error"
    assert manifest["sources"]["cwfis"]["status"] == "error"


def test_missing_airnow_key_never_restores_previous_observations(tmp_path: Any) -> None:
    settings = Settings(local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache", airnow_api_key="")
    previous: dict[str, Any] = {
        "sources": {"airnow": {"observedAt": "2026-09-10T09:00:00Z"}},
        "air": {
            "monitorsUrl": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/airnow-monitors.json",
            "monitorSets": {"airnow": {"url": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/airnow-monitors.json"}},
        },
    }
    outcomes: dict[str, SourceOutcome] = {
        name: SourceFailure(PermissionError("AIRNOW_API_KEY is not configured") if name == "airnow" else RuntimeError("offline"))
        for name in ("airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis")
    }
    manifest: dict[str, Any] = {"sources": {}, "air": {}, "fires": {}}
    apply_light_sources(manifest, outcomes, settings, NOW, previous)
    assert manifest["sources"]["airnow"]["status"] == "unavailable"
    assert "monitorsUrl" not in manifest["air"]
    assert "airnow" not in manifest["air"].get("monitorSets", {})


def test_non_airnow_permission_failure_preserves_previous_observations(tmp_path: Any) -> None:
    settings = Settings(local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache", airnow_api_key="")
    previous: dict[str, Any] = {
        "sources": {"bcair": {"observedAt": "2026-09-10T09:00:00Z"}},
        "air": {
            "bcMonitorsUrl": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/bc-monitors.json",
            "monitorSets": {"bcair": {"url": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/bc-monitors.json"}},
        },
    }
    outcomes: dict[str, SourceOutcome] = {
        name: SourceFailure(PermissionError("publication directory is read-only") if name == "bcair" else RuntimeError("offline"))
        for name in ("airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis")
    }
    manifest: dict[str, Any] = {"sources": {}, "air": {}, "fires": {}}
    apply_light_sources(manifest, outcomes, settings, NOW, previous)
    assert manifest["sources"]["bcair"]["status"] == "error"
    assert manifest["air"]["bcMonitorsUrl"] == previous["air"]["bcMonitorsUrl"]
    assert manifest["air"]["monitorSets"]["bcair"] == previous["air"]["monitorSets"]["bcair"]


@pytest.mark.parametrize(
    ("api_key", "error"),
    [
        ("configured", PermissionError("upstream denied access")),
        ("", RuntimeError("upstream unavailable")),
    ],
)
def test_airnow_failure_is_unavailable_only_for_a_missing_key_permission_error(tmp_path: Any, api_key: Any, error: Any) -> None:
    settings = Settings(local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache", airnow_api_key=api_key)
    previous: dict[str, Any] = {
        "sources": {"airnow": {"observedAt": "2026-09-10T09:00:00Z"}},
        "air": {
            "monitorsUrl": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/airnow-monitors.json",
            "monitorSets": {"airnow": {"url": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/airnow-monitors.json"}},
        },
    }
    outcomes: dict[str, SourceOutcome] = {
        name: SourceFailure(error if name == "airnow" else RuntimeError("offline"))
        for name in ("airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis")
    }
    manifest: dict[str, Any] = {"sources": {}, "air": {}, "fires": {}}

    apply_light_sources(manifest, outcomes, settings, NOW, previous)

    assert manifest["sources"]["airnow"]["status"] == "error"
    assert manifest["air"]["monitorsUrl"] == previous["air"]["monitorsUrl"]
    assert manifest["air"]["monitorSets"]["airnow"] == previous["air"]["monitorSets"]["airnow"]


def test_previous_publication_is_reused_only_for_matching_mode_and_valid_assets(tmp_path: Any) -> None:
    shutil.copytree("public/demo", tmp_path, dirs_exist_ok=True)
    captured: list[dict | None] = []

    def stop_after_admission(_settings: Any, _store: Any, _now: Any, previous: Any, _cache: Any) -> None:
        captured.append(previous)
        raise RuntimeError("stop after admission")

    settings = Settings(context_source="live", local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache")
    with patch("ingest.context_pipeline._live_manifest", side_effect=stop_after_admission):
        result = run_context_ingest(settings, NOW)
    assert not result["ok"] and captured == [None]

    settings = Settings(context_source="demo", local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache")
    captured.clear()
    with patch(
        "ingest.context_pipeline.demo_manifest",
        side_effect=lambda _store, _now, _settings, previous: stop_after_admission(_settings, _store, _now, previous, None),
    ):
        result = run_context_ingest(settings, NOW)
    assert not result["ok"] and captured[0] is not None

    pointer = json.loads((tmp_path / "context/latest.json").read_text())
    manifest = json.loads((tmp_path / pointer["manifestPath"]).read_text())
    first_url = next(_asset_urls(manifest))
    first_asset = tmp_path / ("context/assets/" + first_url.split("context/assets/", 1)[1])
    first_asset.write_bytes(b"corrupt")
    settings = Settings(context_source="demo", local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache")
    captured.clear()
    with patch(
        "ingest.context_pipeline.demo_manifest",
        side_effect=lambda _store, _now, _settings, previous: stop_after_admission(_settings, _store, _now, previous, None),
    ):
        result = run_context_ingest(settings, NOW)
    assert not result["ok"] and captured == [None]


def _asset_urls(value: Any) -> Any:
    if isinstance(value, dict):
        for nested in value.values():
            yield from _asset_urls(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _asset_urls(nested)
    elif isinstance(value, str) and "context/assets/" in value:
        yield value


def test_corrupt_asset_and_manifest_state_self_repair(tmp_path: Any) -> None:
    store = LocalFrameStore(tmp_path)
    data = b'{"ok":true}'
    digest = hashlib.sha256(data).hexdigest()[:20]
    path = f"context/assets/{digest}/fixture.json"
    store.put_bytes(path, b"corrupt", "application/json", cache_seconds=0, overwrite=True)
    with ingest_run():
        assert put_asset(store, "fixture.json", data, "application/json") == store.url_for(path)
    assert store.get_bytes(path) == data

    manifest_path = "context/manifests/" + "a" * 20 + ".json"
    store.put_bytes(manifest_path, b"{broken", "application/json", cache_seconds=0, overwrite=True)
    store.put_json("context/latest.json", {"manifestPath": manifest_path}, cache_seconds=0, overwrite=True)
    assert previous_context(store) == (None, manifest_path)

    store.put_json("context/latest.json", {"manifestPath": "context/status.json"}, cache_seconds=0, overwrite=True)
    assert previous_context(store) == (None, None)

    store.put_bytes("context/latest.json", b"x" * (CONTEXT_JSON_BUDGET_BYTES + 1), "application/json", cache_seconds=0, overwrite=True)
    assert previous_context(store) == (None, None)

    store.put_bytes("context/latest.json", b"\xff", "application/json", cache_seconds=0, overwrite=True)
    assert previous_context(store) == (None, None)

    store.put_bytes(manifest_path, b"x" * (CONTEXT_JSON_BUDGET_BYTES + 1), "application/json", cache_seconds=0, overwrite=True)
    store.put_json("context/latest.json", {"manifestPath": manifest_path}, cache_seconds=0, overwrite=True)
    assert previous_context(store) == (None, manifest_path)

    store.put_bytes(manifest_path, b"\xff", "application/json", cache_seconds=0, overwrite=True)
    assert previous_context(store) == (None, manifest_path)


@pytest.mark.parametrize("pointer, manifest", [([1], None), ({"manifestPath": "context/manifests/" + "b" * 20 + ".json"}, [])])
def test_wrong_shaped_publication_metadata_does_not_block_self_repair(tmp_path: Any, pointer: Any, manifest: Any) -> None:
    store = LocalFrameStore(tmp_path)
    if manifest is not None:
        store.put_json(pointer["manifestPath"], manifest, cache_seconds=0, overwrite=True)
    store.put_json("context/latest.json", pointer, cache_seconds=0, overwrite=True)
    expected_path = pointer.get("manifestPath") if isinstance(pointer, dict) else None
    assert previous_context(store) == (None, expected_path)


def test_store_atomically_replaces_final_symlink_and_rejects_ancestor_symlink(tmp_path: Any) -> None:
    store = LocalFrameStore(tmp_path)
    store.put_bytes("context/latest.json", b"latest", "application/json", cache_seconds=0, overwrite=True)
    (tmp_path / "context/status.json").symlink_to("latest.json")
    with pytest.raises(ValueError, match="invalid store path"):
        store.put_bytes("context/status.json", b"status", "application/json", cache_seconds=0, overwrite=False)
    store.put_bytes("context/status.json", b"status", "application/json", cache_seconds=0, overwrite=True)
    assert store.get_bytes("context/latest.json") == b"latest"
    assert store.get_bytes("context/status.json") == b"status"

    (tmp_path / "alias").symlink_to("context", target_is_directory=True)
    with pytest.raises(ValueError, match="invalid store path"):
        store.get_bytes("alias/latest.json")
    with pytest.raises(ValueError, match="invalid store path"):
        store.put_bytes("alias/latest.json", b"replaced", "application/json", cache_seconds=0, overwrite=True)
    assert store.get_bytes("context/latest.json") == b"latest"


def test_previous_publication_with_symlinked_asset_is_rebuilt(tmp_path: Any) -> None:
    store = LocalFrameStore(tmp_path)
    data = b'{"ok":true}'
    digest = hashlib.sha256(data).hexdigest()[:20]
    asset_path = f"context/assets/{digest}/fixture.json"
    target = tmp_path / "outside.json"
    target.write_bytes(b"outside")
    symlink = tmp_path / asset_path
    symlink.parent.mkdir(parents=True)
    symlink.symlink_to(target)
    with ingest_run():
        assert not seed_asset_memo(store, {"fixtureUrl": store.url_for(asset_path)})
        assert put_asset(store, "fixture.json", data, "application/json") == store.url_for(asset_path)
    assert store.get_bytes(asset_path) == data
    assert target.read_bytes() == b"outside"


def test_local_store_is_atomic_persistent_and_confines_paths(tmp_path: Any) -> None:
    store = LocalFrameStore(tmp_path)
    store.put_bytes("context/latest.json", b"old", "application/json", cache_seconds=0, overwrite=True)
    store.put_bytes("context/latest.json", b"new", "application/json", cache_seconds=0, overwrite=True)
    assert LocalFrameStore(tmp_path).get_bytes("context/latest.json") == b"new"
    assert not list((tmp_path / "context").glob("*.tmp"))
    store.put_bytes("context/latest.json", b"ignored", "application/json", cache_seconds=0, overwrite=False)
    assert store.get_bytes("context/latest.json") == b"new"
    for pathname in ("../secret", "/absolute", "context/../../secret"):
        with pytest.raises(ValueError, match="invalid store path"):
            store.get_bytes(pathname)


def test_local_store_lease_is_exclusive_expires_and_checks_owner(tmp_path: Any) -> None:
    first_store = LocalFrameStore(tmp_path)
    second_store = LocalFrameStore(tmp_path)
    first = first_store.acquire_lease("locks/context.json", "first", NOW, NOW + timedelta(minutes=2))
    assert first is not None
    assert second_store.acquire_lease("locks/context.json", "second", NOW, NOW + timedelta(minutes=2)) is None
    second_store.release_lease(type(first)(first.pathname, "wrong-owner"))
    assert first_store.get_bytes("locks/context.json") is not None
    replacement = second_store.acquire_lease("locks/context.json", "second", NOW + timedelta(minutes=3), NOW + timedelta(minutes=5))
    assert replacement is not None
    first_store.release_lease(first)
    assert second_store.get_bytes("locks/context.json") is not None
    second_store.release_lease(replacement)
    assert second_store.get_bytes("locks/context.json") is None


def test_firework_capabilities_and_all_black_fallback_guard() -> None:
    xml = """
    <WMS_Capabilities xmlns="http://www.opengis.net/wms">
      <Capability><Layer><Layer><Name>RAQDPS.Sfc_PM2.5-WildfireSmokePlume</Name>
      <Dimension name="time">2026-09-10T10:00:00Z,2026-09-10T11:00:00Z</Dimension>
      <Dimension name="reference_time">2026-09-10T06:00:00Z</Dimension>
      </Layer></Layer></Capability>
    </WMS_Capabilities>
    """
    assert parse_firework_capabilities(xml) == (
        "2026-09-10T06:00:00Z",
        ["2026-09-10T10:00:00Z", "2026-09-10T11:00:00Z"],
    )
    output = io.BytesIO()
    Image.new("RGBA", (2, 2), (0, 0, 0, 255)).save(output, format="PNG")
    with pytest.raises(ValueError, match="invalid FireWork"):
        _validated_firework_sld_png(output.getvalue(), (2, 2))


def test_wfigs_and_cwfis_fixture_parsers_preserve_provider_units() -> None:
    timestamp_ms = int(NOW.timestamp() * 1000)
    wfigs, rings = parse_wfigs(
        {
            "features": [
                {
                    "attributes": {
                        "OBJECTID": 1,
                        "IrwinID": "US1",
                        "IncidentName": "alpha",
                        "IncidentSize": 10,
                        "ModifiedOnDateTime_dt": timestamp_ms,
                    },
                    "geometry": {"x": -120, "y": 45},
                }
            ]
        },
        {"features": [{"attributes": {"OBJECTID": 2}, "geometry": {"rings": [[[-121, 44], [-120, 44], [-120, 45], [-121, 44]]]}}]},
    )
    assert wfigs[0]["sourceArea"] == 10
    assert wfigs[0]["sourceAreaUnit"] == "acres"
    assert wfigs[0]["areaHectares"] == 4.047
    assert rings

    cwfis, cwfis_rings = parse_cwfis(
        {
            "features": [
                {
                    "properties": {"national_fire_id": "CA1", "fire_size": 11, "status_date": "2026-09-10T09:00:00Z"},
                    "geometry": {"type": "Polygon", "coordinates": [[[-121, 44], [-120, 44], [-120, 45], [-121, 44]]]},
                }
            ]
        }
    )
    assert cwfis[0]["sourceArea"] == cwfis[0]["areaHectares"] == 11
    assert cwfis[0]["sourceAreaUnit"] == "hectares"
    assert cwfis_rings
