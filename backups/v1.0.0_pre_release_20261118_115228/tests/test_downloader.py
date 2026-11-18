import hashlib
import json
from pathlib import Path

import pytest

from update_core.config import create_config_template, load_config
from update_core.downloader import Downloader
from update_core.errors import DownloadError
from update_core.models import InstallerAsset


def _config(tmp_path: Path):
    path = tmp_path / "Data.json"
    create_config_template(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["security"]["allowed_hosts"] = ["updates.example.com"]
    data["download"]["retry_count"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_config(path)


def test_download_verifies_and_finishes(tmp_path: Path, httpx_mock) -> None:
    content = b"verified installer"
    asset = InstallerAsset("setup.exe", "https://updates.example.com/setup.exe", len(content), hashlib.sha256(content).hexdigest())
    httpx_mock.add_response(url=asset.url, content=content, headers={"Content-Length": str(len(content))})
    path = Downloader(_config(tmp_path), tmp_path / "downloads", tmp_path / "State.json").download(asset)
    assert path.read_bytes() == content
    assert not path.with_suffix(".exe.part").exists()


def test_resume_rejects_wrong_content_range(tmp_path: Path, httpx_mock) -> None:
    content = b"abcdef"
    asset = InstallerAsset("setup.exe", "https://updates.example.com/setup.exe", len(content), hashlib.sha256(content).hexdigest())
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "setup.exe.part").write_bytes(b"abc")
    httpx_mock.add_response(url=asset.url, status_code=206, content=b"def", headers={"Content-Range": "bytes 2-5/6", "Content-Length": "3"})
    httpx_mock.add_response(url=asset.url, status_code=206, content=b"def", headers={"Content-Range": "bytes 2-5/6", "Content-Length": "3"})
    with pytest.raises(DownloadError, match="Content-Range"):
        Downloader(_config(tmp_path), downloads, tmp_path / "State.json").download(asset)
