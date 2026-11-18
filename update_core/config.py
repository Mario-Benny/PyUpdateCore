from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .storage import atomic_write_json, read_config_json

SCHEMA_VERSION = 1
USERDATA_SCHEMA_VERSION = 1
COREDATA_SCHEMA_VERSION = 1


def config_template() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "application": {"id": "your.application.id", "name": "Your Application"},
        "current_version": {"source": "command_line", "value": "", "path": "", "json_key": "version"},
        "update_source": {"provider": "manifest", "manifest_url": "", "channel": "stable", "repository": "", "asset_pattern": "", "sha256_asset_pattern": "*.sha256", "release_tag": ""},
        "download": {"default_threads": 4, "allowed_threads": [1, 4, 16], "fallback_to_single": True, "resume": True, "retry_count": 5, "timeout_seconds": 30, "stall_timeout_seconds": 60, "max_file_size_mb": 2048, "segment_storage": "separate_files", "min_segment_size_mb": 1, "verify_etag": True, "delete_parts_after_merge": True},
        "security": {"require_https": True, "require_sha256": True, "allowed_hosts": [], "signature_required": False, "trusted_manifest_keys": {}},
        "installer": {"wait_for_main_program": True, "wait_timeout_seconds": 600, "request_admin": True, "arguments": [], "delete_after_install": False},
        "restart": {"enabled": False, "program_path": "", "arguments": []},
        "interface": {"language": "zh-CN", "show_progress": True, "confirm_before_download": True, "confirm_before_install": True},
        "logging": {"enabled": True, "retention_days": 14},
    }


def userdata_template() -> dict[str, Any]:
    legacy = config_template()
    return {"schema_version": USERDATA_SCHEMA_VERSION, **{key: legacy[key] for key in ("application", "current_version", "update_source", "installer", "restart", "interface")}}


def coredata_template() -> dict[str, Any]:
    legacy = config_template()
    core = {"schema_version": COREDATA_SCHEMA_VERSION, **{key: legacy[key] for key in ("download", "security", "logging")}}
    core["network"] = {"download_channel": "native", "proxy_mode": "system", "proxy_url": "", "browser": {"type": "auto", "mode": "proxy_probe"}}
    core["security"]["signature_required"] = True
    return core


def create_config_template(path: Path) -> bool:
    if path.exists():
        return False
    atomic_write_json(path, config_template())
    return True


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"Data.json 缺少对象：{name}")
    return value


def _string(section: dict[str, Any], name: str, *, required: bool = True) -> str:
    value = section.get(name, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ConfigError(f"配置字段无效：{name}")
    return value.strip()


def _bool(section: dict[str, Any], name: str) -> bool:
    value = section.get(name)
    if not isinstance(value, bool):
        raise ConfigError(f"配置字段必须为布尔值：{name}")
    return value


def _positive_int(section: dict[str, Any], name: str) -> int:
    value = section.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"配置字段必须为正整数：{name}")
    return value


def _positive_int_or_default(section: dict[str, Any], name: str, default: int) -> int:
    value = section.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"配置字段必须为正整数：{name}")
    return value


def _arguments(section: dict[str, Any], name: str) -> tuple[str, ...]:
    value = section.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"配置字段必须为字符串数组：{name}")
    return tuple(value)


@dataclass(frozen=True)
class UpdaterConfig:
    path: Path
    application_id: str
    application_name: str
    version_source: str
    version_value: str
    version_path: str
    version_json_key: str
    provider: str
    manifest_url: str
    channel: str
    repository: str
    asset_pattern: str
    sha256_asset_pattern: str
    release_tag: str
    resume: bool
    retry_count: int
    timeout_seconds: int
    stall_timeout_seconds: int
    max_file_size_bytes: int
    default_threads: int
    allowed_threads: tuple[int, ...]
    fallback_to_single: bool
    min_segment_size_bytes: int
    verify_etag: bool
    delete_parts_after_merge: bool
    require_https: bool
    require_sha256: bool
    allowed_hosts: frozenset[str]
    wait_for_main_program: bool
    request_admin: bool
    installer_arguments: tuple[str, ...]
    delete_after_install: bool
    restart_enabled: bool
    restart_program_path: str
    restart_arguments: tuple[str, ...]
    language: str
    show_progress: bool
    confirm_before_download: bool
    confirm_before_install: bool
    logging_enabled: bool
    logging_retention_days: int
    signature_required: bool
    trusted_manifest_keys: dict[str, str]
    wait_timeout_seconds: int
    download_channel: str
    proxy_mode: str
    proxy_url: str


