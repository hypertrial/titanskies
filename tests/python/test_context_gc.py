from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ingest.local_store import LocalFrameStore, StoragePage
from ingest.context_gc import GRACE_SECONDS, STATE_PATH, reconcile_orphans
from ingest.context_publish import _cleanup_context_assets

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)
ORPHAN = "context/assets/" + "a" * 20 + "/orphan.png"
MANIFEST = "context/manifests/" + "b" * 20 + ".json"


def _v8_manifest_fixture() -> dict:
    pointer = json.loads(Path("public/demo/context/latest.json").read_text(encoding="utf-8"))
    return json.loads((Path("public/demo") / pointer["manifestPath"]).read_text(encoding="utf-8"))


@pytest.fixture
def publication(tmp_path):
    store = LocalFrameStore(tmp_path)
    manifest = _v8_manifest_fixture()
    pointer = {"version": 8, "manifestPath": MANIFEST, "manifestUrl": store.url_for(MANIFEST), "updatedAt": NOW.isoformat()}
    store.put_json(MANIFEST, manifest, cache_seconds=0, overwrite=True)
    store.put_json("context/latest.json", pointer, cache_seconds=0, overwrite=True)
    lease = store.acquire_lease("locks/context.json", "owner", NOW, NOW + timedelta(seconds=420))
    store.put_bytes(ORPHAN, b"orphan", "image/png", cache_seconds=0, overwrite=True)
    return store, lease, pointer, manifest


def run(publication, now=NOW, retention_hours=48):
    with patch("ingest.context_gc._now", return_value=now):
        reconcile_orphans(*publication, None, None, retention_hours=retention_hours)


def renew(publication, now):
    store, lease, _, _ = publication
    store.put_json(lease.pathname, {"owner": lease.owner, "expiresAt": (now + timedelta(seconds=420)).isoformat()}, cache_seconds=0, overwrite=True)


def test_missing_previous_manifest_prevents_cleanup(publication):
    store, lease, pointer, manifest = publication
    run(publication)
    later = NOW + timedelta(days=2)
    renew(publication, later)
    previous_path = "context/manifests/" + "c" * 20 + ".json"
    with patch("ingest.context_gc._now", return_value=later), patch.object(store, "delete_many") as deleted:
        reconcile_orphans(store, lease, pointer, manifest, None, previous_path)
        with pytest.raises(ValueError, match="references unavailable"):
            _cleanup_context_assets(store, manifest, MANIFEST, None, previous_path)
    deleted.assert_not_called()
    assert store.get_bytes(ORPHAN)


def test_rejected_cursor_restarts_discovery(publication):
    store, _, _, _ = publication
    run(publication)
    state = json.loads(store.get_text(STATE_PATH))
    state["cursors"][0] = "expired"
    state["nextPrefix"] = 0
    store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
    with patch.object(store, "list_page", side_effect=ValueError("rejected cursor")):
        run(publication)
    recovered = json.loads(store.get_text(STATE_PATH))
    assert recovered["cursors"][0] is None
    assert recovered["pending"][ORPHAN] == NOW.timestamp()


def test_orphan_discovery_grace_and_recovery(publication):
    store, _, pointer, manifest = publication
    _cleanup_context_assets(store, manifest, MANIFEST, None, None, discover_unknown=False)
    assert store.get_bytes(ORPHAN)
    run(publication)
    state = json.loads(store.get_text(STATE_PATH))
    assert state["pending"][ORPHAN] == NOW.timestamp()
    before = NOW + timedelta(seconds=GRACE_SECONDS - 1)
    renew(publication, before)
    run(publication, before)
    assert store.get_bytes(ORPHAN)
    after = before + timedelta(seconds=1)
    renew(publication, after)
    run(publication, after)
    assert store.get_bytes(ORPHAN) is None
    assert store.get_bytes(MANIFEST)
    assert json.loads(store.get_text("context/latest.json")) == pointer


def test_configured_retention_controls_orphan_deletion(publication):
    store = publication[0]
    run(publication, retention_hours=24)
    before = NOW + timedelta(hours=24) - timedelta(seconds=1)
    renew(publication, before)
    run(publication, before, retention_hours=24)
    assert store.get_bytes(ORPHAN)
    after = before + timedelta(seconds=1)
    renew(publication, after)
    run(publication, after, retention_hours=24)
    assert store.get_bytes(ORPHAN) is None


@pytest.mark.parametrize("change", ["lease_owner", "lease_expired", "pointer", "future_state", "unsafe_path"])
def test_unsafe_reconciliation_fails_closed(publication, change):
    store, lease, pointer, _ = publication
    run(publication)
    later = NOW + timedelta(days=2)
    renew(publication, later)
    if change == "lease_owner":
        store.put_json(lease.pathname, {"owner": "other", "expiresAt": (later + timedelta(minutes=5)).isoformat()}, cache_seconds=0, overwrite=True)
    elif change == "lease_expired":
        store.put_json(lease.pathname, {"owner": lease.owner, "expiresAt": NOW.isoformat()}, cache_seconds=0, overwrite=True)
    elif change == "pointer":
        store.put_json("context/latest.json", {**pointer, "updatedAt": "changed"}, cache_seconds=0, overwrite=True)
    else:
        state = json.loads(store.get_text(STATE_PATH))
        if change == "malformed_state": state["version"] = 2
        if change == "future_state": state["pending"][ORPHAN] = later.timestamp() + 1
        if change == "unsafe_path": state["pending"]["../secret"] = NOW.timestamp()
        store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
    run(publication, later)
    assert store.get_bytes(ORPHAN)


