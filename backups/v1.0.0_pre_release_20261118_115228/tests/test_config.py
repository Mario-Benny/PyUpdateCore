from pathlib import Path

import json
import pytest

from update_core.config import create_config_template, load_config
from update_core.errors import ConfigError
from update_core.storage import atomic_write_json, read_json


def test_config_template_is_created_once(tmp_path: Path) -> None:
    path = tmp_path / "Data.json"
    assert create_config_template(path)
    assert not create_config_template(path)
    payload = read_json(path)
    payload["security"]["allowed_hosts"] = ["github.com"]
    atomic_write_json(path, payload)
    config = load_config(path)
    assert config.application_id == "your.application.id"
    assert config.version_source == "command_line"


def test_rejects_executable_config_source(tmp_path: Path) -> None:
    path = tmp_path / "Data.json"
    create_config_template(path)
    payload = read_json(path)
    payload["security"]["allowed_hosts"] = ["github.com"]
    payload["current_version"]["source"] = "powershell"
    atomic_write_json(path, payload)
    with pytest.raises(ConfigError, match="不受支持"):
        load_config(path)
