from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from update_core.config import config_template, coredata_template, load_config, userdata_template
from update_core.errors import ConfigError, DownloadError, ManifestError
from update_core.ipc import serve_stdio
from update_core.layout import initialise_layout
from update_core.models import CheckResult, InstallerAsset, UpdateManifest
from update_core.providers import GithubReleaseConfig, GithubReleaseProvider
from update_core.storage import DEFAULT_STATE, read_state_json


def _config(tmp_path: Path):
    data = config_template()
    data["security"]["allowed_hosts"] = ["github.com"]
    path = tmp_path / "Data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_config(path)


def _release(asset_size: int) -> dict:
    return {
        "tag_name": "v1.2.3",
        "prerelease": False,
        "assets": [
            {"name": "setup.exe", "browser_download_url": "https://github.com/acme/demo/releases/download/v1/setup.exe", "size": asset_size},
            {"name": "setup.exe.sha256", "browser_download_url": "https://github.com/acme/demo/releases/download/v1/setup.exe.sha256", "size": 72},
        ],
    }


def _provider(config):
    return GithubReleaseProvider(GithubReleaseConfig("acme/demo", "stable", "setup.exe", "*.sha256"), config)


def test_github_sidecar_redirect_is_checked_each_hop(tmp_path: Path, httpx_mock) -> None:
    api = f"{GithubReleaseProvider.API}/acme/demo/releases/latest"
    httpx_mock.add_response(url=api, json=_release(100))
    httpx_mock.add_response(
        url="https://github.com/acme/demo/releases/download/v1/setup.exe.sha256",
        status_code=302,
        headers={"Location": "https://untrusted.example/checksum"},
    )
    with pytest.raises(DownloadError, match="sidecar"):
        _provider(_config(tmp_path)).fetch()


def test_github_sidecar_has_hard_size_limit(tmp_path: Path, httpx_mock) -> None:
    api = f"{GithubReleaseProvider.API}/acme/demo/releases/latest"
    httpx_mock.add_response(url=api, json=_release(100))
    httpx_mock.add_response(
        url="https://github.com/acme/demo/releases/download/v1/setup.exe.sha256",
        headers={"Content-Length": "65537"},
        content=b"0" * 65537,
    )
    with pytest.raises(DownloadError, match="sidecar"):
        _provider(_config(tmp_path)).fetch()


def test_github_asset_obeys_max_file_size_before_sidecar_fetch(tmp_path: Path, httpx_mock) -> None:
    api = f"{GithubReleaseProvider.API}/acme/demo/releases/latest"
    httpx_mock.add_response(url=api, json=_release(101))
    with pytest.raises(DownloadError, match="最大文件大小"):
        _provider(replace(_config(tmp_path), max_file_size_bytes=100)).fetch()


def test_manifest_rejects_non_exe_installer(tmp_path: Path) -> None:
    from update_core.manifest import parse_manifest

    payload = {
        "schema_version": 1,
        "version": "1.2.3",
        "channel": "stable",
        "installer": {"name": "setup.cmd", "url": "https://github.com/acme/setup.cmd", "size": 1, "sha256": "0" * 64},
    }
    with pytest.raises(ManifestError, match="EXE"):
        parse_manifest(json.dumps(payload).encode(), _config(tmp_path))


def test_corrupt_config_is_an_explicit_error(tmp_path: Path) -> None:
    path = tmp_path / "Data.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="损坏"):
        load_config(path)


