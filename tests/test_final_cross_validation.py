from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from update_core.config import config_template, coredata_template, load_config, userdata_template
from update_core.errors import ManifestError, UpdaterError, VerificationError
from update_core.layout import initialise_layout
from update_core.models import InstallerAsset, UpdateManifest
from update_core.network import SafeHttpClient
from update_core.storage import read_state_json
from update_core.windows import secure_cache_directory
from update_core.workflow import UpdateWorkflow


def _config(tmp_path: Path):
    data = config_template()
    data["security"]["allowed_hosts"] = ["github.com"]
    path = tmp_path / "Data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_config(path)


def _request(request_id: str, command: str, params: dict | None = None) -> str:
    return json.dumps({"protocol_version": 1, "type": "request", "request_id": request_id, "command": command, "params": params or {}})


def test_safe_http_rejects_downgrade_unknown_and_private_hosts(tmp_path: Path, httpx_mock) -> None:
    config = _config(tmp_path)
    client = SafeHttpClient(config)
    try:
        httpx_mock.add_response(url="https://github.com/acme/checksum", status_code=302, headers={"Location": "http://github.com/acme/checksum"})
        with pytest.raises(ManifestError, match="HTTPS"):
            client.read_limited("https://github.com/acme/checksum", max_bytes=64 * 1024)
        with pytest.raises(ManifestError, match="白名单"):
            client.read_limited("https://untrusted.example/checksum", max_bytes=64 * 1024)
        with pytest.raises(ManifestError, match="内部网络"):
            client.read_limited("https://127.0.0.1/checksum", max_bytes=64 * 1024)
    finally:
        client.close()


def test_cache_acl_is_verified_and_acl_error_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache with spaces"
    secure_cache_directory(cache)
    verified = subprocess.run(["icacls", str(cache), "/verify"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert verified.returncode == 0

    failed = Mock(return_value=subprocess.CompletedProcess(["icacls"], 1, "", "denied"))
    monkeypatch.setattr("update_core.windows.subprocess.run", failed)
    with pytest.raises(UpdaterError, match="权限"):
        secure_cache_directory(tmp_path / "failing cache")


def test_install_rechecks_hash_after_wait_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    config = replace(config, wait_for_main_program=True, request_admin=False)
    downloads = tmp_path / "download"
    downloads.mkdir()
    installer = downloads / "harmless.exe"
    original = b"a" * 4096
    installer.write_bytes(original)
    manifest = UpdateManifest("1.0.1", "stable", False, InstallerAsset(installer.name, "https://github.com/acme/harmless.exe", len(original), hashlib.sha256(original).hexdigest()))
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    launched = []
    monkeypatch.setattr("update_core.workflow.secure_cache_directory", lambda _: None)
    monkeypatch.setattr("update_core.workflow.launch_installer", lambda *args: launched.append(args))
    workflow = UpdateWorkflow(config, tmp_path)
    errors: list[Exception] = []

    def install() -> None:
        try:
            workflow.install(installer, manifest, sleeper.pid)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=install)
    worker.start()
    time.sleep(0.3)
    installer.write_bytes(b"b" * len(original))
    sleeper.terminate()
    sleeper.wait(timeout=3)
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], VerificationError)
    assert not launched


