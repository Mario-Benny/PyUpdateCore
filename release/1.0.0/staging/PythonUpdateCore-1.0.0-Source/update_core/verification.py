from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .errors import VerificationError
from .models import InstallerAsset

RESERVE_BYTES = 64 * 1024 * 1024


def ensure_disk_space(directory: Path, required_bytes: int) -> None:
    if required_bytes < 0 or shutil.disk_usage(directory).free < required_bytes + RESERVE_BYTES:
        raise VerificationError("更新缓存所在磁盘空间不足")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_installer(path: Path, asset: InstallerAsset, *, require_sha256: bool) -> None:
    if not path.is_file() or path.stat().st_size != asset.size:
        raise VerificationError("安装包大小与更新清单不一致")
    if require_sha256 and sha256_file(path).lower() != asset.sha256:
        raise VerificationError("安装包 SHA-256 校验失败")
