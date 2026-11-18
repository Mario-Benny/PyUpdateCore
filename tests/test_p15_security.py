from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from update_core.config import config_template, coredata_template, load_config, load_split_config, userdata_template
from update_core.errors import ConfigError, ManifestError
from update_core.layout import initialise_layout
from update_core.manifest import parse_manifest
from update_core.signatures import canonical_manifest_bytes


def _signed_document(private: Ed25519PrivateKey) -> dict:
    document = {"schema_version": 1, "version": "1.2.3", "channel": "stable", "installer": {"name": "setup.exe", "url": "https://updates.example.com/setup.exe", "size": 10, "sha256": "ab" * 32}}
    document["signature"] = {"algorithm": "ed25519", "key_id": "main", "value": base64.b64encode(private.sign(canonical_manifest_bytes(document))).decode()}
    return document


def _signed_config(tmp_path: Path, public_key: str):
    path = tmp_path / "Data.json"
    data = config_template()
    data["security"].update({"allowed_hosts": ["updates.example.com"], "signature_required": True, "trusted_manifest_keys": {"main": public_key}})
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_config(path)


def test_ed25519_signature_and_tampering(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    document = _signed_document(private)
    assert parse_manifest(json.dumps(document).encode(), _signed_config(tmp_path, public)).version == "1.2.3"
    document["version"] = "9.9.9"
    with pytest.raises(ManifestError, match="签名"):
        parse_manifest(json.dumps(document).encode(), _signed_config(tmp_path, public))


@pytest.mark.parametrize("name", ["CON", "con.exe", "NUL.", "AUX ", "COM1.exe", "LPT9.msi", "bad?.exe"])
def test_windows_unsafe_installer_names_rejected(tmp_path: Path, name: str) -> None:
    data = config_template(); data["security"]["allowed_hosts"] = ["updates.example.com"]
    config_path = tmp_path / "Data.json"; config_path.write_text(json.dumps(data), encoding="utf-8")
    document = {"schema_version": 1, "version": "1.2.3", "channel": "stable", "installer": {"name": name, "url": "https://updates.example.com/a.exe", "size": 1, "sha256": "ab" * 32}}
    with pytest.raises(ManifestError, match="文件名"):
        parse_manifest(json.dumps(document).encode(), load_config(config_path))


def test_security_floor_cannot_be_disabled(tmp_path: Path) -> None:
    data = config_template(); data["security"].update({"allowed_hosts": ["updates.example.com"], "require_https": False})
    path = tmp_path / "Data.json"; path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="不可关闭"):
        load_config(path)


def test_new_layout_and_legacy_migration(tmp_path: Path) -> None:
    legacy = tmp_path / "UpdaterData"; legacy.mkdir()
    old = config_template(); old["security"]["allowed_hosts"] = ["updates.example.com"]
    (legacy / "Data.json").write_text(json.dumps(old), encoding="utf-8")
    (legacy / "State.json").write_text(json.dumps({"schema_version": 1, "downloads": {"x": {"status": "ready"}}}), encoding="utf-8")
    paths, migrated = initialise_layout(tmp_path)
    assert migrated is True and paths["userdata"].is_file() and paths["coredata"].is_file()
    assert load_split_config(paths["userdata"], paths["coredata"]).application_id == "your.application.id"
    assert json.loads(paths["state"].read_text(encoding="utf-8"))["downloads"]["x"]["status"] == "ready"
    assert legacy.is_dir()  # migration never deletes the old data


def test_first_run_creates_separated_templates(tmp_path: Path) -> None:
    paths, created = initialise_layout(tmp_path)
    assert created is True
    assert all(paths[key].exists() for key in ("data", "download", "temporary", "logs", "userdata", "coredata", "state"))
