from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
import io

from ingest.local_store import LocalFrameStore
from ingest.config import Settings
from ingest.context_sources import PROVENANCE, apply_light_sources, gather_light_sources
from ingest.context_types import SourceFailure
from ingest.sources.firework import _validated_firework_sld_png, parse_firework_capabilities
from ingest.sources.context_http import _get
from ingest.sources.wildfires import parse_cwfis, parse_wfigs

NOW = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
EXPECTED_SOURCES = {"airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis", "firework", "hrrr"}


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
        _get("https://example.invalid/private")


def test_all_enabled_light_sources_are_attempted_and_missing_airnow_isolated(tmp_path) -> None:
    settings = Settings(local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache", airnow_api_key="")
    store = LocalFrameStore(tmp_path)
    manifest: dict[str, object] = {"sources": {}, "air": {}, "fires": {}}
    calls: list[str] = []

    def missing_airnow(*_args, **_kwargs):
        calls.append("airnow")
        raise PermissionError("AIRNOW_API_KEY is not configured")

    def source(name, result):
        def run(*_args, **_kwargs):
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
        outcomes = gather_light_sources(manifest, settings, store, NOW, None, 8)
    apply_light_sources(manifest, outcomes, settings, NOW, None, 8)

    assert set(calls) == {"airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis"}
    assert manifest["sources"]["airnow"]["status"] == "unavailable"
    assert all(manifest["sources"][name]["status"] == "ok" for name in ("bcair", "sinaica", "aqhi", "wfigs", "cwfis"))


def test_multiple_source_failures_do_not_discard_other_sections(tmp_path) -> None:
    settings = Settings(local_frame_dir=tmp_path, local_cache_dir=tmp_path / "cache")
    manifest: dict[str, object] = {"sources": {}, "air": {}, "fires": {}}
    previous = {
        "sources": {
            "airnow": {"observedAt": "2026-09-10T09:00:00Z"},
            "wfigs": {"observedAt": "2026-09-10T09:00:00Z"},
        },
        "air": {"monitorsUrl": "/data/context/assets/aaaaaaaaaaaaaaaaaaaa/airnow-monitors.json"},
        "fires": {"wfigsIncidentsUrl": "/data/context/assets/bbbbbbbbbbbbbbbbbbbb/wfigs-incidents.json"},
    }
    outcomes = {
        "airnow": SourceFailure(RuntimeError("air failed secret=redacted")),
        "bcair": SourceFailure(RuntimeError("bc failed")),
        "sinaica": SourceFailure(RuntimeError("mx failed")),
        "aqhi": SourceFailure(RuntimeError("aqhi failed")),
        "wfigs": SourceFailure(RuntimeError("us fire failed")),
        "cwfis": SourceFailure(RuntimeError("ca fire failed")),
    }
    apply_light_sources(manifest, outcomes, settings, NOW, previous, 8)
    assert manifest["air"]["monitorsUrl"] == previous["air"]["monitorsUrl"]
    assert manifest["fires"]["wfigsIncidentsUrl"] == previous["fires"]["wfigsIncidentsUrl"]
    assert manifest["sources"]["airnow"]["status"] == "error"
    assert manifest["sources"]["wfigs"]["status"] == "error"
    assert manifest["sources"]["cwfis"]["status"] == "error"


def test_local_store_is_atomic_persistent_and_confines_paths(tmp_path) -> None:
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


def test_local_store_lease_is_exclusive_expires_and_checks_owner(tmp_path) -> None:
    first_store = LocalFrameStore(tmp_path)
    second_store = LocalFrameStore(tmp_path)
    first = first_store.acquire_lease("locks/context.json", "first", NOW, NOW + timedelta(minutes=2))
    assert first is not None
    assert second_store.acquire_lease("locks/context.json", "second", NOW, NOW + timedelta(minutes=2)) is None
    second_store.release_lease(type(first)(first.pathname, "wrong-owner"))
    assert first_store.get_bytes("locks/context.json") is not None
    replacement = second_store.acquire_lease(
        "locks/context.json", "second", NOW + timedelta(minutes=3), NOW + timedelta(minutes=5)
    )
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
    wfigs, rings = parse_wfigs({"features": [{
        "attributes": {"OBJECTID": 1, "IrwinID": "US1", "IncidentName": "alpha", "IncidentSize": 10, "ModifiedOnDateTime_dt": timestamp_ms},
        "geometry": {"x": -120, "y": 45},
    }]}, {"features": [{"attributes": {"OBJECTID": 2}, "geometry": {"rings": [[[-121, 44], [-120, 44], [-120, 45], [-121, 44]]]}}]})
    assert wfigs[0]["sourceArea"] == 10
    assert wfigs[0]["sourceAreaUnit"] == "acres"
    assert wfigs[0]["areaHectares"] == 4.047
    assert rings

    cwfis, cwfis_rings = parse_cwfis({"features": [{
        "properties": {"national_fire_id": "CA1", "fire_size": 11, "status_date": "2026-09-10T09:00:00Z"},
        "geometry": {"type": "Polygon", "coordinates": [[[-121, 44], [-120, 44], [-120, 45], [-121, 44]]]},
    }]})
    assert cwfis[0]["sourceArea"] == cwfis[0]["areaHectares"] == 11
    assert cwfis[0]["sourceAreaUnit"] == "hectares"
    assert cwfis_rings
