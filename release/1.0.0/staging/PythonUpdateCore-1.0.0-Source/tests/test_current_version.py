import json
from pathlib import Path

import pytest

from update_core.config import create_config_template, load_config
from update_core.current_version import read_current_version
from update_core.errors import VersionError


def test_reads_text_and_json_version_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "Data.json"
    create_config_template(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["security"]["allowed_hosts"] = ["github.com"]
    (tmp_path / "version.txt").write_text("v1.2.3\n", encoding="utf-8")
    data["current_version"].update({"source": "text_file", "path": "version.txt"})
    config_path.write_text(json.dumps(data), encoding="utf-8")
    assert read_current_version(load_config(config_path)) == "1.2.3"
    (tmp_path / "version.json").write_text('{"release": "2.0.1"}', encoding="utf-8")
    data["current_version"].update({"source": "json_file", "path": "version.json", "json_key": "release"})
    config_path.write_text(json.dumps(data), encoding="utf-8")
    assert read_current_version(load_config(config_path)) == "2.0.1"


def test_static_nested_json_and_command_line_override(tmp_path: Path) -> None:
    config_path = tmp_path / "Data.json"
    create_config_template(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["security"]["allowed_hosts"] = ["github.com"]
    data["current_version"].update({"source": "static", "value": "v1.2.2"})
    config_path.write_text(json.dumps(data), encoding="utf-8")
    assert read_current_version(load_config(config_path)) == "1.2.2"
    nested = tmp_path / "nested" / "version.json"; nested.parent.mkdir(); nested.write_text('{"application":{"version":"2.3.4"}}', encoding="utf-8")
    data["current_version"].update({"source": "json_file", "path": "nested/version.json", "json_key": "application.version"})
    config_path.write_text(json.dumps(data), encoding="utf-8")
    config = load_config(config_path)
    assert read_current_version(config) == "2.3.4"
    assert read_current_version(config, "3.0.0") == "3.0.0"


def test_json_version_errors_are_explicit_and_safe(tmp_path: Path) -> None:
    config_path = tmp_path / "Data.json"; create_config_template(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8")); data["security"]["allowed_hosts"] = ["github.com"]
    data["current_version"].update({"source": "json_file", "path": "missing.json", "json_key": "version"}); config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(VersionError): read_current_version(load_config(config_path))
    (tmp_path / "version.json").write_text('{"application":{"version": 7}}', encoding="utf-8")
    data["current_version"].update({"path": "version.json", "json_key": "application.version"}); config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(VersionError): read_current_version(load_config(config_path))
    data["current_version"]["json_key"] = "__import__('os').system('bad')"; config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(VersionError): read_current_version(load_config(config_path))
