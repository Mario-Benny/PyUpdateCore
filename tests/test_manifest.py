import json
from pathlib import Path

import pytest

from update_core.config import create_config_template, load_config
from update_core.errors import ManifestError
from update_core.manifest import parse_manifest


def _config(tmp_path: Path):
    path = tmp_path / "Data.json"
    create_config_template(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["security"]["allowed_hosts"] = ["github.com"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_config(path)


def _manifest(**installer_changes: object) -> bytes:
    installer = {"name": "app-1.2.3.exe", "url": "https://github.com/acme/app.exe", "size": 1024, "sha256": "ab" * 32}
    installer.update(installer_changes)
    return json.dumps({"schema_version": 1, "version": "1.2.3", "channel": "stable", "mandatory": False, "installer": installer}).encode()


def test_manifest_requires_safe_verified_asset(tmp_path: Path) -> None:
    parsed = parse_manifest(_manifest(), _config(tmp_path))
    assert parsed.version == "1.2.3"
    assert parsed.installer.name == "app-1.2.3.exe"


@pytest.mark.parametrize("change", [{"url": "http://github.com/a.exe"}, {"name": "../a.exe"}, {"sha256": "bad"}, {"size": 0}])
def test_manifest_rejects_unsafe_asset(tmp_path: Path, change: dict[str, object]) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(_manifest(**change), _config(tmp_path))
