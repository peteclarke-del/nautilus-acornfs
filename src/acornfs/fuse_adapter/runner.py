"""FUSE mount lifecycle."""

from __future__ import annotations

import logging
from pathlib import Path

import pyfuse3
import trio

from acornfs.core import ReadOnlyImage
from acornfs.errors import AcornFSError
from acornfs.fuse_adapter.operations import ReadOnlyOperations
from acornfs.mounts import register_mount, unregister_mount


def _contains_keyboard_interrupt(error: BaseException) -> bool:
    if isinstance(error, KeyboardInterrupt):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_keyboard_interrupt(child) for child in error.exceptions)
    return False


def mount_image(
    image_path: str | Path,
    mountpoint: str | Path,
    *,
    read_write: bool = False,
    debug: bool = False,
) -> None:
    """Mount one image in the foreground until interrupted or unmounted."""

    target = Path(mountpoint).expanduser().resolve()
    if not target.is_dir():
        raise AcornFSError(f"Mountpoint does not exist or is not a directory: {target}")
    try:
        if any(target.iterdir()):
            raise AcornFSError(f"Mountpoint must be empty: {target}")
    except OSError as exc:
        raise AcornFSError(f"Cannot inspect mountpoint {target}: {exc}") from exc

    registered = False
    try:
        with ReadOnlyImage.open(image_path, writable=read_write) as image:
            operations = ReadOnlyOperations(image)
            options = set(pyfuse3.default_options)
            options.update(
                {
                    "nodev",
                    "nosuid",
                    "noexec",
                    "auto_unmount",
                    "subtype=acornfs",
                    f"fsname={image.pair.dat_path.name}",
                }
            )
            if not read_write:
                options.add("ro")
            if debug:
                logging.basicConfig(level=logging.DEBUG)
                options.add("debug")
            # Publish identity before init: the kernel mount may become visible
            # while libfuse is still completing initialisation. active_mounts()
            # remains kernel-gated, so this cannot advertise a mount early.
            register_mount(image.pair.dat_path, target, read_write=read_write)
            registered = True
            pyfuse3.init(operations, str(target), options)
            try:
                trio.run(pyfuse3.main)
            except BaseException as exc:
                if not _contains_keyboard_interrupt(exc):
                    pyfuse3.close(unmount=False)
                    raise
            try:
                operations.flush_pending()
            finally:
                pyfuse3.close()
    finally:
        if registered:
            unregister_mount(target)


def mount_read_only(image_path: str | Path, mountpoint: str | Path, *, debug: bool = False) -> None:
    """Compatibility wrapper for an explicitly read-only mount."""

    mount_image(image_path, mountpoint, debug=debug)
