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

    def connect(self, *_args: Any) -> None:
        pass

    def set_submenu(self, submenu: _Menu) -> None:
        self.submenu = submenu


class _FileInfo:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_uri_scheme(self) -> str:
        return "file"

    def get_location(self) -> Any:
        return SimpleNamespace(get_path=lambda: str(self.path))

    def is_directory(self) -> bool:
        return False


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
    return importlib.import_module("acornfs_nautilus.extension")


def test_image_actions_are_collapsed_under_one_support_menu(
    tmp_path: Path, monkeypatch: Any
) -> None:
    extension = _load_extension(monkeypatch)
    monkeypatch.setattr(extension, "is_supported_image", lambda _path: True)
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
    ]
