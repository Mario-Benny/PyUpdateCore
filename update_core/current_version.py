from __future__ import annotations

import ctypes
import json
import re
from pathlib import Path

from .config import UpdaterConfig
from .errors import VersionError
from .versioning import normalize_version


def _configured_path(config: UpdaterConfig) -> Path:
    if not config.version_path:
        raise VersionError("当前版本来源需要 current_version.path")
    path = Path(config.version_path)
    return path if path.is_absolute() else (config.path.parent / path).resolve()


def _exe_version(path: Path) -> str:
    if not path.is_file():
        raise VersionError(f"EXE 文件不存在：{path}")
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise VersionError("EXE 没有可读取的版本资源")
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise VersionError("无法读取 EXE 版本资源")
    value = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
        raise VersionError("无法解析 EXE 版本资源")
    words = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32))
    ms, ls = words[2], words[3]  # VS_FIXEDFILEINFO: dwFileVersionMS / LS
    return f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}"


def read_current_version(config: UpdaterConfig, command_line_version: str | None = None) -> str:
    # Explicit caller input always wins, for every configured source.
    if command_line_version is not None:
        return normalize_version(command_line_version)
    source = config.version_source
    if source == "static":
        if not config.version_value:
            raise VersionError("static 当前版本需要 current_version.value")
        return normalize_version(config.version_value)
    if source == "command_line":
        if not command_line_version:
            raise VersionError("必须通过 --current-version 提供当前版本")
        return normalize_version(command_line_version)
    path = _configured_path(config)
    if source == "text_file":
        try:
            return normalize_version(path.read_text(encoding="utf-8-sig").strip())
        except OSError as exc:
            raise VersionError(f"无法读取版本文本：{exc}") from exc
    if source == "json_file":
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VersionError("无法读取 version.json") from exc
        if not isinstance(data, dict):
            raise VersionError("version.json 根节点必须是对象")
        key = config.version_json_key
        if not key or any(not re.fullmatch(r"[A-Za-z0-9_-]+", item) for item in key.split(".")):
            raise VersionError("json_key 只能使用以点分隔的对象字段名")
        value: object = data
        for item in key.split("."):
            if not isinstance(value, dict) or item not in value:
                raise VersionError(f"version.json 缺少字段：{key}")
            value = value[item]
        if not isinstance(value, str):
            raise VersionError(f"version.json 字段 {key} 必须是字符串版本号")
        return normalize_version(value)
    if source == "exe_version":
        return normalize_version(_exe_version(path))
    raise VersionError("不支持的当前版本来源")
