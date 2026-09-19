from datetime import UTC, datetime, timedelta

from ingest.context_publish import context_status_from_manifest, context_status_metrics, context_status_payload


def test_context_status_tracks_fresh_retained_failed_and_recovery() -> None:
    start = datetime(2026, 8, 21, 15, tzinfo=UTC)
    manifest = {
        "version": 8,
        "forecast": {
            "frames": [
                {"validTime": "2026-08-21T15:00:00Z"},
                {"validTime": "2026-08-23T03:00:00Z"},
            ]
        },
    }
    fresh = context_status_payload(None, start, "fresh", manifest=manifest, metrics={"publicationStatus": "fresh"})
    assert fresh["lastCompleteForecastAt"] == "2026-08-21T15:00:00Z"
    assert fresh["consecutiveNonFresh"] == 0
    assert fresh["forecastLastValidTime"] == "2026-08-23T03:00:00Z"

    retained = context_status_payload(fresh, start + timedelta(minutes=15), "retained", manifest=manifest)
    assert retained["lastCompleteForecastAt"] == fresh["lastCompleteForecastAt"]
    assert retained["consecutiveNonFresh"] == 1

    failed = context_status_payload(retained, start + timedelta(minutes=30), "failed", error=RuntimeError("secret"))
    assert failed["lastSuccessfulPublicationAt"] == retained["lastSuccessfulPublicationAt"]
    assert failed["failureCategory"] == "RuntimeError"
    assert "secret" not in str(failed)
    assert failed["consecutiveNonFresh"] == 2

    recovered = context_status_payload(failed, start + timedelta(minutes=45), "fresh", manifest=manifest)
    assert recovered["lastCompleteForecastAt"] == "2026-08-21T15:45:00Z"
    assert recovered["consecutiveNonFresh"] == 0


def test_demo_status_omits_only_nondeterministic_timings() -> None:
    metrics = {
        "elapsedSeconds": 1.23,
        "cpuSeconds": 0.8,
        "phaseSeconds": {"build": 1.0},
        "sourceSeconds": {},
        "providerPhaseSeconds": {},
        "forecastWaveSeconds": None,
        "deadlineSkips": ["cleanup"],
        "splitRecommended": True,
        "forecastExpectedFrames": 37,
        "publicationStatus": "fresh",
    }
    stable = context_status_metrics({"mode": "demo"}, metrics)
    assert stable == {"forecastExpectedFrames": 37, "publicationStatus": "fresh"}
    assert context_status_metrics({"mode": "live"}, metrics) == metrics


def test_existing_manifest_bootstraps_first_nonfresh_attempt_as_degraded() -> None:
    manifest = {
        "version": 8,
        "generatedAt": "2026-08-21T15:00:00Z",
        "forecast": {
            "frames": [
                {"validTime": "2026-08-21T15:00:00Z"},
                {"validTime": "2026-08-22T15:00:00Z"},
            ]
        },
    }
    baseline = context_status_from_manifest(manifest)
    failed = context_status_payload(
        baseline,
        datetime(2026, 8, 21, 15, 15, tzinfo=UTC),
        "failed",
        error=RuntimeError("offline"),
    )
    assert failed["lastCompleteForecastAt"] == "2026-08-21T15:00:00Z"
    assert failed["contextVersion"] == 8
    assert failed["forecastFrameCount"] == 2
    assert failed["consecutiveNonFresh"] == 1
