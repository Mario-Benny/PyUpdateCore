"""Safe native-network proxy discovery; PAC is reported but never executed."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import ConfigError


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
        if value: return ProxyDecision(mode, value, f"environment:{key}")
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
