import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


class _Menu:
    def __init__(self) -> None:
        self.items: list[_MenuItem] = []

    def append_item(self, item: "_MenuItem") -> None:
        self.items.append(item)


class _MenuItem:
    def __init__(self, **values: str) -> None:
        self.label = values["label"]
        self.submenu: _Menu | None = None
        self.callback: Any = None
        self.arguments: tuple[Any, ...] = ()

    def connect(self, _signal: str, callback: Any, *arguments: Any) -> None:
        self.callback = callback
        self.arguments = arguments

    def set_submenu(self, submenu: _Menu) -> None:
        self.submenu = submenu


class _FileInfo:
    def __init__(self, path: Path, *, directory: bool = False) -> None:
        self.path = path
        self.directory = directory

    def get_uri_scheme(self) -> str:
        return "file"

    def get_location(self) -> Any:
        return SimpleNamespace(get_path=lambda: str(self.path))

    def is_directory(self) -> bool:
        return self.directory


def _load_extension(monkeypatch: Any) -> Any:
    gi = ModuleType("gi")
    gi.require_version = lambda *_args: None  # type: ignore[attr-defined]
    repository = ModuleType("gi.repository")
    repository.Gio = SimpleNamespace(ListStore=object)
    repository.GObject = SimpleNamespace(GObject=type("GObject", (), {}))
    repository.Nautilus = SimpleNamespace(
        Menu=_Menu,
        MenuItem=_MenuItem,
        MenuProvider=type("MenuProvider", (), {}),
        PropertiesModelProvider=type("PropertiesModelProvider", (), {}),
    )
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    sys.modules.pop("acornfs_nautilus.extension", None)
    extension = importlib.import_module("acornfs_nautilus.extension")
    monkeypatch.setattr(extension, "physical_write_available", lambda _path: False)
    return extension


def _capabilities(**overrides: bool) -> Any:
    values = {
        "mount_read_only": True,
        "mount_read_write": True,
        "validate": True,
        "repair": True,
        "recover": True,
        "properties": True,
        "file_forge": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_image_actions_are_collapsed_under_one_support_menu(
    tmp_path: Path, monkeypatch: Any
) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(extension, "image_capabilities", lambda _path: _capabilities())
    monkeypatch.setattr(extension, "mount_for_image", lambda _path: None)
    monkeypatch.setattr(extension, "pending_recovery", lambda _path: None)

    items = extension.AcornFSMenuProvider().get_file_items([_FileInfo(tmp_path / "scsi0.dat")])

    assert len(items) == 1
    assert items[0].label == "Acorn FS Support"
    assert [item.label for item in items[0].submenu.items] == [
        "Open read-only",
        "Open read-write",
        "Validate image",
        "Repair image…",
        "Open in Acorn File Forge…",
        "Mount location…",
    ]


def test_writable_folder_offers_create_in_support_menu(tmp_path: Path, monkeypatch: Any) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(extension, "is_mounted", lambda _path: False)

    items = extension.AcornFSMenuProvider().get_background_items(_FileInfo(tmp_path))

    assert len(items) == 1
    assert items[0].label == "Acorn FS Support"
    assert [item.label for item in items[0].submenu.items] == [
        "Create BeebSCSI image…",
        "Mount location…",
    ]


def test_create_action_launches_desktop_command(tmp_path: Path, monkeypatch: Any) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(extension, "is_mounted", lambda _path: False)
    launched: list[tuple[str, ...]] = []
    monkeypatch.setattr(extension, "_launch", lambda *arguments: launched.append(arguments))
    item = (
        extension.AcornFSMenuProvider()
        .get_file_items([_FileInfo(tmp_path, directory=True)])[0]
        .submenu.items[0]
    )

    item.callback(None, *item.arguments)

    assert launched == [("desktop-create", str(tmp_path))]


def test_mount_location_action_launches_desktop_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(extension, "is_mounted", lambda _path: False)
    launched: list[tuple[str, ...]] = []
    monkeypatch.setattr(extension, "_launch", lambda *arguments: launched.append(arguments))
    item = (
        extension.AcornFSMenuProvider()
        .get_background_items(_FileInfo(tmp_path))[0]
        .submenu.items[1]
    )

    item.callback(None, *item.arguments)

    assert launched == [("desktop-configure-mount-location",)]


def test_file_forge_action_launches_desktop_handoff(tmp_path: Path, monkeypatch: Any) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(extension, "image_capabilities", lambda _path: _capabilities())
    monkeypatch.setattr(extension, "mount_for_image", lambda _path: None)
    monkeypatch.setattr(extension, "pending_recovery", lambda _path: None)
    launched: list[tuple[str, ...]] = []
    monkeypatch.setattr(extension, "_launch", lambda *arguments: launched.append(arguments))
    image = tmp_path / "scsi0.dat"
    items = extension.AcornFSMenuProvider().get_file_items([_FileInfo(image)])
    item = next(row for row in items[0].submenu.items if row.label == "Open in Acorn File Forge…")

    item.callback(None, *item.arguments)

    assert launched == [("desktop-open-file-forge", str(image))]


def test_mounted_image_keeps_file_forge_action(tmp_path: Path, monkeypatch: Any) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(extension, "image_capabilities", lambda _path: _capabilities())
    monkeypatch.setattr(
        extension,
        "mount_for_image",
        lambda _path: SimpleNamespace(mountpoint=str(tmp_path / "mounted")),
    )

    items = extension.AcornFSMenuProvider().get_file_items([_FileInfo(tmp_path / "scsi0.dat")])

    assert [item.label for item in items[0].submenu.items] == [
        "Unmount",
        "Open in Acorn File Forge…",
        "Mount location…",
    ]


def test_adfs_floppy_menu_offers_only_supported_actions(tmp_path: Path, monkeypatch: Any) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(
        extension,
        "image_capabilities",
        lambda _path: _capabilities(
            mount_read_write=False,
            validate=False,
            repair=False,
            recover=False,
            file_forge=False,
        ),
    )
    monkeypatch.setattr(extension, "mount_for_image", lambda _path: None)

    items = extension.AcornFSMenuProvider().get_file_items([_FileInfo(tmp_path / "disc.adl")])

    assert [item.label for item in items[0].submenu.items] == [
        "Open read-only",
        "Mount location…",
    ]


def test_physical_write_is_offered_only_when_greaseweazle_is_detected(
    tmp_path: Path, monkeypatch: Any
) -> None:
    extension = _load_extension(monkeypatch)
    image = tmp_path / "disc.ssd"
    monkeypatch.setattr(extension, "image_capabilities", lambda _path: None)

    assert extension.AcornFSMenuProvider().get_file_items([_FileInfo(image)]) == []

    monkeypatch.setattr(extension, "physical_write_available", lambda _path: True)
    items = extension.AcornFSMenuProvider().get_file_items([_FileInfo(image)])
    assert [item.label for item in items[0].submenu.items] == ["Write to physical floppy…"]


def test_physical_write_action_launches_desktop_workflow(tmp_path: Path, monkeypatch: Any) -> None:
    extension = _load_extension(monkeypatch)
    image = tmp_path / "disc.adf"
    monkeypatch.setattr(extension, "image_capabilities", lambda _path: None)
    monkeypatch.setattr(extension, "physical_write_available", lambda _path: True)
    launched: list[tuple[str, ...]] = []
    monkeypatch.setattr(extension, "_launch", lambda *arguments: launched.append(arguments))

    item = extension.AcornFSMenuProvider().get_file_items([_FileInfo(image)])[0].submenu.items[0]
    item.callback(None, *item.arguments)

    assert launched == [("desktop-write-floppy", str(image))]
