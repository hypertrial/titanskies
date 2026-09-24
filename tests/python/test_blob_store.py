from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlparse

import pytest
import requests

from ingest import __version__
from ingest.blob_store import BlobFrameStore, storage_operation_budget
from ingest.config import Settings
from ingest.context_pipeline import run_context_ingest
from ingest.local_store import IngestLease, LocalFrameStore, open_store


def _settings() -> Settings:
    return Settings(
        storage_backend="blob",
        blob_token="vercel_blob_rw_test_secret",
        blob_store_id="example",
        public_blob_base_url="https://example.public.blob.vercel-storage.com",
    )


def _http(status: int, body: bytes = b"", headers: dict | None = None, json_payload: Any = None) -> Mock:
    response = Mock(status_code=status, headers=headers or {})
    if json_payload is not None and not body:
        body = json.dumps(json_payload).encode()
    response.iter_content = Mock(return_value=iter([body] if body else []))
    if json_payload is not None:
        response.json.return_value = json_payload
    return response


def _session(*, get: Any = None, put: Any = None, post: Any = None) -> Mock:
    session = Mock()
    session.get = get or Mock()
    session.put = put or Mock()
    session.post = post or Mock()
    return session


class _MemoryBlobSession:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.gets = 0
        self.heads = 0
        self.puts = 0
        self.posts = 0

    @staticmethod
    def _response(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response._content = body
        response._content_consumed = True
        response.headers.update(headers or {})
        return response

    @staticmethod
    def _path(url: str) -> str:
        return unquote(urlparse(url).path.lstrip("/"))

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        self.puts += 1
        assert url == "https://vercel.com/api/blob"
        pathname = kwargs["params"]["pathname"]
        if pathname in self.objects and kwargs["headers"].get("x-allow-overwrite") == "0":
            return self._response(409)
        self.objects[pathname] = bytes(kwargs.get("data", b""))
        body = json.dumps(
            {
                "url": f"https://example.public.blob.vercel-storage.com/{pathname}",
                "pathname": pathname,
                "etag": f'"{len(self.objects[pathname])}"',
            }
        ).encode()
        return self._response(200, body, {"etag": f'"{len(self.objects[pathname])}"'})

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        self.heads += 1
        pathname = self._path(url)
        return self._response(200 if pathname in self.objects else 404)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        self.gets += 1
        params = kwargs.get("params")
        if url == "https://vercel.com/api/blob" and params is not None:
            prefix = params["prefix"]
            limit = int(params["limit"])
            cursor = params.get("cursor")
            paths = sorted(
                pathname for pathname in self.objects if pathname.startswith(prefix + "/") and (cursor is None or pathname > cursor)
            )
            page = paths[:limit]
            more = len(paths) > limit
            body = json.dumps(
                {
                    "blobs": [{"pathname": pathname} for pathname in page],
                    "hasMore": more,
                    "cursor": page[-1] if more else None,
                }
            ).encode()
            return self._response(200, body)
        pathname = self._path(url)
        payload = self.objects.get(pathname)
        return self._response(404 if payload is None else 200, payload or b"")

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        self.posts += 1
        if url != "https://vercel.com/api/blob/delete":
            return self._response(404)
        for item in kwargs["json"]["urls"]:
            self.objects.pop(self._path(item), None)
        return self._response(200)


@pytest.mark.parametrize("status", [400, 410])
def test_blob_rejected_page_cursor_is_resettable(status: Any) -> None:
    response = requests.Response()
    response.status_code = status
    response._content = b"invalid cursor"
    response._content_consumed = True
    with patch("ingest.blob_store._blob_session", return_value=_session(get=Mock(return_value=response))):
        with pytest.raises(ValueError, match="cursor"):
            BlobFrameStore(_settings()).list_page("context/assets", "expired")


def test_blob_page_validates_paths_and_closes_response() -> None:
    body = {"blobs": [{"pathname": "context/assets/abc/a.png"}], "hasMore": True, "cursor": "next"}
    response = _http(200, json.dumps(body).encode())
    session = _session(get=Mock(return_value=response))
    with patch("ingest.blob_store._blob_session", return_value=session):
        page = BlobFrameStore(_settings()).list_page("context/assets", limit=1)
    assert page.cursor == "next" and page.paths == ["context/assets/abc/a.png"]
    assert session.get.call_args.kwargs["params"]["limit"] == "1"
    response.close.assert_called_once()


def test_authoritative_read_never_falls_back_or_retries() -> None:
    session = _session(get=Mock(side_effect=requests.Timeout("offline")))
    with patch("ingest.blob_store._blob_session", return_value=session), storage_operation_budget(5):
        with pytest.raises(requests.Timeout):
            BlobFrameStore(_settings()).get_authoritative_json("context/latest.json")
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["timeout"] <= 2.5


def test_blob_lease_release_uses_etag_conditional_delete() -> None:
    response = _http(200)
    store = BlobFrameStore(_settings())
    lease = IngestLease("locks/ingest.json", "owner", '"lease-etag"')
    session = _session(post=Mock(return_value=response))

    with patch("ingest.blob_store._blob_session", return_value=session):
        store.release_lease(lease)

    session.get.assert_not_called()
    response.raise_for_status.assert_called_once_with()
    assert session.post.call_args.kwargs["headers"]["x-if-match"] == '"lease-etag"'
    assert "If-Match" not in session.post.call_args.kwargs["headers"]
    assert session.post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status", [409, 412])
def test_blob_lease_release_tolerates_a_replaced_lease(status: int) -> None:
    response = _http(status)
    response.raise_for_status = Mock(side_effect=AssertionError("conditional conflict was raised"))
    session = _session(post=Mock(return_value=response))
    with patch("ingest.blob_store._blob_session", return_value=session):
        BlobFrameStore(_settings()).release_lease(IngestLease("locks/ingest.json", "owner", '"old"'))
    response.raise_for_status.assert_not_called()


def test_blob_stale_takeover_deletes_only_the_observed_etag() -> None:
    conflict = _http(409)
    current = _http(
        200,
        headers={"etag": '"stale-etag"'},
        json_payload={
            "owner": "stale",
            "expiresAt": "2024-07-15T17:00:00Z",
        },
    )
    deleted = _http(200)
    created = _http(200, headers={}, json_payload={"etag": '"new-etag"'})
    store = BlobFrameStore(_settings())
    session = _session(
        put=Mock(side_effect=[conflict, created]),
        get=Mock(return_value=current),
        post=Mock(return_value=deleted),
    )

    with patch("ingest.blob_store._blob_session", return_value=session):
        lease = store.acquire_lease(
            "locks/ingest.json",
            "new-owner",
            datetime(2024, 7, 15, 18, tzinfo=UTC),
            datetime(2024, 7, 15, 18, 15, tzinfo=UTC),
        )

    assert lease == IngestLease("locks/ingest.json", "new-owner", '"new-etag"')
    assert session.post.call_args.kwargs["headers"]["x-if-match"] == '"stale-etag"'
    assert session.put.call_args_list[0].kwargs.get("allow_redirects") is False


def test_lease_create_does_not_retry_conflict() -> None:
    conflict = _http(409)
    session = _session(
        put=Mock(return_value=conflict),
        get=Mock(
            return_value=_http(
                200,
                json_payload={
                    "owner": "other",
                    "expiresAt": "2026-08-14T12:00:00Z",
                },
            )
        ),
    )
    store = BlobFrameStore(_settings())
    with patch("ingest.blob_store._blob_session", return_value=session):
        assert (
            store.acquire_lease(
                "locks/ingest.json",
                "owner",
                datetime(2026, 8, 14, 11, tzinfo=UTC),
                datetime(2026, 8, 14, 11, 15, tzinfo=UTC),
            )
            is None
        )
    assert session.put.call_count == 1


def test_lease_create_accepts_vercel_duplicate_bad_request() -> None:
    conflict = _http(
        400,
        json_payload={
            "error": {
                "code": "bad_request",
                "message": "This blob already exists, use `allowOverwrite: true` if you want to overwrite it.",
            }
        },
    )
    current = _http(
        200,
        json_payload={
            "owner": "other",
            "expiresAt": "2026-08-14T12:00:00Z",
        },
    )
    session = _session(put=Mock(return_value=conflict), get=Mock(return_value=current))

    with patch("ingest.blob_store._blob_session", return_value=session):
        assert (
            BlobFrameStore(_settings()).acquire_lease(
                "locks/ingest.json",
                "owner",
                datetime(2026, 8, 14, 11, tzinfo=UTC),
                datetime(2026, 8, 14, 11, 15, tzinfo=UTC),
            )
            is None
        )

    assert session.put.call_count == 1
    assert session.get.call_count == 1


def test_blob_lease_creation_fails_closed_without_an_etag() -> None:
    created = _http(200, json_payload={"url": "https://example.public.blob.vercel-storage.com/locks/ingest.json"})
    session = _session(put=Mock(return_value=created))
    with patch("ingest.blob_store._blob_session", return_value=session):
        with pytest.raises(RuntimeError, match="ETag"):
            BlobFrameStore(_settings()).acquire_lease(
                "locks/ingest.json",
                "owner",
                datetime(2026, 8, 14, 11, tzinfo=UTC),
                datetime(2026, 8, 14, 11, 15, tzinfo=UTC),
            )


def test_blob_listing_follows_cursor_pages() -> None:
    first = _http(200, json_payload={"blobs": [{"pathname": "context/assets/a"}], "hasMore": True, "cursor": "next"})
    second = _http(200, json_payload={"blobs": [{"pathname": "context/assets/b"}], "hasMore": False})
    session = _session(get=Mock(side_effect=[first, second]))

    with patch("ingest.blob_store._blob_session", return_value=session):
        assert BlobFrameStore(_settings()).list_prefix("context/assets") == ["context/assets/a", "context/assets/b"]

    assert session.get.call_count == 2
    assert session.get.call_args_list[1].kwargs["params"]["cursor"] == "next"


def test_blob_get_bytes_reads_public_store_without_credentials() -> None:
    store = BlobFrameStore(_settings())
    response = _http(200, b"png-bytes")
    session = _session(get=Mock(return_value=response))
    with patch("ingest.blob_store._blob_session", return_value=session):
        assert store.get_bytes("context/assets/abc/firework.png") == b"png-bytes"
    assert session.get.call_args.args[0] == "https://example.public.blob.vercel-storage.com/context/assets/abc/firework.png"
    assert "Authorization" not in session.get.call_args.kwargs["headers"]
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["stream"] is True


def test_blob_get_bytes_treats_forbidden_as_missing() -> None:
    store = BlobFrameStore(_settings())
    forbidden = _http(403)
    forbidden.raise_for_status = Mock(side_effect=AssertionError("403 was raised"))
    session = _session(get=Mock(return_value=forbidden))

    with patch("ingest.blob_store._blob_session", return_value=session):
        assert store.get_bytes("context/assets/abc/firework.png") is None

    assert session.get.call_count == 1
    assert "Authorization" not in session.get.call_args.kwargs["headers"]
    assert session.get.call_args.kwargs["headers"]["User-Agent"].startswith(f"TitanSkies/{__version__}")


def test_blob_get_bytes_treats_not_found_as_missing() -> None:
    store = BlobFrameStore(_settings())
    missing = _http(404)
    missing.raise_for_status = Mock(side_effect=AssertionError("404 was raised"))
    session = _session(get=Mock(return_value=missing))

    with patch("ingest.blob_store._blob_session", return_value=session):
        assert store.get_bytes("context/assets/abc/firework.png") is None


def test_blob_get_bytes_raises_on_public_server_error() -> None:
    store = BlobFrameStore(_settings())
    server_error = _http(500)
    server_error.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    session = _session(get=Mock(return_value=server_error))

    with patch("ingest.blob_store._blob_session", return_value=session):
        try:
            store.get_bytes("context/assets/abc/firework.png")
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("public 5xx was swallowed")

    assert session.get.call_count == 3


def test_blob_stream_enforces_byte_cap() -> None:
    store = BlobFrameStore(_settings())
    response = _http(200, b"x" * 64)
    session = _session(get=Mock(return_value=response))
    with patch("ingest.blob_store._blob_session", return_value=session):
        try:
            store.get_bytes("cache/fields/abc/field.bin", max_bytes=16)
        except RuntimeError as exc:
            assert "exceeds" in str(exc)
        else:
            raise AssertionError("oversized blob stream was accepted")


def test_blob_stream_stops_at_the_ingest_deadline() -> None:
    response = _http(200, b"chunk")
    session = _session(get=Mock(return_value=response))
    with (
        patch("ingest.blob_store._blob_session", return_value=session),
        patch("ingest.blob_store.bounded_timeout", side_effect=[30, None]),
    ):
        with pytest.raises(RuntimeError, match="ingest deadline"):
            BlobFrameStore(_settings()).get_bytes("context/assets/abc/firework.png")
    response.close.assert_called_once()


def test_blob_put_treats_duplicate_path_conflict_as_reuse() -> None:
    response = _http(409)
    response.raise_for_status = Mock(side_effect=AssertionError("409 was raised"))
    store = BlobFrameStore(_settings())
    session = _session(put=Mock(return_value=response))
    with patch("ingest.blob_store._blob_session", return_value=session):
        url = store.put_bytes("context/assets/abc/x.png", b"png", "image/png", cache_seconds=60, overwrite=False)
    assert url.endswith("context/assets/abc/x.png")
    response.raise_for_status.assert_not_called()
    assert session.put.call_args.args[0] == "https://vercel.com/api/blob"
    assert session.put.call_args.kwargs["params"] == {"pathname": "context/assets/abc/x.png"}
    assert session.put.call_args.kwargs["headers"]["x-api-version"] == "12"
    assert session.put.call_args.kwargs["headers"]["x-vercel-blob-store-id"] == "example"
    assert session.put.call_args.kwargs["headers"]["x-vercel-blob-access"] == "public"


def test_blob_put_treats_vercel_duplicate_bad_request_as_reuse() -> None:
    response = _http(
        400,
        json_payload={
            "error": {
                "code": "bad_request",
                "message": "This blob already exists, use `allowOverwrite: true` if you want to overwrite it.",
            }
        },
    )
    response.raise_for_status = Mock(side_effect=AssertionError("duplicate response was raised"))
    session = _session(put=Mock(return_value=response))

    with patch("ingest.blob_store._blob_session", return_value=session):
        url = BlobFrameStore(_settings()).put_bytes(
            "context/assets/abc/x.png",
            b"png",
            "image/png",
            cache_seconds=60,
            overwrite=False,
        )

    assert url.endswith("context/assets/abc/x.png")
    response.raise_for_status.assert_not_called()


def test_blob_put_rejects_unrelated_bad_request() -> None:
    response = _http(400, json_payload={"error": {"code": "bad_request", "message": "invalid pathname"}})
    response.raise_for_status = Mock(side_effect=requests.HTTPError("400 Client Error"))
    session = _session(put=Mock(return_value=response))

    with patch("ingest.blob_store._blob_session", return_value=session):
        with pytest.raises(requests.HTTPError, match="400 Client Error"):
            BlobFrameStore(_settings()).put_bytes(
                "context/assets/abc/x.png",
                b"png",
                "image/png",
                cache_seconds=60,
                overwrite=False,
            )


def test_blob_put_rejects_a_response_from_another_store() -> None:
    response = _http(200, json_payload={"url": "https://other.public.blob.vercel-storage.com/context/state.json"})
    session = _session(put=Mock(return_value=response))
    with patch("ingest.blob_store._blob_session", return_value=session):
        with pytest.raises(RuntimeError, match="configured store"):
            BlobFrameStore(_settings()).put_json(
                "context/state.json",
                {"version": 1},
                cache_seconds=60,
                overwrite=True,
            )


def test_blob_delete_many_sends_url_batches() -> None:
    response = _http(200)
    store = BlobFrameStore(_settings())
    session = _session(post=Mock(return_value=response))
    with patch("ingest.blob_store._blob_session", return_value=session):
        store.delete_many(["a.png", "b.png"])
    assert session.post.call_args.kwargs["json"] == {
        "urls": [
            "https://example.public.blob.vercel-storage.com/a.png",
            "https://example.public.blob.vercel-storage.com/b.png",
        ]
    }


def test_local_delete_many_removes_existing_paths(tmp_path: Path) -> None:
    store = LocalFrameStore(tmp_path)
    store.put_bytes("a.txt", b"a", "text/plain", cache_seconds=1, overwrite=True)
    store.put_bytes("b.txt", b"b", "text/plain", cache_seconds=1, overwrite=True)
    store.delete_many(["a.txt", "b.txt", "missing.txt"])
    assert store.get_bytes("a.txt") is None
    assert store.get_bytes("b.txt") is None


def test_local_and_blob_storage_have_the_same_object_contract(tmp_path: Path) -> None:
    def exercise(store: Any) -> dict:
        store.put_json("context/state.json", {"version": 1}, cache_seconds=60, overwrite=True)
        store.put_bytes("context/assets/a/item.png", b"first", "image/png", cache_seconds=60, overwrite=False)
        store.put_bytes("context/assets/a/item.png", b"ignored", "image/png", cache_seconds=60, overwrite=False)
        store.put_bytes("context/assets/b/item.png", b"second", "image/png", cache_seconds=60, overwrite=True)
        first_page = store.list_page("context/assets", limit=1)
        second_page = store.list_page("context/assets", cursor=first_page.cursor, limit=1)
        before_delete = {
            "state": store.get_authoritative_json("context/state.json"),
            "immutable": store.get_bytes("context/assets/a/item.png"),
            "paths": sorted(store.list_prefix("context/assets")),
            "pages": first_page.paths + second_page.paths,
        }
        store.delete_many(before_delete["paths"])
        before_delete["remaining"] = store.list_prefix("context/assets")
        return before_delete

    local_result = exercise(LocalFrameStore(tmp_path / "local"))
    memory = _MemoryBlobSession()
    with patch("ingest.blob_store._blob_session", return_value=memory):
        blob_result = exercise(BlobFrameStore(_settings()))

    assert (
        blob_result
        == local_result
        == {
            "state": {"version": 1},
            "immutable": b"first",
            "paths": ["context/assets/a/item.png", "context/assets/b/item.png"],
            "pages": ["context/assets/a/item.png", "context/assets/b/item.png"],
            "remaining": [],
        }
    )


def test_blob_credentials_are_limited_to_the_api_host() -> None:
    from ingest.blob_store import _retrying_request

    try:
        _retrying_request(
            "GET",
            "https://example.public.blob.vercel-storage.com/cache/fields/abc/field.bin",
            headers={"Authorization": "Bearer vercel_blob_rw_test_secret", "User-Agent": "TitanSkies/0.1.0"},
            timeout=1,
            retries=0,
        )
    except RuntimeError as exc:
        assert "Vercel Blob API" in str(exc)
    else:
        raise AssertionError("blob credentials were sent to a non-API host")


def test_local_store_rejects_path_escape(tmp_path: Path) -> None:
    store = LocalFrameStore(tmp_path)
    try:
        store.get_bytes("../secret.json")
    except ValueError as exc:
        assert "invalid store path" in str(exc)
    else:
        raise AssertionError("escaped store path was accepted")


@pytest.mark.parametrize("pathname", ["", "../secret.json", "/absolute.json", "a//b", "a/./b", "a\\b"])
def test_local_and_blob_stores_reject_the_same_unsafe_paths(tmp_path: Path, pathname: str) -> None:
    with pytest.raises(ValueError, match="invalid store path"):
        LocalFrameStore(tmp_path).url_for(pathname)
    with pytest.raises(ValueError, match="invalid store path"):
        BlobFrameStore(_settings()).url_for(pathname)


def test_blob_mode_requires_complete_valid_configuration(monkeypatch: Any) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "blob")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("BLOB_STORE_ID", raising=False)
    monkeypatch.delenv("PUBLIC_BLOB_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="BLOB_READ_WRITE_TOKEN"):
        Settings.from_env()

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_test_secret")
    monkeypatch.setenv("BLOB_STORE_ID", "example")
    monkeypatch.setenv("PUBLIC_BLOB_BASE_URL", "https://attacker.example")
    with pytest.raises(ValueError, match="public Vercel Blob store"):
        Settings.from_env()

    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("PUBLIC_BLOB_BASE_URL", "https://example.public.blob.vercel-storage.com")
    with pytest.raises(ValueError, match="unavailable in Vercel Preview"):
        Settings.from_env()


def test_blob_store_id_is_normalized_and_must_match_public_origin() -> None:
    settings = Settings(
        storage_backend="blob",
        blob_token="vercel_blob_rw_test_secret",
        blob_store_id="store_example",
        public_blob_base_url="https://example.public.blob.vercel-storage.com/",
    )
    assert settings.blob_store_id == "example"
    assert settings.public_blob_base_url == "https://example.public.blob.vercel-storage.com"

    with pytest.raises(ValueError, match="match the configured"):
        Settings(
            storage_backend="blob",
            blob_token="vercel_blob_rw_test_secret",
            blob_store_id="example",
            public_blob_base_url="https://other.public.blob.vercel-storage.com",
        )


def test_storage_dispatch_preserves_local_and_selects_blob(tmp_path: Path) -> None:
    local = open_store(Settings(local_frame_dir=tmp_path))
    assert isinstance(local, LocalFrameStore)
    assert isinstance(open_store(_settings()), BlobFrameStore)


def test_pipeline_publishes_assets_then_manifest_then_pointer(monkeypatch: Any, tmp_path: Path) -> None:
    class RecordingStore(LocalFrameStore):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.writes: list[str] = []

        def put_bytes(self, pathname: Any, data: Any, content_type: Any, *, cache_seconds: Any, overwrite: Any) -> Any:
            self.writes.append(pathname)
            return super().put_bytes(
                pathname,
                data,
                content_type,
                cache_seconds=cache_seconds,
                overwrite=overwrite,
            )

    pointer = json.loads(Path("public/demo/context/latest.json").read_text(encoding="utf-8"))
    manifest = json.loads((Path("public/demo") / pointer["manifestPath"]).read_text(encoding="utf-8"))
    store = RecordingStore(tmp_path / "data")

    def build_demo(active_store: Any, *_args: Any, **_kwargs: Any) -> Any:
        active_store.put_bytes(
            "context/assets/0123456789abcdef0123/test.png",
            b"asset",
            "image/png",
            cache_seconds=60,
            overwrite=False,
        )
        return manifest

    monkeypatch.setattr("ingest.context_pipeline.open_store", lambda _settings: store)
    monkeypatch.setattr("ingest.context_pipeline.demo_manifest", build_demo)
    result = run_context_ingest(
        Settings(context_source="demo", local_frame_dir=tmp_path / "data", local_cache_dir=tmp_path / "cache"),
        datetime(2024, 7, 15, 20, tzinfo=UTC),
    )
    assert result["ok"] is True
    asset_index = store.writes.index("context/assets/0123456789abcdef0123/test.png")
    manifest_index = next(index for index, path in enumerate(store.writes) if path.startswith("context/manifests/"))
    pointer_index = store.writes.index("context/latest.json")
    assert asset_index < manifest_index < pointer_index


def test_pipeline_redacts_storage_credentials_from_failure_logs(monkeypatch: Any, tmp_path: Path, caplog: Any) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN=never-log-this CRON_SECRET=or-this")

    monkeypatch.setattr("ingest.context_pipeline.demo_manifest", fail)
    caplog.set_level(logging.ERROR, logger="titanskies.context")
    result = run_context_ingest(
        Settings(context_source="demo", local_frame_dir=tmp_path / "data", local_cache_dir=tmp_path / "cache"),
        datetime(2024, 7, 15, 20, tzinfo=UTC),
    )

    assert result["ok"] is False
    assert "never-log-this" not in str(result)
    assert "or-this" not in str(result)
    assert "never-log-this" not in caplog.text
    assert "or-this" not in caplog.text


def test_blob_exists_treats_forbidden_and_server_error_as_missing() -> None:
    store = BlobFrameStore(_settings())
    forbidden = _http(403)
    server_error = _http(503)
    session = _session(get=Mock(side_effect=AssertionError("exists must use HEAD")))
    session.head = Mock(side_effect=[forbidden, server_error])
    with patch("ingest.blob_store._blob_session", return_value=session):
        assert store.exists("context/assets/aaaaaaaaaaaaaaaaaaaa/a.png") is False
        assert store.exists("context/assets/bbbbbbbbbbbbbbbbbbbb/b.png") is False
    session.get.assert_not_called()


def test_blob_seed_uses_head_without_gets(monkeypatch: Any) -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import ingest_run

    memory = _MemoryBlobSession()
    store = BlobFrameStore(_settings())
    previous: dict[str, Any] = {"assets": []}
    for index in range(3):
        data = f"asset-{index}".encode()
        digest = hashlib.sha256(data).hexdigest()[:20]
        path = f"context/assets/{digest}/item-{index}.bin"
        with patch("ingest.blob_store._blob_session", return_value=memory):
            store.put_bytes(path, data, "application/octet-stream", cache_seconds=60, overwrite=False)
        previous["assets"].append(store.url_for(path))
        previous["assets"].append(store.url_for(path))
    puts = memory.puts
    with patch("ingest.blob_store._blob_session", return_value=memory), ingest_run() as (metrics, _budget):
        assert seed_asset_memo(store, previous, workers=3) is True
    assert memory.gets == 0
    assert memory.heads == 3
    assert memory.puts == puts
    assert metrics.storage_reads == 3
    assert metrics.storage_read_bytes == 0


def test_blob_seed_rebinds_memo_urls_to_store_origin() -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import current_asset_memo, ingest_run

    memory = _MemoryBlobSession()
    store = BlobFrameStore(_settings())
    previous: dict[str, Any] = {"assets": []}
    paths: list[str] = []
    for index in range(2):
        data = f"rebind-{index}".encode()
        digest = hashlib.sha256(data).hexdigest()[:20]
        path = f"context/assets/{digest}/item-{index}.bin"
        paths.append(path)
        with patch("ingest.blob_store._blob_session", return_value=memory):
            store.put_bytes(path, data, "application/octet-stream", cache_seconds=60, overwrite=False)
        previous["assets"].append(f"https://evil.example/{path}")
        previous["assets"].append(f"https://another.example/{path}?cache=old")
    with patch("ingest.blob_store._blob_session", return_value=memory), ingest_run():
        assert seed_asset_memo(store, previous, workers=2) is True
        memo = current_asset_memo()
        for path in paths:
            assert memo[path] == store.url_for(path)
    assert memory.heads == 2


def test_blob_seed_verify_assets_uses_gets(monkeypatch: Any) -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import ingest_run

    monkeypatch.setenv("CONTEXT_VERIFY_ASSETS", "1")
    memory = _MemoryBlobSession()
    store = BlobFrameStore(_settings())
    previous: dict[str, Any] = {"assets": []}
    for index in range(3):
        data = f"asset-{index}".encode()
        digest = hashlib.sha256(data).hexdigest()[:20]
        path = f"context/assets/{digest}/item-{index}.bin"
        with patch("ingest.blob_store._blob_session", return_value=memory):
            store.put_bytes(path, data, "application/octet-stream", cache_seconds=60, overwrite=False)
        previous["assets"].append(store.url_for(path))
        previous["assets"].append(f"https://old.example/{path}")
    puts = memory.puts
    with patch("ingest.blob_store._blob_session", return_value=memory), ingest_run() as (metrics, _budget):
        assert seed_asset_memo(store, previous, workers=3) is True
    assert memory.heads == 0
    assert memory.gets == 3
    assert memory.puts == puts
    assert metrics.storage_reads == 3
    assert metrics.storage_read_bytes > 0


def test_blob_seed_missing_distinct_asset_clears_memo() -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import current_asset_memo, ingest_run

    memory = _MemoryBlobSession()
    store = BlobFrameStore(_settings())
    data = b"present"
    present = f"context/assets/{hashlib.sha256(data).hexdigest()[:20]}/present.bin"
    missing = "context/assets/aaaaaaaaaaaaaaaaaaaa/missing.bin"
    memory.objects[present] = data
    previous = {"assets": [store.url_for(present), store.url_for(missing), store.url_for(missing)]}
    with patch("ingest.blob_store._blob_session", return_value=memory), ingest_run():
        assert seed_asset_memo(store, previous, workers=1) is False
        assert current_asset_memo() == {}
    assert memory.heads == 2


def test_blob_seed_digest_failure_clears_memo(monkeypatch: Any) -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import current_asset_memo, ingest_run

    monkeypatch.setenv("CONTEXT_VERIFY_ASSETS", "1")
    memory = _MemoryBlobSession()
    store = BlobFrameStore(_settings())
    data = b"expected"
    path = f"context/assets/{hashlib.sha256(data).hexdigest()[:20]}/item.bin"
    memory.objects[path] = b"corrupt"
    previous = {"assets": [store.url_for(path), f"https://old.example/{path}"]}
    with patch("ingest.blob_store._blob_session", return_value=memory), ingest_run():
        assert seed_asset_memo(store, previous) is False
        assert current_asset_memo() == {}
    assert memory.gets == 1


def test_bundled_manifest_seed_checks_each_distinct_asset_once() -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import ingest_run

    pointer = json.loads(Path("public/demo/context/latest.json").read_text(encoding="utf-8"))
    manifest = json.loads((Path("public/demo") / pointer["manifestPath"]).read_text(encoding="utf-8"))
    references = re.findall(r"context/assets/[0-9a-f]{20}/[A-Za-z0-9._-]+", json.dumps(manifest))
    assert len(references) > len(set(references))
    assert len(set(references)) == 738
    store = BlobFrameStore(_settings())
    with patch.object(store, "exists", return_value=True) as exists, ingest_run():
        assert seed_asset_memo(store, manifest, workers=4) is True
    assert exists.call_count == len(set(references))


def test_local_exists_requires_regular_file(tmp_path: Path) -> None:
    store = LocalFrameStore(tmp_path)
    regular = "context/assets/aaaaaaaaaaaaaaaaaaaa/item.bin"
    store.put_bytes(regular, b"payload", "application/octet-stream", cache_seconds=60, overwrite=False)
    assert store.exists(regular) is True

    directory = "context/assets/bbbbbbbbbbbbbbbbbbbb/folder.bin"
    (tmp_path / directory).mkdir(parents=True)
    assert store.exists(directory) is False

    linked = "context/assets/cccccccccccccccccccc/link.bin"
    (tmp_path / linked).parent.mkdir(parents=True)
    (tmp_path / linked).symlink_to(tmp_path / regular)
    assert store.exists(linked) is False


def test_local_seed_still_full_reads(tmp_path: Path) -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import ingest_run

    store = LocalFrameStore(tmp_path)
    previous: dict[str, Any] = {"assets": []}
    total = 0
    for index in range(3):
        data = f"asset-{index}".encode()
        digest = hashlib.sha256(data).hexdigest()[:20]
        path = f"context/assets/{digest}/item-{index}.bin"
        store.put_bytes(path, data, "application/octet-stream", cache_seconds=60, overwrite=False)
        previous["assets"].append(store.url_for(path))
        previous["assets"].append(f"https://old.example/{path}")
        total += len(data)
    with ingest_run() as (metrics, _budget):
        assert seed_asset_memo(store, previous) is True
    assert metrics.storage_reads == 3
    assert metrics.storage_read_bytes == total


@pytest.mark.parametrize("missing", [True, False])
def test_local_seed_missing_or_corrupt_asset_clears_memo(tmp_path: Path, missing: bool) -> None:
    from ingest.context_publish import seed_asset_memo
    from ingest.perf import current_asset_memo, ingest_run

    store = LocalFrameStore(tmp_path)
    good = b"good"
    good_path = f"context/assets/{hashlib.sha256(good).hexdigest()[:20]}/good.bin"
    store.put_bytes(good_path, good, "application/octet-stream", cache_seconds=60, overwrite=False)
    expected = b"expected"
    bad_path = f"context/assets/{hashlib.sha256(expected).hexdigest()[:20]}/bad.bin"
    if not missing:
        store.put_bytes(bad_path, b"corrupt", "application/octet-stream", cache_seconds=60, overwrite=False)
    previous = {"assets": [store.url_for(good_path), store.url_for(bad_path), store.url_for(bad_path)]}
    with ingest_run() as (metrics, _budget):
        assert seed_asset_memo(store, previous) is False
        assert current_asset_memo() == {}
    assert metrics.storage_reads == 2