def _load_config_data(data: dict[str, Any], path: Path) -> UpdaterConfig:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError("不支持或缺失 schema_version")
    app = _section(data, "application")
    current = _section(data, "current_version")
    source = _string(current, "source")
    if source not in {"static", "command_line", "json_file", "text_file", "exe_version"}:
        raise ConfigError("current_version.source 不受支持")
    update = _section(data, "update_source")
    provider = _string(update, "provider")
    if provider not in {"manifest", "github_release"}:
        raise ConfigError("update_source.provider 不受支持")
    download = _section(data, "download")
    allowed_threads_raw = download.get("allowed_threads")
    if not isinstance(allowed_threads_raw, list) or not allowed_threads_raw or any(item not in {1, 4, 16} for item in allowed_threads_raw):
        raise ConfigError("download.allowed_threads 仅允许 1、4、16")
    default_threads = _positive_int(download, "default_threads")
    if default_threads not in allowed_threads_raw:
        raise ConfigError("download.default_threads 必须包含在 allowed_threads 中")
    security = _section(data, "security")
    hosts = security.get("allowed_hosts")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(host, str) and host.strip() for host in hosts):
        raise ConfigError("security.allowed_hosts 必须是非空主机名字符串数组")
    installer = _section(data, "installer")
    restart = _section(data, "restart")
    interface = _section(data, "interface")
    logging = _section(data, "logging")
    network = data.get("network", {"download_channel": "native", "proxy_mode": "system", "proxy_url": ""})
    if not isinstance(network, dict): raise ConfigError("network 必须是对象")
    channel = _string(network, "download_channel")
    proxy_mode = _string(network, "proxy_mode")
    if channel not in {"native", "browser_proxy", "browser_download"}: raise ConfigError("network.download_channel 不受支持")
    if proxy_mode not in {"direct", "system", "custom"}: raise ConfigError("network.proxy_mode 不受支持")
    require_https = _bool(security, "require_https")
    require_sha256 = _bool(security, "require_sha256")
    if not require_https or not require_sha256:
        raise ConfigError("HTTPS 与 SHA-256 是不可关闭的安全底线")
    trusted_keys = security.get("trusted_manifest_keys", {})
    if not isinstance(trusted_keys, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in trusted_keys.items()):
        raise ConfigError("security.trusted_manifest_keys 必须是字符串映射")
    if _string(update, "manifest_url", required=False) and _bool(security, "signature_required") and not trusted_keys:
        raise ConfigError("启用签名验证时必须在 coredata.json 配置受信任的清单公钥")
    return UpdaterConfig(
        path=path, application_id=_string(app, "id"), application_name=_string(app, "name"),
        version_source=source, version_value=_string(current, "value", required=False), version_path=_string(current, "path", required=False), version_json_key=_string(current, "json_key", required=False),
        provider=provider, manifest_url=_string(update, "manifest_url", required=False), channel=_string(update, "channel"),
        repository=_string(update, "repository", required=False), asset_pattern=_string(update, "asset_pattern", required=False), sha256_asset_pattern=_string(update, "sha256_asset_pattern", required=False), release_tag=_string(update, "release_tag", required=False),
        resume=_bool(download, "resume"), retry_count=_positive_int(download, "retry_count"), timeout_seconds=_positive_int(download, "timeout_seconds"), stall_timeout_seconds=_positive_int(download, "stall_timeout_seconds"), max_file_size_bytes=_positive_int(download, "max_file_size_mb") * 1024 * 1024,
        default_threads=default_threads, allowed_threads=tuple(allowed_threads_raw), fallback_to_single=_bool(download, "fallback_to_single"), min_segment_size_bytes=_positive_int(download, "min_segment_size_mb") * 1024 * 1024, verify_etag=_bool(download, "verify_etag"), delete_parts_after_merge=_bool(download, "delete_parts_after_merge"),
        require_https=True, require_sha256=True, allowed_hosts=frozenset(host.lower().strip() for host in hosts),
        wait_for_main_program=_bool(installer, "wait_for_main_program"), request_admin=_bool(installer, "request_admin"), installer_arguments=_arguments(installer, "arguments"), delete_after_install=_bool(installer, "delete_after_install"),
        restart_enabled=_bool(restart, "enabled"), restart_program_path=_string(restart, "program_path", required=False), restart_arguments=_arguments(restart, "arguments"),
        language=_string(interface, "language"), show_progress=_bool(interface, "show_progress"), confirm_before_download=_bool(interface, "confirm_before_download"), confirm_before_install=_bool(interface, "confirm_before_install"),
        logging_enabled=_bool(logging, "enabled"), logging_retention_days=_positive_int(logging, "retention_days"), signature_required=_bool(security, "signature_required"), trusted_manifest_keys=dict(trusted_keys), wait_timeout_seconds=_positive_int_or_default(installer, "wait_timeout_seconds", 600), download_channel=channel, proxy_mode=proxy_mode, proxy_url=_string(network, "proxy_url", required=False),
    )


def load_config(path: Path) -> UpdaterConfig:
    return _load_config_data(read_config_json(path), path)


def load_split_config(userdata_path: Path, coredata_path: Path) -> UpdaterConfig:
    user, core = read_config_json(userdata_path), read_config_json(coredata_path)
    if user.get("schema_version") != USERDATA_SCHEMA_VERSION or core.get("schema_version") != COREDATA_SCHEMA_VERSION:
        raise ConfigError("userdata.json 或 coredata.json schema_version 不受支持")
    merged: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    merged.update({key: value for key, value in user.items() if key != "schema_version"})
    merged.update({key: value for key, value in core.items() if key != "schema_version"})
    return _load_config_data(merged, userdata_path)
