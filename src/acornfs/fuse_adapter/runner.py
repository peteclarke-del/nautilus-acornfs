"""FUSE mount lifecycle."""

from __future__ import annotations

import logging
from pathlib import Path

import pyfuse3
import trio

from acornfs.core import ReadOnlyImage
from acornfs.errors import AcornFSError
from acornfs.fuse_adapter.operations import ReadOnlyOperations


def _contains_keyboard_interrupt(error: BaseException) -> bool:
    if isinstance(error, KeyboardInterrupt):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_keyboard_interrupt(child) for child in error.exceptions)
    return False


def mount_read_only(image_path: str | Path, mountpoint: str | Path, *, debug: bool = False) -> None:
    """Mount one image in the foreground until interrupted or unmounted."""

    target = Path(mountpoint).expanduser().resolve()
    if not target.is_dir():
        raise AcornFSError(f"Mountpoint does not exist or is not a directory: {target}")
    try:
        if any(target.iterdir()):
            raise AcornFSError(f"Mountpoint must be empty: {target}")
    except OSError as exc:
        raise AcornFSError(f"Cannot inspect mountpoint {target}: {exc}") from exc

    with ReadOnlyImage.open(image_path) as image:
        operations = ReadOnlyOperations(image)
        options = set(pyfuse3.default_options)
        options.update(
            {
                "ro",
                "nodev",
                "nosuid",
                "noexec",
                "subtype=acornfs",
                f"fsname={image.pair.dat_path.name}",
            }
        )
        if debug:
            logging.basicConfig(level=logging.DEBUG)
            options.add("debug")
        pyfuse3.init(operations, str(target), options)
        try:
            trio.run(pyfuse3.main)
        except BaseException as exc:
            if _contains_keyboard_interrupt(exc):
                pyfuse3.close()
            else:
                pyfuse3.close(unmount=False)
                raise
        else:
            pyfuse3.close()