@pytest.mark.parametrize(
    "payload",
    [
        b"{broken",
        b"\xff",
        {"version": 2},
        {"version": 1, "nextPrefix": 0, "cursors": [None, None], "pending": [], "lastSweep": [None, None]},
    ],
)
def test_invalid_reconciliation_state_resets_and_resumes_discovery(publication, payload):
    store = publication[0]
    if isinstance(payload, bytes):
        store.put_bytes(STATE_PATH, payload, "application/json", cache_seconds=0, overwrite=True)
    else:
        store.put_json(STATE_PATH, payload, cache_seconds=0, overwrite=True)
    run(publication)
    state = json.loads(store.get_text(STATE_PATH))
    assert state["version"] == 1
    assert state["pending"][ORPHAN] == NOW.timestamp()
    later = NOW + timedelta(hours=48)
    renew(publication, later)
    run(publication, later)
    assert store.get_bytes(ORPHAN) is None


def test_oversized_reconciliation_state_resets_and_resumes_discovery(publication):
    store = publication[0]
    store.put_bytes(STATE_PATH, b"x" * (16 * 1024 * 1024 + 1), "application/json", cache_seconds=0, overwrite=True)
    run(publication)
    state = json.loads(store.get_text(STATE_PATH))
    assert state["version"] == 1
    assert state["pending"][ORPHAN] == NOW.timestamp()


def test_symlinked_reconciliation_state_is_replaced_without_touching_its_target(publication, tmp_path):
    store = publication[0]
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    state_path = store.root / STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.symlink_to(outside)

    run(publication)

    state = json.loads(store.get_text(STATE_PATH))
    assert state["version"] == 1
    assert state["pending"][ORPHAN] == NOW.timestamp()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_referenced_candidate_resets_grace(publication):
    store, _, _, manifest = publication
    run(publication)
    manifest["fires"]["wfigsPerimeterTextureUrl"] = store.url_for(ORPHAN)
    run(publication, NOW + timedelta(hours=23))
    assert ORPHAN not in json.loads(store.get_text(STATE_PATH))["pending"]
    del manifest["fires"]["wfigsPerimeterTextureUrl"]
    run(publication, NOW + timedelta(days=1))
    assert json.loads(store.get_text(STATE_PATH))["pending"][ORPHAN] == (NOW + timedelta(days=1)).timestamp()


def test_interrupted_checkpoint_does_not_lose_discovery(publication):
    store = publication[0]
    put = store.put_json
    count = 0
    def fail_second(path, *args, **kwargs):
        nonlocal count
        if path == STATE_PATH:
            count += 1
            if count == 2: raise OSError("state unavailable")
        return put(path, *args, **kwargs)
    with patch.object(store, "put_json", side_effect=fail_second):
        run(publication)
    assert json.loads(store.get_text(STATE_PATH))["cursors"] == [None, None]
    run(publication)
    assert ORPHAN in json.loads(store.get_text(STATE_PATH))["pending"]


def test_deleted_object_is_safe_to_retry_after_checkpoint_failure(publication):
    store = publication[0]
    run(publication)
    later = NOW + timedelta(seconds=GRACE_SECONDS)
    renew(publication, later)
    put = store.put_json
    count = 0
    def fail_second(path, *args, **kwargs):
        nonlocal count
        if path == STATE_PATH:
            count += 1
            if count == 2: raise OSError("lost deletion checkpoint")
        return put(path, *args, **kwargs)
    with patch.object(store, "put_json", side_effect=fail_second): run(publication, later)
    assert store.get_bytes(ORPHAN) is None
    assert ORPHAN in json.loads(store.get_text(STATE_PATH))["pending"]
    run(publication, later)
    assert ORPHAN not in json.loads(store.get_text(STATE_PATH))["pending"]


def test_pages_and_deletions_are_bounded(publication):
    store = publication[0]
    for i in range(270):
        store.put_bytes(f"context/assets/{i:020x}/test.png", b"x", "image/png", cache_seconds=0, overwrite=True)
    with patch.object(store, "list_page", wraps=store.list_page) as listed:
        run(publication)
        assert listed.call_count == 2
    assert len(json.loads(store.get_text(STATE_PATH))["pending"]) == 250
    run(publication)
    assert len(json.loads(store.get_text(STATE_PATH))["pending"]) == 271
    later = NOW + timedelta(seconds=GRACE_SECONDS)
    renew(publication, later)
    with patch.object(store, "delete_many", wraps=store.delete_many) as deleted:
        run(publication, later)
        assert len(deleted.call_args.args[0]) == 64


def test_full_queue_can_delete_and_partial_page_cannot_overflow(publication):
    store = publication[0]
    run(publication)
    state = json.loads(store.get_text(STATE_PATH))
    state["pending"] = {f"context/assets/{i:020x}/orphan.png": NOW.timestamp() for i in range(4096)}
    store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
    later = NOW + timedelta(seconds=GRACE_SECONDS)
    renew(publication, later)
    with patch.object(store, "delete_many", wraps=store.delete_many) as deleted:
        run(publication, later)
        assert len(deleted.call_args.args[0]) == 64
    state = json.loads(store.get_text(STATE_PATH))
    assert len(state["pending"]) <= 4096
    with patch.object(store, "list_page", return_value=StoragePage([ORPHAN] * 251, "bad")):
        run(publication, later)
    assert len(json.loads(store.get_text(STATE_PATH))["pending"]) <= 4096


def test_no_work_without_finalization_reserve(publication):
    from ingest.perf import ingest_run
    with ingest_run(budget_seconds=10), patch.object(publication[0], "get_authoritative_json") as get:
        run(publication)
        get.assert_not_called()
