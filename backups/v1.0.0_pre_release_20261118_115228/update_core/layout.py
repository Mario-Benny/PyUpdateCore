"""New data/download/log layout and conservative migration from UpdaterData."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from .config import coredata_template, userdata_template
from .storage import DEFAULT_STATE, atomic_write_json, read_json


def layout_paths(root: Path) -> dict[str, Path]:
    return {"root": root, "data": root / "data", "userdata": root / "data" / "userdata.json", "coredata": root / "data" / "coredata.json", "state": root / "data" / "state.json", "download": root / "download", "temporary": root / "download" / "download_tmp", "logs": root / "logs"}


def initialise_layout(root: Path) -> tuple[dict[str, Path], bool]:
    paths = layout_paths(root.resolve())
    for key in ("data", "download", "temporary", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    migrated = False
    created = not paths["userdata"].exists() and not (root / "UpdaterData").is_dir()
    legacy = root / "UpdaterData"
    if not paths["userdata"].exists() and legacy.is_dir():
        _migrate_legacy(legacy, paths)
        migrated = True
    if not paths["userdata"].exists(): atomic_write_json(paths["userdata"], userdata_template())
    if not paths["coredata"].exists(): atomic_write_json(paths["coredata"], coredata_template())
    if not paths["state"].exists(): atomic_write_json(paths["state"], DEFAULT_STATE)
    return paths, migrated or created


def _migrate_legacy(legacy: Path, paths: dict[str, Path]) -> None:
    # Keep a durable marker.  Existing target files are never overwritten, so a
    # reboot during migration is safely resumable on the next launch.
    marker = paths["data"] / "migration_state.json"
    atomic_write_json(marker, {"status": "in_progress", "started_at": int(time.time()), "source": str(legacy)})
    old_data = read_json(legacy / "Data.json")
    user = userdata_template(); core = coredata_template()
    for key in ("application", "current_version", "update_source", "installer", "restart", "interface"):
        if isinstance(old_data.get(key), dict): user[key] = old_data[key]
    for key in ("download", "security", "logging"):
        if isinstance(old_data.get(key), dict): core[key] = old_data[key]
    atomic_write_json(paths["userdata"], user)
    atomic_write_json(paths["coredata"], core)
    old_state = read_json(legacy / "State.json", default=DEFAULT_STATE)
    atomic_write_json(paths["state"], old_state)
    old_downloads = legacy / "downloads"
    if old_downloads.is_dir():
        target = paths["temporary"] / "legacy_unverified"
        if not target.exists(): shutil.copytree(old_downloads, target)
    atomic_write_json(marker, {"status": "completed", "completed_at": int(time.time()), "source": str(legacy)})
