"""Locked, identity-checked access to standalone disk images."""

from __future__ import annotations

import errno
import fcntl
import mmap
import os
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from oaknut.filesystem.reader import ImageReader

from acornfs.errors import AcornFSError
from acornfs.i18n import _


def open_locked_image(
    selected: str | Path,
    *,
    writable: bool,
    companion: str | Path | None = None,
) -> tuple[ImageReader, tuple[Any, ...]]:
    """Open one regular image once, lock it and map the locked inode."""

    path = Path(selected).expanduser().resolve(strict=True)
    mode = "r+b" if writable else "rb"
    lock_mode = fcntl.LOCK_EX if writable else fcntl.LOCK_SH
    stack = ExitStack()
    try:
        handle = stack.enter_context(path.open(mode))
        fcntl.flock(handle, lock_mode | fcntl.LOCK_NB)
        opened = os.fstat(handle.fileno())
        current = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise AcornFSError(
                _("The image changed while AcornFS was opening it: {path}").format(path=path)
            )
        if writable and opened.st_nlink != 1:
            raise AcornFSError(
                _(
                    "Writable mounting refuses an image with hard links: {path}. "
                    "Copy it to a uniquely owned file first."
                ).format(path=path)
            )
        access = mmap.ACCESS_WRITE if writable else mmap.ACCESS_COPY
        mapping = mmap.mmap(handle.fileno(), 0, access=access)
        stack.callback(mapping.close)
        reader = ImageReader(mapping, suffix=path.suffix, writable=writable)
        closeables: tuple[Any, ...] = (mapping, handle)
        if companion is not None:
            companion_path = Path(companion).expanduser().resolve(strict=True)
            companion_handle = stack.enter_context(companion_path.open(mode))
            fcntl.flock(companion_handle, lock_mode | fcntl.LOCK_NB)
            companion_opened = os.fstat(companion_handle.fileno())
            companion_current = companion_path.stat(follow_symlinks=False)
            if (companion_opened.st_dev, companion_opened.st_ino) != (
                companion_current.st_dev,
                companion_current.st_ino,
            ):
                raise AcornFSError(
                    _("The companion image changed while AcornFS was opening it: {path}").format(
                        path=companion_path
                    )
                )
            if writable and companion_opened.st_nlink != 1:
                raise AcornFSError(
                    _("Writable mounting refuses a companion image with hard links.")
                )
            closeables = (mapping, handle, None, companion_handle)
    except BlockingIOError as exc:
        stack.close()
        raise AcornFSError(_("The image is mounted or open in another AcornFS process.")) from exc
    except OSError as exc:
        stack.close()
        if writable and exc.errno in {errno.EACCES, errno.EROFS, errno.EPERM}:
            raise AcornFSError(
                _(
                    "The image is on read-only storage or is not writable by this user. "
                    "Open it read-only, or copy it to writable local storage."
                )
            ) from exc
        raise
    except BaseException:
        stack.close()
        raise
    stack.pop_all()
    return reader, closeables


__all__ = ["open_locked_image"]
