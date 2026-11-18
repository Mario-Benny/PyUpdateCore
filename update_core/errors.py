class UpdaterError(Exception):
    """Base class for safe, user-presentable updater errors."""


class ConfigError(UpdaterError):
    pass


class VersionError(UpdaterError):
    pass


class ManifestError(UpdaterError):
    pass


class DownloadError(UpdaterError):
    pass


class VerificationError(UpdaterError):
    pass


class LockError(UpdaterError):
    pass


class RangeUnsupportedError(DownloadError):
    """The server cannot safely satisfy byte-range requests."""


class CancelledError(DownloadError):
    pass
