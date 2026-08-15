"""Nautilus 4 context-menu integration for AcornFS."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import gi

from acornfs.desktop import mountpoint_for_image
from acornfs.errors import AcornFSError
from acornfs.mounts import is_mounted
from acornfs_nautilus.logic import is_supported_image

gi.require_version("Nautilus", "4.0")
from gi.repository import GObject, Nautilus  # noqa: E402

_COMMAND = ["acornfs"]


def configure_command(command: list[str]) -> None:
    global _COMMAND
    _COMMAND = command


def _local_path(file_info: Any) -> Path | None:
    if file_info.get_uri_scheme() != "file":
        return None
    location = file_info.get_location()
    path = location.get_path() if location is not None else None
    return Path(path) if path else None


def _launch(*arguments: str) -> None:
    subprocess.Popen(
        [*_COMMAND, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class AcornFSMenuProvider(GObject.GObject, Nautilus.MenuProvider):
    """Add mount lifecycle actions for paired BeebSCSI images."""

    def _mount(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-mount", str(image_path))

    def _unmount(self, _menu: Any, mountpoint: Path) -> None:
        _launch("desktop-unmount", str(mountpoint))

    def _open(self, _menu: Any, mountpoint: Path) -> None:
        _launch("desktop-open", str(mountpoint))

    def _unmount_item(self, mountpoint: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::Unmount",
            label="Unmount Acorn image",
            tip=f"Unmount {mountpoint.name}",
            icon="media-eject-symbolic",
        )
        item.connect("activate", self._unmount, mountpoint)
        return item

    def get_file_items(self, files: list[Any]) -> list[Any]:
        if len(files) != 1:
            return []
        file_info = files[0]
        path = _local_path(file_info)
        if path is None:
            return []
        if file_info.is_directory():
            return [self._unmount_item(path)] if is_mounted(path) else []
        if not is_supported_image(path):
            return []
        try:
            mountpoint = mountpoint_for_image(path)
        except AcornFSError:
            return []
        if is_mounted(mountpoint):
            open_item = Nautilus.MenuItem(
                name="AcornFS::Open",
                label="Open mounted Acorn image",
                tip=f"Open {mountpoint.name}",
                icon="folder-open-symbolic",
            )
            open_item.connect("activate", self._open, mountpoint)
            return [open_item, self._unmount_item(mountpoint)]
        mount_item = Nautilus.MenuItem(
            name="AcornFS::Mount",
            label="Mount Acorn image",
            tip=f"Mount {path.name} read-only",
            icon="drive-harddisk-symbolic",
        )
        mount_item.connect("activate", self._mount, path)
        return [mount_item]

    def get_background_items(self, current_folder: Any) -> list[Any]:
        path = _local_path(current_folder)
        if path is None or not is_mounted(path):
            return []
        return [self._unmount_item(path)]
