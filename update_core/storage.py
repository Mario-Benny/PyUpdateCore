from __future__ import annotations

import json
import copy
import os
import tempfile
import errno
import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .errors import ConfigError, LockError

_STATE_LOCK = threading.Lock()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lenient reader for disposable per-download resume records only."""
    if not path.is_file():
        return {} if default is None else copy.deepcopy(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {} if default is None else copy.deepcopy(default)
    return data if isinstance(data, dict) else ({} if default is None else copy.deepcopy(default))


def read_config_json(path: Path) -> dict[str, Any]:
    """Read user-controlled configuration without hiding corruption."""
    if not path.is_file():
        raise ConfigError(f"配置文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"配置文件损坏或无法读取：{path}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件根节点必须是对象：{path}")
    return data


def _record_state_recovery(path: Path, reason: str, quarantined: Path | None) -> None:
    log_path = path.parent.parent / "logs" / "state_recovery.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "state_quarantined",
            "state_path": str(path),
            "quarantined_path": str(quarantined) if quarantined else None,
            "reason": reason,
        }
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_state_json(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    """Quarantine a corrupt durable state file, log it, then return a safe default."""
    if not path.is_file():
        return copy.deepcopy(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("state root is not an object")
        return data
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        quarantined: Path | None = None
        try:
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            quarantined = path.with_name(f"{path.name}.corrupt.{stamp}")
            suffix = 1
            while quarantined.exists():
                quarantined = path.with_name(f"{path.name}.corrupt.{stamp}.{suffix}")
                suffix += 1
            os.replace(path, quarantined)
        except OSError:
            quarantined = None
        _record_state_recovery(path, type(exc).__name__, quarantined)
        return copy.deepcopy(default)


DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 1,
    "status": "idle",
    "last_check": None,
    "downloads": {},
}


def merge_state(path: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Atomically merge a shallow state patch without dropping peer fields."""
    with _STATE_LOCK:
        state = read_state_json(path, default=DEFAULT_STATE)
        state.setdefault("schema_version", 1)
        state.setdefault("downloads", {})
        for key, value in patch.items():
            if key == "downloads" and isinstance(value, dict):
                downloads = state.setdefault("downloads", {})
                if not isinstance(downloads, dict):
                    downloads = state["downloads"] = {}
                downloads.update(value)
            else:
                state[key] = value
        atomic_write_json(path, state)
    return state


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


class UpdateLock(AbstractContextManager["UpdateLock"]):
    """Simple cross-process lock; never silently steals another updater's task."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._created = False

    def __enter__(self) -> "UpdateLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner_pid = int(self.path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                owner_pid = 0
            if not _pid_alive(owner_pid):
                self.path.unlink(missing_ok=True)
                return self.__enter__()
            raise LockError("已有更新任务正在运行") from None
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        self._created = True
        return self

    def __exit__(self, *_: object) -> None:
        if self._created:
            self.path.unlink(missing_ok=True)
