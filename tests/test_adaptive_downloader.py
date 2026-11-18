from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import httpx
import pytest

from update_core.config import create_config_template, load_config
from update_core.downloader import Downloader
from update_core.errors import CancelledError, DownloadError, RangeUnsupportedError
from update_core.models import InstallerAsset
from update_core.storage import UpdateLock, merge_state, read_json


def _config(tmp_path: Path, **changes):
    path = tmp_path / "Data.json"
    create_config_template(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["security"]["allowed_hosts"] = ["updates.example.com", "cdn.example.com"]
    data["download"]["retry_count"] = 1
    data["download"]["min_segment_size_mb"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    return replace(load_config(path), min_segment_size_bytes=1, **changes)


def _asset(content: bytes) -> InstallerAsset:
    return InstallerAsset("package.bin", "https://updates.example.com/package.bin", len(content), hashlib.sha256(content).hexdigest())


def _range_callback(content: bytes):
    def callback(request: httpx.Request) -> httpx.Response:
        value = request.headers.get("Range")
        if value == "bytes=0-0":
            return httpx.Response(206, headers={"Content-Range": f"bytes 0-0/{len(content)}", "Content-Length": "1", "ETag": '"v1"'}, content=content[:1])
        assert value is not None
        start, end = (int(item) for item in value.removeprefix("bytes=").split("-"))
        body = content[start : end + 1]
        return httpx.Response(206, headers={"Content-Range": f"bytes {start}-{end}/{len(content)}", "Content-Length": str(len(body)), "ETag": '"v1"'}, content=body)
    return callback


@pytest.mark.parametrize("threads", [1, 4, 16])
def test_adaptive_download_modes(tmp_path: Path, httpx_mock, threads: int) -> None:
    content = bytes(range(256)) * 16
    asset = _asset(content)
    if threads == 1:
        httpx_mock.add_response(url=asset.url, content=content, headers={"Content-Length": str(len(content))})
    else:
        httpx_mock.add_callback(_range_callback(content), url=asset.url, is_reusable=True)
    path = Downloader(_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=threads)
    assert path.read_bytes() == content


def test_range_ignored_falls_back_to_single(tmp_path: Path, httpx_mock) -> None:
    content = b"range fallback data"
    asset = _asset(content)
    httpx_mock.add_response(url=asset.url, status_code=200, content=content, headers={"Content-Length": str(len(content))})
    httpx_mock.add_response(url=asset.url, status_code=200, content=content, headers={"Content-Length": str(len(content))})
    events: list[dict] = []
    result = Downloader(_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json", event_callback=events.append).download(asset, threads=4)
    time.sleep(0.05)
    assert result.read_bytes() == content
    assert any(event["status"] == "download_fallback" for event in events)


def test_bad_content_range_is_rejected_when_fallback_disabled(tmp_path: Path, httpx_mock) -> None:
    content = b"abcdef"
    asset = _asset(content)
    httpx_mock.add_response(url=asset.url, status_code=206, content=b"x", headers={"Content-Range": "bytes 0-0/6", "Content-Length": "1"})
    httpx_mock.add_callback(lambda request: httpx.Response(206, content=b"abc", headers={"Content-Range": "bytes 1-3/6", "Content-Length": "3"}), url=asset.url, is_reusable=True)
    with pytest.raises(RangeUnsupportedError):
        Downloader(_config(tmp_path, fallback_to_single=False), tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=4)


def test_trusted_redirect_is_checked_hop_by_hop(tmp_path: Path, httpx_mock) -> None:
    content = b"redirected"
    asset = _asset(content)
    httpx_mock.add_response(url=asset.url, status_code=302, headers={"Location": "https://cdn.example.com/package.bin"})
    httpx_mock.add_response(url="https://cdn.example.com/package.bin", status_code=200, content=content, headers={"Content-Length": str(len(content))})
    assert Downloader(_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=1).read_bytes() == content


def test_untrusted_redirect_is_rejected(tmp_path: Path, httpx_mock) -> None:
    asset = _asset(b"blocked")
    httpx_mock.add_response(url=asset.url, status_code=302, headers={"Location": "https://evil.example.net/payload.bin"})
    with pytest.raises(DownloadError, match="白名单"):
        Downloader(_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=1)


def test_changed_etag_discards_saved_parts(tmp_path: Path, httpx_mock) -> None:
    content = b"new version content" * 32
    asset = _asset(content)
    task_id = hashlib.sha256(f"{asset.url}\0{asset.size}\0{asset.sha256}".encode()).hexdigest()[:16]
    task = tmp_path / "downloads" / f"task_{task_id}"
    task.mkdir(parents=True)
    (task / "part_000.tmp").write_bytes(b"old")
    base, extra, start, segments = asset.size // 4, asset.size % 4, 0, []
    for index in range(4):
        length = base + (index < extra)
        segments.append({"segment_id": index, "start": start, "end": start + length - 1, "downloaded": 3 if index == 0 else 0, "completed": False, "retry_count": 0, "last_error": ""})
        start += length
    (task / "state.json").write_text(json.dumps({"resource": {"url": asset.url, "size": asset.size, "sha256": asset.sha256, "etag": '"old"', "threads": 4}, "segments": segments}), encoding="utf-8")
    httpx_mock.add_callback(_range_callback(content), url=asset.url, is_reusable=True)
    result = Downloader(_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=4)
    assert result.read_bytes() == content


def test_state_merge_preserves_downloads_and_checks(tmp_path: Path) -> None:
    state = tmp_path / "State.json"
    merge_state(state, {"last_check": {"available": True}})
    merge_state(state, {"downloads": {"a": {"status": "downloading"}}})
    merge_state(state, {"downloads": {"b": {"status": "completed"}}})
    value = read_json(state)
    assert value["last_check"]["available"] is True
    assert set(value["downloads"]) == {"a", "b"}


def test_stale_lock_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "updater.lock"
    path.write_text("999999", encoding="utf-8")
    with UpdateLock(path):
        assert path.exists()


def test_downloader_rejects_path_traversal(tmp_path: Path) -> None:
    asset = InstallerAsset("../outside.exe", "https://updates.example.com/outside.exe", 1, "00" * 32)
    with pytest.raises(DownloadError, match="文件名不安全"):
        Downloader(_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=1)


class _LocalRangeHandler(BaseHTTPRequestHandler):
    content = b""
    ranges: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        requested = self.headers.get("Range")
        if requested:
            self.__class__.ranges.append(requested)
            start, end = (int(value) for value in requested.removeprefix("bytes=").split("-"))
            body = self.content[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.content)}")
        else:
            body = self.content
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"local-v1"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


class _SlowRangeHandler(_LocalRangeHandler):
    first_body = Event()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        requested = self.headers.get("Range")
        if requested:
            self.__class__.ranges.append(requested)
            start, end = (int(value) for value in requested.removeprefix("bytes=").split("-"))
            body = self.content[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.content)}")
        else:
            body = self.content
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"slow-v1"')
        self.end_headers()
        for offset in range(0, len(body), 4096):
            try:
                self.wfile.write(body[offset : offset + 4096])
                self.wfile.flush()
                self.__class__.first_body.set()
                time.sleep(0.004)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return


@pytest.mark.parametrize("threads", [4, 16])
def test_real_threaded_range_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, threads: int) -> None:
    """A controlled loopback exception validates actual concurrent HTTP requests."""
    _LocalRangeHandler.content = bytes(range(256)) * 32
    _LocalRangeHandler.ranges = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalRangeHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr("update_core.network.validate_url", lambda url, config: url)
    try:
        config = replace(_config(tmp_path), require_https=False)
        url = f"http://127.0.0.1:{server.server_port}/asset.bin"
        content = _LocalRangeHandler.content
        asset = InstallerAsset("package.bin", url, len(content), hashlib.sha256(content).hexdigest())
        result = Downloader(config, tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=threads)
        assert result.read_bytes() == content
        assert len(_LocalRangeHandler.ranges) == threads + 1  # probe + every segment
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("threads", [1, 4, 16])
def test_cancelled_download_resumes_for_all_thread_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, threads: int) -> None:
    """Cancellation leaves valid parts, and a new Downloader resumes them safely."""
    _SlowRangeHandler.content = bytes(range(256)) * 1024
    _SlowRangeHandler.ranges = []
    _SlowRangeHandler.first_body = Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowRangeHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr("update_core.network.validate_url", lambda url, config: url)
    try:
        content = _SlowRangeHandler.content
        config = replace(_config(tmp_path), require_https=False, proxy_mode="direct")
        asset = InstallerAsset("package.bin", f"http://127.0.0.1:{server.server_port}/asset.bin", len(content), hashlib.sha256(content).hexdigest())
        cancel = Event()
        failures: list[Exception] = []

        def interrupted() -> None:
            try:
                Downloader(config, tmp_path / "downloads", tmp_path / "State.json", cancel_event=cancel).download(asset, threads=threads)
            except Exception as exc:
                failures.append(exc)

        worker = Thread(target=interrupted)
        worker.start()
        assert _SlowRangeHandler.first_body.wait(3)
        cancel.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], CancelledError)
        result = Downloader(config, tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=threads)
        assert result.read_bytes() == content
    finally:
        server.shutdown()
        server.server_close()

