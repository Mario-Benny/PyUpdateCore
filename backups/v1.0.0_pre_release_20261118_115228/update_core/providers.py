"""Provider abstractions; download transport remains in Downloader."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ConfigError, DownloadError
from .models import InstallerAsset, UpdateManifest
from .network import resolve_proxy
from .versioning import normalize_version


@dataclass(frozen=True)
class GithubReleaseConfig:
    repository: str
    channel: str
    asset_pattern: str
    sha256_asset_pattern: str
    release_tag: str = ""


class GithubReleaseProvider:
    API = "https://api.github.com/repos"

    def __init__(self, settings: GithubReleaseConfig, timeout_seconds: int, proxy_mode: str = "system", proxy_url: str = "") -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", settings.repository):
            raise ConfigError("github_release.repository 必须为 owner/repository")
        self.settings, self.timeout_seconds, self.proxy_mode, self.proxy_url = settings, timeout_seconds, proxy_mode, proxy_url

    def fetch(self) -> UpdateManifest:
        suffix = f"tags/{self.settings.release_tag}" if self.settings.release_tag else "latest"
        url = f"{self.API}/{self.settings.repository}/releases/{suffix}"
        try:
            decision = resolve_proxy(self.proxy_mode, self.proxy_url)
            if decision.pac_url and not decision.proxy_url: raise DownloadError("检测到 PAC 自动代理；无法安全执行")
            response = httpx.get(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "PyUpdataCore"}, timeout=self.timeout_seconds, follow_redirects=False, proxy=decision.proxy_url, trust_env=False)
        except httpx.HTTPError as exc:
            raise DownloadError("GitHub Release API 网络请求失败") from exc
        if response.status_code == 404: raise DownloadError("未找到 GitHub Release")
        if response.status_code in {403, 429}: raise DownloadError("GitHub API 限流或拒绝访问")
        if response.status_code >= 500: raise DownloadError(f"GitHub API 服务错误（HTTP {response.status_code}）")
        if response.status_code != 200: raise DownloadError(f"GitHub API 返回 HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict) or (self.settings.channel == "stable" and data.get("prerelease")):
            raise DownloadError("没有符合更新通道的 GitHub Release")
        assets = data.get("assets", [])
        if not isinstance(assets, list): raise DownloadError("GitHub Release assets 格式无效")
        matched = [asset for asset in assets if isinstance(asset, dict) and fnmatch.fnmatchcase(str(asset.get("name", "")), self.settings.asset_pattern)]
        if len(matched) != 1: raise DownloadError("安装资产匹配数量必须恰好为一个")
        asset = matched[0]
        sha_assets = [item for item in assets if isinstance(item, dict) and fnmatch.fnmatchcase(str(item.get("name", "")), self.settings.sha256_asset_pattern)]
        if len(sha_assets) != 1: raise DownloadError("GitHub Release 缺少唯一的可信 SHA-256 sidecar 资产")
        sha_url = sha_assets[0].get("browser_download_url")
        if not isinstance(sha_url, str) or not sha_url.startswith("https://github.com/"): raise DownloadError("SHA-256 sidecar 地址无效")
        try:
            sidecar = httpx.get(sha_url, timeout=self.timeout_seconds, follow_redirects=True, proxy=decision.proxy_url, trust_env=False).text.strip()
        except httpx.HTTPError as exc:
            raise DownloadError("无法下载 SHA-256 sidecar") from exc
        digest = sidecar.split()[0].lower() if sidecar else ""
        if not re.fullmatch(r"[0-9a-f]{64}", digest): raise DownloadError("SHA-256 sidecar 内容无效")
        name, download_url, size = asset.get("name"), asset.get("browser_download_url"), asset.get("size")
        if not isinstance(name, str) or not isinstance(download_url, str) or not isinstance(size, int) or size <= 0:
            raise DownloadError("GitHub 安装资产元数据无效")
        version = normalize_version(str(data.get("tag_name", "").lstrip("vV")))
        return UpdateManifest(version, self.settings.channel, bool(data.get("prerelease", False)), InstallerAsset(name, download_url, size, digest), str(data.get("published_at", "")), str(data.get("html_url", "")))