def test_corrupt_state_is_quarantined_and_logged(tmp_path: Path) -> None:
    state = tmp_path / "data" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{broken", encoding="utf-8")
    assert read_state_json(state, default=DEFAULT_STATE) == DEFAULT_STATE
    assert not state.exists()
    assert list(state.parent.glob("state.json.corrupt.*"))
    log = tmp_path / "logs" / "state_recovery.jsonl"
    assert "state_quarantined" in log.read_text(encoding="utf-8")


def _request(request_id: str, command: str, params: dict | None = None) -> str:
    return json.dumps({"protocol_version": 1, "type": "request", "request_id": request_id, "command": command, "params": params or {}})


def test_ipc_rejects_unconfirmed_download(tmp_path: Path) -> None:
    paths, _ = initialise_layout(tmp_path)
    user, core = userdata_template(), coredata_template()
    user["current_version"] = {"source": "command_line", "value": "", "path": "", "json_key": "version"}
    core["security"]["allowed_hosts"] = ["github.com"]
    paths["userdata"].write_text(json.dumps(user), encoding="utf-8")
    paths["coredata"].write_text(json.dumps(core), encoding="utf-8")
    output = io.StringIO()
    serve_stdio(tmp_path, io.StringIO(_request("1", "download", {"current_version": "1.0.0"}) + "\n" + _request("2", "shutdown") + "\n"), output)
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert records[0]["success"] is False
    assert "confirmed=true" in records[0]["error"]["message"]
    assert records[-1]["result"]["shutdown_completed"] is True


class _Input:
    def __init__(self) -> None:
        self.values: queue.Queue[str | None] = queue.Queue()

    def __iter__(self):
        while True:
            value = self.values.get()
            if value is None:
                return
            yield value


class _Output:
    def __init__(self) -> None:
        self.values: queue.Queue[dict] = queue.Queue()

    def write(self, value: str) -> int:
        for line in value.splitlines():
            if line:
                self.values.put(json.loads(line))
        return len(value)

    def flush(self) -> None:
        pass


def test_ipc_check_does_not_block_control_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started, release = threading.Event(), threading.Event()

    class SlowWorkflow:
        def __init__(self, *args, **kwargs):
            pass

        def check(self, current):
            started.set()
            assert release.wait(3)
            return CheckResult(current or "1.0.0", False, None, "latest")

    paths, _ = initialise_layout(tmp_path)
    user, core = userdata_template(), coredata_template()
    user["current_version"] = {"source": "command_line", "value": "", "path": "", "json_key": "version"}
    core["security"]["allowed_hosts"] = ["github.com"]
    paths["userdata"].write_text(json.dumps(user), encoding="utf-8")
    paths["coredata"].write_text(json.dumps(core), encoding="utf-8")
    monkeypatch.setattr("update_core.ipc.UpdateWorkflow", SlowWorkflow)
    source, output = _Input(), _Output()
    server = threading.Thread(target=serve_stdio, args=(tmp_path, source, output))
    server.start()
    source.values.put(_request("1", "check", {"current_version": "1.0.0"}) + "\n")
    assert output.values.get(timeout=1)["success"] is True
    assert started.wait(1)
    source.values.put(_request("2", "get_version") + "\n")
    response = output.values.get(timeout=1)
    assert response["request_id"] == "2" and response["success"] is True
    release.set()
    source.values.put(_request("3", "shutdown") + "\n")
    deadline = time.monotonic() + 3
    while True:
        response = output.values.get(timeout=max(0.1, deadline - time.monotonic()))
        if response.get("request_id") == "3":
            assert response["success"] is True
            break
    server.join(timeout=3)
    assert not server.is_alive()


def test_install_handoff_allows_ipc_process_to_shutdown(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    paths, _ = initialise_layout(root)
    user, core = userdata_template(), coredata_template()
    user["current_version"] = {"source": "command_line", "value": "", "path": "", "json_key": "version"}
    user["installer"]["request_admin"] = False
    user["interface"]["confirm_before_download"] = False
    user["interface"]["confirm_before_install"] = False
    core["security"]["allowed_hosts"] = ["github.com"]
    paths["userdata"].write_text(json.dumps(user), encoding="utf-8")
    paths["coredata"].write_text(json.dumps(core), encoding="utf-8")
    payload = b"not-a-real-exe"
    digest = hashlib.sha256(payload).hexdigest()
    bootstrap = tmp_path / "ipc_bootstrap.py"
    bootstrap.write_text(
        """
import hashlib, sys
from pathlib import Path
from update_core import ipc
from update_core.models import CheckResult, InstallerAsset, UpdateManifest
root = Path(sys.argv[1])
payload = b'not-a-real-exe'
asset = InstallerAsset('setup.exe', 'https://github.com/acme/setup.exe', len(payload), hashlib.sha256(payload).hexdigest())
manifest = UpdateManifest('1.0.1', 'stable', False, asset)
class FakeWorkflow:
    def __init__(self, config, data_dir, **kwargs): self.root = data_dir
    def check(self, current): return CheckResult(current or '1.0.0', True, manifest, 'update')
    def download(self, incoming, **kwargs):
        path = self.root / 'download' / 'setup.exe'; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload); return path
if __name__ == '__main__':
    ipc.UpdateWorkflow = FakeWorkflow
    raise SystemExit(ipc.serve_stdio(root))
""".strip(),
        encoding="utf-8",
    )
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen([sys.executable, str(bootstrap), str(root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    assert process.stdin and process.stdout
    try:
        process.stdin.write(_request("1", "download", {"current_version": "1.0.0"}) + "\n")
        process.stdin.flush()
        task_id = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = json.loads(process.stdout.readline())
            if record.get("event") == "task_finished":
                task_id = record["task_id"]
                break
        assert task_id
        process.stdin.write(_request("2", "install", {"task_id": task_id, "process_id": sleeper.pid}) + "\n" + _request("3", "shutdown") + "\n")
        process.stdin.flush()
        started = time.monotonic()
        assert process.wait(timeout=2) == 0
        assert time.monotonic() - started < 2
    finally:
        if process.poll() is None:
            process.kill()
        if sleeper.poll() is None:
            sleeper.terminate()
        sleeper.wait(timeout=3)
