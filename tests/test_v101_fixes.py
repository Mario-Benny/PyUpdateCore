from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from updater import main as cli_main
from update_core.config import config_template, load_config
from update_core.downloader import Downloader
from update_core.errors import ConfigError, DownloadError
from update_core.models import InstallerAsset
from update_core.network import resolve_proxy
from update_core.providers import GithubReleaseConfig, GithubReleaseProvider
from update_core.storage import merge_state, read_json


# --------------------------------------------------------------------------
# H4: download.mode / merge_after_download are removed as dead configuration
# --------------------------------------------------------------------------

def test_dead_download_keys_absent_from_template() -> None:
    download = config_template()["download"]
    assert "mode" not in download
    assert "merge_after_download" not in download


def test_legacy_dead_keys_are_ignored(tmp_path: Path) -> None:
    data = config_template()
    data["security"]["allowed_hosts"] = ["updates.example.com"]
    data["download"]["mode"] = "single"          # still present in an old config
    data["download"]["merge_after_download"] = False
    path = tmp_path / "Data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    config = load_config(path)
    assert not hasattr(config, "download_mode")
    assert not hasattr(config, "merge_after_download")


# --------------------------------------------------------------------------
# V-12: wait_timeout_seconds is read from the installer section
# --------------------------------------------------------------------------

def _load_with(tmp_path: Path, mutate) -> object:
    data = config_template()
    data["security"]["allowed_hosts"] = ["updates.example.com"]
    mutate(data)
    path = tmp_path / "Data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_config(path)


def test_wait_timeout_defaults_and_reads_config(tmp_path: Path) -> None:
    assert _load_with(tmp_path, lambda d: None).wait_timeout_seconds == 600
    assert _load_with(tmp_path, lambda d: d["installer"].__setitem__("wait_timeout_seconds", 123)).wait_timeout_seconds == 123


def test_wait_timeout_rejects_non_positive(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _load_with(tmp_path, lambda d: d["installer"].__setitem__("wait_timeout_seconds", 0))


# --------------------------------------------------------------------------
# M2: system proxy from environment variables gains a scheme; direct stays None
# --------------------------------------------------------------------------

def test_system_env_proxy_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "localhost:3128")
    assert resolve_proxy("system").proxy_url == "http://localhost:3128"
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    assert resolve_proxy("system").proxy_url == "socks5://127.0.0.1:1080"
    assert resolve_proxy("direct").proxy_url is None


# --------------------------------------------------------------------------
# M3.2: a non-JSON GitHub API body is a DownloadError, not a raw traceback
# --------------------------------------------------------------------------

def test_provider_non_json_response_raises_download_error(tmp_path: Path, httpx_mock) -> None:
    api = f"{GithubReleaseProvider.API}/octo/hello/releases/latest"
    httpx_mock.add_response(url=api, status_code=200, content=b"<html>gateway error</html>")
    config = _load_with(tmp_path, lambda d: d["security"].__setitem__("allowed_hosts", ["github.com"]))
    provider = GithubReleaseProvider(GithubReleaseConfig("octo/hello", "stable", "*.exe", "*.sha256"), config)
    with pytest.raises(DownloadError, match="JSON"):
        provider.fetch()


# --------------------------------------------------------------------------
# M5: corrupt / schema-drifted resume state self-heals by re-planning
# --------------------------------------------------------------------------

def _dl_config(tmp_path: Path):
    path = tmp_path / "Data.json"
    data = config_template()
    data["security"]["allowed_hosts"] = ["updates.example.com"]
    data["download"]["retry_count"] = 1
    data["download"]["min_segment_size_mb"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    return replace(load_config(path), min_segment_size_bytes=1)


def _range_callback(content: bytes):
    def callback(request: httpx.Request) -> httpx.Response:
        value = request.headers.get("Range")
        if value == "bytes=0-0":
            return httpx.Response(206, headers={"Content-Range": f"bytes 0-0/{len(content)}", "Content-Length": "1", "ETag": '"v1"'}, content=content[:1])
        assert value is not None
        start, end = (int(item) for item in value.removeprefix("bytes=").split("-"))
        body = content[start:end + 1]
        return httpx.Response(206, headers={"Content-Range": f"bytes {start}-{end}/{len(content)}", "Content-Length": str(len(body)), "ETag": '"v1"'}, content=body)
    return callback


def test_corrupt_resume_state_self_heals(tmp_path: Path, httpx_mock) -> None:
    content = bytes(range(256)) * 16
    asset = InstallerAsset("package.bin", "https://updates.example.com/package.bin", len(content), hashlib.sha256(content).hexdigest())
    task_id = hashlib.sha256(f"{asset.url}\0{asset.size}\0{asset.sha256}".encode()).hexdigest()[:16]
    task = tmp_path / "downloads" / f"task_{task_id}"
    task.mkdir(parents=True)
    # one segment carries an unknown key -> Segment(**item) would raise TypeError
    (task / "state.json").write_text(json.dumps({
        "schema_version": 1,
        "resource": {"url": asset.url, "size": asset.size, "sha256": asset.sha256, "etag": '"v1"', "threads": 4},
        "segments": [{"segment_id": 0, "start": 0, "end": 100, "downloaded": 0, "completed": False, "retry_count": 0, "last_error": "", "unexpected": True}],
    }), encoding="utf-8")
    httpx_mock.add_callback(_range_callback(content), url=asset.url, is_reusable=True)
    result = Downloader(_dl_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json").download(asset, threads=4)
    assert result.read_bytes() == content


# --------------------------------------------------------------------------
# M4: under --json-output the OSError branch still emits structured JSON
# --------------------------------------------------------------------------

def test_json_output_oserror_emits_structured_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    userdata = tmp_path / "userdata.json"
    coredata = tmp_path / "coredata.json"
    userdata.write_text("{}", encoding="utf-8")
    coredata.write_text("{}", encoding="utf-8")
    paths = {"userdata": userdata, "coredata": coredata, "data": tmp_path, "logs": tmp_path,
             "download": tmp_path, "temporary": tmp_path, "state": tmp_path / "state.json"}
    monkeypatch.setattr("update_core.layout.initialise_layout", lambda _root: (paths, False))
    monkeypatch.setattr("update_core.logging_utils.clean_logs", lambda *a, **k: None)
    monkeypatch.setattr("update_core.logging_utils.write_log", lambda *a, **k: None)
    monkeypatch.setattr("update_core.config.load_split_config", lambda *a, **k: MagicMock())

    class BoomWorkflow:
        def __init__(self, *a, **k): ...
        def check(self, *a, **k): raise OSError("磁盘不可用")

    monkeypatch.setattr("update_core.workflow.UpdateWorkflow", BoomWorkflow)

    assert cli_main(["--json-output", "--data-root", str(tmp_path)]) == 2
    err_line = capsys.readouterr().err.strip().splitlines()[-1]
    record = json.loads(err_line)
    assert record["status"] == "error"
    assert record["code"] == "OSError"


# --------------------------------------------------------------------------
# L4: concurrent merge_state writes never drop a peer task's update
# --------------------------------------------------------------------------

def test_concurrent_merge_state_keeps_all_updates(tmp_path: Path) -> None:
    path = tmp_path / "State.json"
    workers = [threading.Thread(target=merge_state, args=(path, {"downloads": {f"t{i}": {"status": "done"}}})) for i in range(16)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert set(read_json(path)["downloads"]) == {f"t{i}" for i in range(16)}
