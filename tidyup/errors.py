"""Exception hierarchy. Messages are plain English and shown to the user without a traceback."""
from __future__ import annotations


class TidyupError(Exception):
    """Base class; the message is shown to the user."""


class ConfigError(TidyupError):
    pass


class UnsafePathError(TidyupError):
    """Protected / hidden / excluded / inside an atomic folder."""


class CloudOnlyError(TidyupError):
    """Attempted content read or cross-volume copy of an online-only placeholder."""


class FileLockedError(TidyupError):
    """File is in use or permission was denied."""


class VerifyError(TidyupError):
    """SHA-256 mismatch after copy."""


class InsufficientSpaceError(TidyupError):
    pass


class ManifestError(TidyupError):
    pass


class NotSupportedError(TidyupError):
    """E.g. a Windows-only operation on a non-Windows system."""
