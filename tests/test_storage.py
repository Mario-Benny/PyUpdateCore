from pathlib import Path

import pytest

from update_core.errors import LockError
from update_core.storage import UpdateLock, atomic_write_json, read_json


def test_atomic_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "State.json"
    atomic_write_json(path, {"message": "更新", "status": "idle"})
    assert read_json(path) == {"message": "更新", "status": "idle"}


def test_parallel_task_lock_is_rejected(tmp_path: Path) -> None:
    lock_path = tmp_path / "updater.lock"
    with UpdateLock(lock_path):
        with pytest.raises(LockError):
            with UpdateLock(lock_path):
                pass
    assert not lock_path.exists()