def test_onefile_worker_survives_parent_exit_and_duplicate_install_is_rejected(tmp_path: Path) -> None:
    """Uses a source IPC parent but forces the real onefile EXE as detached worker."""
    project = Path(__file__).resolve().parents[1]
    onefile = project / "validation" / "v1.0.1" / "build" / "dist" / "PyUpdateCore-1.0.1.exe"
    if not onefile.is_file():
        pytest.skip("onefile 验收构建产物不存在（干净源码树），跳过依赖真实 EXE 的集成用例")
    root = tmp_path / "worker root with spaces"
    paths, _ = initialise_layout(root)
    user, core = userdata_template(), coredata_template()
    user["current_version"] = {"source": "command_line", "value": "", "path": "", "json_key": "version"}
    user["installer"].update({"request_admin": False, "arguments": ["--help"]})
    user["interface"].update({"confirm_before_download": False, "confirm_before_install": False})
    core["security"]["allowed_hosts"] = ["github.com"]
    paths["userdata"].write_text(json.dumps(user), encoding="utf-8")
    paths["coredata"].write_text(json.dumps(core), encoding="utf-8")
    bootstrap = tmp_path / "ipc_parent.py"
    bootstrap.write_text(
        """
import hashlib, json, shutil, sys
from pathlib import Path
from update_core import ipc
from update_core.models import CheckResult, InstallerAsset, UpdateManifest
root, onefile = Path(sys.argv[1]), Path(sys.argv[2])
digest = hashlib.sha256(onefile.read_bytes()).hexdigest()
asset = InstallerAsset('Harmless Installer.exe', 'https://github.com/acme/harmless.exe', onefile.stat().st_size, digest)
manifest = UpdateManifest('1.0.1', 'stable', False, asset)
class FakeWorkflow:
    def __init__(self, config, data_dir, **kwargs): self.root = data_dir
    def check(self, current): return CheckResult(current or '1.0.0', True, manifest, 'update')
    def download(self, incoming, **kwargs):
        path = self.root / 'download' / asset.name; path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(onefile, path); return path
def onefile_worker(data_root, installer, item, pid):
    payload = json.dumps({'version': item.version, 'channel': item.channel, 'mandatory': item.mandatory, 'installer': {'name': item.installer.name, 'url': item.installer.url, 'size': item.installer.size, 'sha256': item.installer.sha256}, 'published_at': item.published_at, 'release_notes_url': item.release_notes_url}, separators=(',', ':'))
    result = [str(onefile), '--install-worker', '--data-root', str(data_root), '--installer-path', installer, '--manifest-json', payload]
    if pid is not None: result += ['--process-id', str(pid)]
    return result
if __name__ == '__main__':
    ipc.UpdateWorkflow = FakeWorkflow
    ipc._handoff_command = onefile_worker
    raise SystemExit(ipc.serve_stdio(root))
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project) + os.pathsep + env.get("PYTHONPATH", "")
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    parent = subprocess.Popen([sys.executable, str(bootstrap), str(root), str(onefile)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    assert parent.stdin and parent.stdout
    try:
        parent.stdin.write(_request("download", "download", {"current_version": "1.0.0"}) + "\n")
        parent.stdin.flush()
        task_id = None
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            raw = parent.stdout.readline()
            assert raw, "IPC parent exited before completing download"
            record = json.loads(raw)
            if record.get("event") == "task_finished":
                task_id = record["task_id"]
                break
        assert task_id
        parent.stdin.write(_request("install-1", "install", {"task_id": task_id, "process_id": sleeper.pid}) + "\n")
        parent.stdin.write(_request("install-2", "install", {"task_id": task_id, "process_id": sleeper.pid}) + "\n")
        parent.stdin.write(_request("shutdown", "shutdown") + "\n")
        parent.stdin.flush()
        started = time.monotonic()
        assert parent.wait(timeout=4) == 0
        assert time.monotonic() - started < 4
        records = [json.loads(line) for line in parent.stdout.read().splitlines()]
        assert sum(record.get("event") == "install_handoff" for record in records) == 1
        duplicate = next(record for record in records if record.get("request_id") == "install-2")
        assert duplicate["success"] is False
        sleeper.terminate()
        sleeper.wait(timeout=3)
        state_path = root / "data" / "state.json"
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            state = read_state_json(state_path, default={})
            if state.get("status") == "installer_started":
                break
            time.sleep(0.2)
        assert state.get("status") == "installer_started"
    finally:
        if parent.poll() is None:
            parent.kill()
        if sleeper.poll() is None:
            sleeper.terminate()
        sleeper.wait(timeout=3)
