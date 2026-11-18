from __future__ import annotations

from pathlib import Path

from .config import UpdaterConfig
from .current_version import read_current_version
from .downloader import Downloader
from .errors import ConfigError
from .manifest import parse_manifest
from .providers import GithubReleaseConfig, GithubReleaseProvider
from .models import CheckResult, UpdateManifest
from .storage import UpdateLock, merge_state
from .verification import verify_installer
from .versioning import is_newer
from .windows import launch_installer, restart_program, wait_for_process


class UpdateWorkflow:
    def __init__(self, config: UpdaterConfig, data_dir: Path, progress=None, event_callback=None) -> None:
        self.config, self.data_dir, self.progress, self.event_callback = config, data_dir, progress, event_callback
        self.downloads_dir = data_dir / "download"
        self.temporary_dir = self.downloads_dir / "download_tmp"
        self.state_path = data_dir / "data" / "state.json"

    def check(self, command_line_version: str | None = None) -> CheckResult:
        current = read_current_version(self.config, command_line_version)
        if self.config.provider == "manifest":
            downloader = Downloader(self.config, self.temporary_dir, self.state_path, self.progress, self.event_callback)
            try: payload = downloader.fetch_manifest()
            finally: downloader.close()
            manifest = parse_manifest(payload, self.config)
        elif self.config.provider == "github_release":
            if not self.config.repository or not self.config.asset_pattern:
                raise ConfigError("github_release 必须配置 repository 与 asset_pattern")
            manifest = GithubReleaseProvider(GithubReleaseConfig(self.config.repository, self.config.channel, self.config.asset_pattern, self.config.sha256_asset_pattern or "*.sha256", self.config.release_tag), self.config.timeout_seconds, self.config.proxy_mode, self.config.proxy_url).fetch()
        else:
            raise ConfigError("不支持的更新来源 Provider")
        available = is_newer(manifest.version, current)
        merge_state(self.state_path, {"last_check": {"current_version": current, "remote_version": manifest.version, "available": available}})
        return CheckResult(current, available, manifest if available else None, "发现新版本" if available else "当前已是最新版本")

    def download(self, manifest: UpdateManifest, *, threads: int | None = None) -> Path:
        if self.config.download_channel != "native":
            raise ConfigError(f"网络渠道 {self.config.download_channel} 是实验功能，当前需要 Browser Bridge 明确授权后使用")
        with UpdateLock(self.data_dir / "updater.lock"):
            downloaded = Downloader(self.config, self.temporary_dir, self.state_path, self.progress, self.event_callback).download(manifest.installer, threads=threads)
            final = self.downloads_dir / manifest.installer.name
            downloaded.replace(final)
            return final

    def install(self, installer: Path, manifest: UpdateManifest, process_id: int | None = None) -> None:
        with UpdateLock(self.data_dir / "updater.lock"):
            # The installer hand-off is asynchronous (especially through UAC).
            # Reject unsupported post-install actions *before* anything is run.
            if self.config.restart_enabled:
                raise ConfigError("restart.enabled 尚未实现安装完成同步，已在启动安装器前安全拒绝")
            if self.config.delete_after_install:
                raise ConfigError("installer.delete_after_install 尚未实现安装完成同步，已在启动安装器前安全拒绝")
            installer = installer.resolve(strict=True)
            cache = self.downloads_dir.resolve(strict=True)
            if installer.parent != cache or installer.name != manifest.installer.name:
                raise ConfigError("拒绝执行更新缓存目录外的安装器")
            verify_installer(installer, manifest.installer, require_sha256=self.config.require_sha256)
            if self.config.wait_for_main_program and process_id:
                wait_for_process(process_id)
            launch_installer(installer, self.config.installer_arguments, self.config.request_admin)
