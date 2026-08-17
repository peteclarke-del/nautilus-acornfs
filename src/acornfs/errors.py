"""User-facing AcornFS errors."""


class AcornFSError(Exception):
    """Base class for errors safe to display to a user."""


class OperationCancelled(AcornFSError):
    """A cooperative operation stopped at a persistent-state-safe boundary."""


class PairDiscoveryError(AcornFSError):
    """A BeebSCSI DAT/DSC pair cannot be identified safely."""


class DescriptorError(AcornFSError):
    """A BeebSCSI DSC descriptor is invalid or unsupported."""


class FilenameTooLongError(ValueError):
    """An image entry name exceeds the filesystem's encoded byte limit."""


class UnsupportedImageError(AcornFSError):
    """An image cannot be mapped to a safely supported mount profile."""
