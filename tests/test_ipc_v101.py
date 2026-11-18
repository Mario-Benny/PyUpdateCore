import io
import json
from pathlib import Path

from update_core.ipc import serve_stdio
from update_core.version import ENGINE_NAME, ENGINE_VERSION


def _request(request_id: str, command: str, params: dict | None = None) -> str:
    return json.dumps({"protocol_version": 1, "type": "request", "request_id": request_id, "command": command, "params": params or {}})


def test_version_and_shutdown_are_json_lines(tmp_path: Path) -> None:
    output = io.StringIO()
    code = serve_stdio(tmp_path, io.StringIO(_request("1", "get_version") + "\n" + _request("2", "shutdown") + "\n"), output)
    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert code == 0
    assert lines[0]["result"] == {"engine_name": ENGINE_NAME, "engine_version": ENGINE_VERSION, "protocol_version": 1}
    assert lines[1]["result"] == {"shutdown_completed": True}
