"""Locked, identity-checked access to standalone disk images."""

from __future__ import annotations

import errno
import fcntl
import mmap
import os
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any, BinaryIO, cast

from oaknut.filesystem.reader import ImageReader

from acornfs.errors import AcornFSError
from acornfs.i18n import _


def open_locked_handle(selected: str | Path, *, writable: bool) -> BinaryIO:
    """Open, lock and identity-check one regular image without mapping it."""

    path = Path(selected).expanduser().resolve(strict=True)
    mode = "r+b" if writable else "rb"
    lock_mode = fcntl.LOCK_EX if writable else fcntl.LOCK_SH
    handle: Any | None = None
    try:
        handle = path.open(mode)
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
        return cast(BinaryIO, handle)
    except BlockingIOError as exc:
        with suppress(Exception):
            if handle is not None:
                handle.close()
        raise AcornFSError(_("The image is mounted or open in another AcornFS process.")) from exc
    except OSError as exc:
        with suppress(Exception):
            if handle is not None:
                handle.close()
        if writable and exc.errno in {errno.EACCES, errno.EROFS, errno.EPERM}:
            raise AcornFSError(
                _(
                    "The image is on read-only storage or is not writable by this user. "
                    "Open it read-only, or copy it to writable local storage."
                )
            ) from exc
        raise
    except BaseException:
        with suppress(Exception):
            if handle is not None:
                handle.close()
        raise


def open_locked_image(
    selected: str | Path,
    *,
    writable: bool,
    companion: str | Path | None = None,
) -> tuple[ImageReader, tuple[Any, ...]]:
    """Open one regular image once, lock it and map the locked inode."""

    path = Path(selected).expanduser().resolve(strict=True)
    stack = ExitStack()
    try:
        handle = open_locked_handle(path, writable=writable)
        stack.callback(handle.close)
        access = mmap.ACCESS_WRITE if writable else mmap.ACCESS_COPY
        mapping = mmap.mmap(handle.fileno(), 0, access=access)
        stack.callback(mapping.close)
        reader = ImageReader(mapping, suffix=path.suffix, writable=writable)
        closeables: tuple[Any, ...] = (mapping, handle)
        if companion is not None:
            companion_path = Path(companion).expanduser().resolve(strict=True)
            companion_handle = open_locked_handle(companion_path, writable=writable)
            stack.callback(companion_handle.close)
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


__all__ = ["open_locked_handle", "open_locked_image"]
