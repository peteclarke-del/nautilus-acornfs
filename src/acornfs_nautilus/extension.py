"""Nautilus 4 context-menu integration for AcornFS."""

from __future__ import annotations

import os
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any

import gi

from acornfs.core import read_image_properties
from acornfs.errors import AcornFSError
from acornfs.file_forge import file_forge_available
from acornfs.greaseweazle import physical_write_available
from acornfs.i18n import _
from acornfs.mounts import is_mounted, mount_for_image
from acornfs.recovery import pending_recovery
from acornfs_nautilus.logic import (
    image_capabilities,
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
            label=_("Acorn FS Support"),
            tip=_("Open AcornFS image and filesystem actions"),
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

    def _repair(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-repair", str(image_path))

    def _open_file_forge(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-open-file-forge", str(image_path))

    def _write_physical_floppy(self, _menu: Any, image_path: Path) -> None:
        _launch("desktop-write-floppy", str(image_path))

    def _create(self, _menu: Any, directory: Path) -> None:
        _launch("desktop-create", str(directory))

    def _configure_mount_location(self, _menu: Any) -> None:
        _launch("desktop-configure-mount-location")

    def _configuration_item(self) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::ConfigureMountLocation",
            label=_("Mount location…"),
            tip=_("Choose where future AcornFS desktop mounts appear"),
            icon="preferences-system-symbolic",
        )
        item.connect("activate", self._configure_mount_location)
        return item

    def _create_item(self, directory: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::CreateBeebSCSI",
            label=_("Create BeebSCSI image…"),
            tip=_("Create an empty ADFS DAT/DSC pair in {directory}").format(
                directory=directory.name
            ),
            icon="document-new-symbolic",
        )
        item.connect("activate", self._create, directory)
        return item

    @staticmethod
    def _can_create_in(directory: Path) -> bool:
        return directory.is_dir() and os.access(directory, os.W_OK | os.X_OK)

    def _validate_item(self, path: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::Validate",
            label=_("Validate image"),
            tip=_("Check {image} without modifying it").format(image=path.name),
            icon="emblem-default-symbolic",
        )
        item.connect("activate", self._validate, path)
        return item

    def _read_only_item(self, path: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::MountReadOnly",
            label=_("Open read-only"),
            tip=_("Mount {image} without allowing changes").format(image=path.name),
            icon="changes-prevent-symbolic",
        )
        item.connect("activate", self._mount_read_only, path)
        return item

    def _repair_item(self, path: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::Repair",
            label=_("Repair image…"),
            tip=_("Review eligible low-risk repairs for {image}").format(image=path.name),
            icon="document-edit-symbolic",
        )
        item.connect("activate", self._repair, path)
        return item

    def _unmount_item(self, mountpoint: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::Unmount",
            label=_("Unmount"),
            tip=_("Unmount {mountpoint}").format(mountpoint=mountpoint.name),
            icon="media-eject-symbolic",
        )
        item.connect("activate", self._unmount, mountpoint)
        return item

    def _file_forge_item(self, path: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::OpenFileForge",
            label=_("Open in Acorn File Forge…"),
            tip=_("Open {image} in Acorn File Forge").format(image=path.name),
            icon="document-open-symbolic",
        )
        item.connect("activate", self._open_file_forge, path)
        return item

    def _write_floppy_item(self, path: Path) -> Any:
        item = Nautilus.MenuItem(
            name="AcornFS::WritePhysicalFloppy",
            label=_("Write to physical floppy…"),
            tip=_("Write and verify {image} using Greaseweazle").format(image=path.name),
            icon="media-floppy-symbolic",
        )
        item.connect("activate", self._write_physical_floppy, path)
        return item

    def get_file_items(self, files: list[Any]) -> list[Any]:
        if len(files) != 1:
            return []
        file_info = files[0]
        path = _local_path(file_info)
        if path is None:
            return []
        if file_info.is_directory():
            if is_mounted(path):
                return [self._support_menu([self._unmount_item(path), self._configuration_item()])]
            if self._can_create_in(path):
                return [self._support_menu([self._create_item(path), self._configuration_item()])]
            return []
        offer_physical_write = physical_write_available(path)
        capabilities = image_capabilities(path)
        if capabilities is None:
            if offer_physical_write:
                return [self._support_menu([self._write_floppy_item(path)])]
            return []
        offer_file_forge = capabilities.file_forge and file_forge_available()
        try:
            mounted = mount_for_image(path)
        except AcornFSError:
            return []
        if mounted is not None:
            mounted_items = [self._unmount_item(Path(mounted.mountpoint))]
            if offer_file_forge:
                mounted_items.append(self._file_forge_item(path))
            if offer_physical_write:
                mounted_items.append(self._write_floppy_item(path))
            mounted_items.append(self._configuration_item())
            return [self._support_menu(mounted_items)]
        recovery = None
        if capabilities.recover:
            with suppress(AcornFSError):
                recovery = pending_recovery(path)
        if recovery is not None:
            recovery_item = Nautilus.MenuItem(
                name="AcornFS::Recover",
                label=_("Resolve interrupted read-write mount…"),
                tip=_("Restore the pre-mount checkpoint or keep the current image"),
                icon="document-revert-symbolic",
            )
            recovery_item.connect("activate", self._recover, path)
            recovery_items = [recovery_item]
            if capabilities.mount_read_only:
                recovery_items.append(self._read_only_item(path))
            if capabilities.validate:
                recovery_items.append(self._validate_item(path))
            if offer_file_forge:
                recovery_items.append(self._file_forge_item(path))
            if offer_physical_write:
                recovery_items.append(self._write_floppy_item(path))
            recovery_items.append(self._configuration_item())
            return [self._support_menu(recovery_items)]
        image_items: list[Any] = []
        if capabilities.mount_read_only:
            image_items.append(self._read_only_item(path))
        if capabilities.mount_read_write:
            writable_item = Nautilus.MenuItem(
                name="AcornFS::MountReadWrite",
                label=_("Open read-write"),
                tip=_("Mount {image} read-write with a recovery checkpoint").format(
                    image=path.name
                ),
                icon="drive-harddisk-symbolic",
            )
            writable_item.connect("activate", self._mount_read_write, path)
            image_items.append(writable_item)
        if capabilities.validate:
            image_items.append(self._validate_item(path))
        if capabilities.repair:
            image_items.append(self._repair_item(path))
        if offer_physical_write:
            image_items.append(self._write_floppy_item(path))
        if offer_file_forge:
            image_items.append(self._file_forge_item(path))
        image_items.append(self._configuration_item())
        return [self._support_menu(image_items)]

    def get_background_items(self, current_folder: Any) -> list[Any]:
        path = _local_path(current_folder)
        if path is None:
            return []
        if is_mounted(path):
            return [self._support_menu([self._unmount_item(path), self._configuration_item()])]
        if self._can_create_in(path):
            return [self._support_menu([self._create_item(path), self._configuration_item()])]
        return []


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
                mount_state = (
                    _("Mounted") if mount_for_image(path) is not None else _("Not mounted")
                )
                rows = (*image_property_rows(properties), (_("Mount state"), mount_state))
            except AcornFSError as exc:
                rows = ((_("Status"), _("Unavailable: {error}").format(error=exc)),)
            return [self._model(_("Acorn disk image"), rows)]
        rows = mounted_file_property_rows(path)
        return [self._model(_("Acorn metadata"), rows)] if rows else []
