"""Nautilus 4 context-menu integration for AcornFS."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import gi

from acornfs.core import read_image_properties
from acornfs.errors import AcornFSError
from acornfs.mounts import is_mounted, mount_for_image
from acornfs.recovery import pending_recovery
from acornfs_nautilus.logic import (
    image_property_rows,
    is_supported_image,
    mounted_file_property_rows,
)

gi.require_version("Nautilus", "4.0")
from gi.repository import Gio, GObject, Nautilus  # noqa: E402

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

    @staticmethod
    def _support_menu(items: list[Any]) -> Any:
        submenu = Nautilus.Menu()
        for item in items:
            submenu.append_item(item)
        parent = Nautilus.MenuItem(
            name="AcornFS::Support",
            label="Acorn FS Support",
            tip="Open AcornFS image and filesystem actions",
            icon="drive-harddisk-symbolic",
        )
        parent.set_submenu(submenu)
        return parent

    def _mount_read_only(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-mount", str(image_path))

    def _mount_read_write(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-mount", "--read-write", str(image_path))

    def _unmount(self, _menu: Any, mountpoint: Path) -> None:
        _launch("desktop-unmount", str(mountpoint))

    def _recover(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-recover", str(image_path))

    def _validate(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-validate", str(image_path))

    def _validate_item(self, path: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::Validate",
            label="Validate image",
            tip=f"Check {path.name} without modifying it",
            icon="emblem-default-symbolic",
        )
        item.connect("activate", self._validate, path)
        return item

    def _read_only_item(self, path: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::MountReadOnly",
            label="Open read-only",
            tip=f"Mount {path.name} without allowing changes",
            icon="changes-prevent-symbolic",
        )
        item.connect("activate", self._mount_read_only, path)
        return item

    def _unmount_item(self, mountpoint: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::Unmount",
            label="Unmount",
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
            return [self._support_menu([self._unmount_item(path)])] if is_mounted(path) else []
        if not is_supported_image(path):
            return []
        try:
            mounted = mount_for_image(path)
        except AcornFSError:
            return []
        if mounted is not None:
            return [self._support_menu([self._unmount_item(Path(mounted.mountpoint))])]
        try:
            recovery = pending_recovery(path)
        except AcornFSError:
            recovery = None
        if recovery is not None:
            recovery_item = Nautilus.MenuItem(
                name="AcornFS::Recover",
                label="Resolve interrupted Acorn write…",
                tip="Restore the pre-write checkpoint or keep the current image",
                icon="document-revert-symbolic",
            )
            recovery_item.connect("activate", self._recover, path)
            return [
                self._support_menu(
                    [recovery_item, self._read_only_item(path), self._validate_item(path)]
                )
            ]
        writable_item = Nautilus.MenuItem(
            name="AcornFS::MountReadWrite",
            label="Open read-write",
            tip=f"Mount {path.name} read-write with a recovery checkpoint",
            icon="drive-harddisk-symbolic",
        )
        writable_item.connect("activate", self._mount_read_write, path)
        return [
            self._support_menu(
                [self._read_only_item(path), writable_item, self._validate_item(path)]
            )
        ]

    def get_background_items(self, current_folder: Any) -> list[Any]:
        path = _local_path(current_folder)
        if path is None or not is_mounted(path):
            return []
        return [self._support_menu([self._unmount_item(path)])]


class AcornFSPropertiesModelProvider(GObject.GObject, Nautilus.PropertiesModelProvider):
    """Show image compatibility and mounted-entry Acorn metadata."""

    @staticmethod
    def _model(title: str, rows: tuple[tuple[str, str], ...]) -> Any:
        items = Gio.ListStore.new(item_type=Nautilus.PropertiesItem)
        for name, value in rows:
            items.append(Nautilus.PropertiesItem(name=name, value=value))
        return Nautilus.PropertiesModel(title=title, model=items)

    def get_models(self, files: list[Any]) -> list[Any]:
        if len(files) != 1:
            return []
        path = _local_path(files[0])
        if path is None:
            return []
        if is_supported_image(path):
            try:
                properties = read_image_properties(path)
                mount_state = "Mounted" if mount_for_image(path) is not None else "Not mounted"
                rows = (*image_property_rows(properties), ("Mount state", mount_state))
            except AcornFSError as exc:
                rows = (("Status", f"Unavailable: {exc}"),)
            return [self._model("Acorn disk image", rows)]
        rows = mounted_file_property_rows(path)
        return [self._model("Acorn metadata", rows)] if rows else []
