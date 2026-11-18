from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstallerAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    channel: str
    mandatory: bool
    installer: InstallerAsset
    published_at: str = ""
    release_notes_url: str = ""


@dataclass(frozen=True)
class CheckResult:
    current_version: str
    available: bool
    manifest: UpdateManifest | None
    message: str
