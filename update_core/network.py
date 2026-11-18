"""Safe native-network proxy discovery; PAC is reported but never executed."""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import httpx

from .config import UpdaterConfig
from .errors import ConfigError
from .errors import DownloadError
from .manifest import validate_url


MAX_REDIRECTS = 5
_REDIRECTS = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class ProxyDecision:
    mode: str
    proxy_url: str | None
    source: str
    pac_url: str | None = None


def resolve_proxy(mode: str, custom_url: str = "") -> ProxyDecision:
    if mode == "direct": return ProxyDecision(mode, None, "explicit")
    if mode == "custom":
        if not custom_url.startswith(("http://", "https://", "socks5://")):
            raise ConfigError("custom 代理必须是 http(s) 或 socks5 URL")
        return ProxyDecision(mode, custom_url, "custom")
    if mode != "system": raise ConfigError("proxy_mode 仅支持 direct、system、custom")
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            candidate = value if "://" in value else f"http://{value}"
            return ProxyDecision(mode, candidate, f"environment:{key}")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings") as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip() if enabled else ""
            try: pac = str(winreg.QueryValueEx(key, "AutoConfigURL")[0]).strip()
            except FileNotFoundError: pac = ""
        if server:
            candidate = server.split(";")[0].split("=", 1)[-1].strip()
            return ProxyDecision(mode, candidate if "://" in candidate else f"http://{candidate}", "windows-internet-settings", pac or None)
        if pac: return ProxyDecision(mode, None, "windows-pac-unsupported", pac)
    except (OSError, ImportError, ValueError): pass
    return ProxyDecision(mode, None, "system-no-proxy")


class SafeHttpClient:
    """HTTP client with mandatory per-hop URL validation for updater content."""

    def __init__(self, config: UpdaterConfig) -> None:
        timeout = httpx.Timeout(
            config.timeout_seconds,
            connect=config.timeout_seconds,
            read=min(config.timeout_seconds, config.stall_timeout_seconds),
            write=config.timeout_seconds,
            pool=config.timeout_seconds,
        )
        decision = resolve_proxy(config.proxy_mode, config.proxy_url)
        if decision.pac_url and not decision.proxy_url:
            raise DownloadError("检测到 PAC 自动代理；当前版本不会执行 PAC，拒绝静默直连")
        self.config = config
        self.proxy_decision = decision
        try:
            self.client = httpx.Client(
                follow_redirects=False,
                timeout=timeout,
                proxy=decision.proxy_url,
                trust_env=False,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=8),
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise DownloadError("代理配置无效，无法建立 HTTP 客户端") from exc

    @contextmanager
    def stream(self, url: str, headers: dict[str, str] | None = None) -> Iterator[httpx.Response]:
        target, request_headers = validate_url(url, self.config), dict(headers or {})
        for _ in range(MAX_REDIRECTS + 1):
            with self.client.stream("GET", target, headers=request_headers, follow_redirects=False) as response:
                validate_url(str(response.url), self.config)
                if response.status_code not in _REDIRECTS:
                    yield response
                    return
                location = response.headers.get("Location")
                if not location:
                    raise DownloadError("重定向响应缺少 Location")
                target = validate_url(httpx.URL(str(response.url)).join(location).__str__(), self.config)
                request_headers.pop("Authorization", None)
                request_headers.pop("Cookie", None)
        raise DownloadError("重定向次数超过安全限制")

    def read_limited(self, url: str, *, max_bytes: int, headers: dict[str, str] | None = None) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正整数")
        content = bytearray()
        try:
            with self.stream(url, headers) as response:
                if response.status_code != 200:
                    raise DownloadError(f"服务器返回 HTTP {response.status_code}")
                declared = response.headers.get("Content-Length")
                if declared is not None and (not declared.isdigit() or int(declared) > max_bytes):
                    raise DownloadError("响应超过安全大小限制")
                for chunk in response.iter_raw():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise DownloadError("响应超过安全大小限制")
        except httpx.HTTPError as exc:
            raise DownloadError("网络请求失败") from exc
        return bytes(content)

    def close(self) -> None:
        self.client.close()
