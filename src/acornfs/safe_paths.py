"""Race-resistant creation of AcornFS-owned private directories."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path

from acornfs.errors import AcornFSError
from acornfs.i18n import _


def ensure_private_directory(path: Path, *, anchor: Path) -> Path:
    """Create descendants below a trusted anchor without following symlinks."""

    try:
        parts = path.relative_to(anchor).parts
    except ValueError as exc:
        raise AcornFSError(_("A private AcornFS directory escaped its trusted root.")) from exc
    if not parts:
        raise AcornFSError(_("A private AcornFS directory must be below its trusted root."))
    anchor.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(anchor, flags)
    except OSError as exc:
        raise AcornFSError(_("The trusted AcornFS directory root is unsafe.")) from exc
    current = anchor
    try:
        for part in parts:
            if part in {"", ".", ".."} or "/" in part:
                raise AcornFSError(_("A private AcornFS directory name is unsafe."))
            with suppress(FileExistsError):
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise AcornFSError(
                    _("Refusing an unsafe private AcornFS directory: {name}").format(name=part)
                ) from exc
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
                os.close(child)
                raise AcornFSError(
                    _("Refusing an unsafe private AcornFS directory: {name}").format(name=part)
                )
            os.close(descriptor)
            descriptor = child
            os.fchmod(descriptor, 0o700)
            current /= part
    finally:
        os.close(descriptor)
    return current


__all__ = ["ensure_private_directory"]
