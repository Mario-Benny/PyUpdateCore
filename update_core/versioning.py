from __future__ import annotations

import re

from .errors import VersionError

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise VersionError("版本号必须是字符串")
    normalized = value.strip().removeprefix("v").removeprefix("V")
    match = _VERSION.fullmatch(normalized)
    if not match:
        raise VersionError(f"版本号必须是三段纯数字：{value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def normalize_version(value: str) -> str:
    return ".".join(str(part) for part in parse_version(value))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)
