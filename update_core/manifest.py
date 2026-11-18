from __future__ import annotations

import json
import ipaddress
import re
from pathlib import PurePath
from urllib.parse import urlparse

from .config import UpdaterConfig
from .errors import ManifestError
from .models import InstallerAsset, UpdateManifest
from .signatures import verify_manifest_signature
from .versioning import normalize_version

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|CLOCK\$|COM[1-9]|LPT[1-9])$", re.I)


def validate_url(value: str, config: UpdaterConfig) -> str:
    parsed = urlparse(value)
    if config.require_https and parsed.scheme.lower() != "https":
        raise ManifestError("只允许 HTTPS 下载地址")
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname:
        raise ManifestError("URL 无效")
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ManifestError("禁止访问本地或内部网络地址")
    if config.allowed_hosts and host not in config.allowed_hosts:
        raise ManifestError(f"下载主机不在白名单中：{parsed.hostname}")
    return value


def parse_manifest(payload: bytes, config: UpdaterConfig) -> UpdateManifest:
    if not payload or len(payload) > 256 * 1024:
        raise ManifestError("更新清单为空或过大")
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("更新清单不是有效 UTF-8 JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ManifestError("不支持的 update.json 格式")
    if config.signature_required:
        verify_manifest_signature(data, config.trusted_manifest_keys)
    if data.get("channel") != config.channel:
        raise ManifestError("更新通道不匹配")
    installer = data.get("installer")
    if not isinstance(installer, dict):
        raise ManifestError("更新清单缺少 installer")
    name, url, size, digest = installer.get("name"), installer.get("url"), installer.get("size"), installer.get("sha256")
    stem = PurePath(name).stem.rstrip(" .") if isinstance(name, str) else ""
    if not isinstance(name, str) or not name or name != name.rstrip(" .") or PurePath(name).name != name or any(char in name for char in '\\/<>":|?*') or any(ord(char) < 32 for char in name) or _RESERVED.fullmatch(stem):
        raise ManifestError("安装器文件名不安全")
    if not name.lower().endswith(".exe"):
        raise ManifestError("当前版本仅允许 EXE 安装器")
    if not isinstance(url, str):
        raise ManifestError("安装器 URL 无效")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > config.max_file_size_bytes:
        raise ManifestError("安装器大小无效或超出配置限制")
    if config.require_sha256 and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
        raise ManifestError("安装器必须提供有效 SHA-256")
    if not isinstance(digest, str):
        digest = ""
    notes = data.get("release_notes_url", "")
    if notes and (not isinstance(notes, str)):
        raise ManifestError("release_notes_url 无效")
    try:
        version = normalize_version(data.get("version", ""))
    except Exception as exc:
        raise ManifestError("更新清单版本号无效") from exc
    return UpdateManifest(version, config.channel, bool(data.get("mandatory", False)), InstallerAsset(name, validate_url(url, config), size, digest.lower()), str(data.get("published_at", "")), notes)
