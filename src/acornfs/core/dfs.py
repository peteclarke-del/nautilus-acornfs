"""Namespace and mutation adapter for double-sided DFS images."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from typing import Any

from oaknut.file import AcornMeta
from oaknut.filesystem import Entry


class DoubleSidedDFSMount:
    """Expose DFS drives 0 and 2 as directories in one mounted namespace.

    Each DFS side remains an independent flat catalogue.  The adapter only
    adds a virtual drive level; catalogue prefixes (``$`` and ``A``-``Z``)
    remain the virtual directories supplied by Oaknut.
    """

    _DESIGNATIONS = ("0", "2")

    def __init__(self, side_zero: Any, side_two: Any) -> None:
        self._mounts = {"0": side_zero, "2": side_two}

    def close(self) -> None:
        for mount in self._mounts.values():
            close = getattr(mount, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

    def path_root(self) -> str:
        return ""

    @staticmethod
    def _virtual_path(drive: str, path: str) -> str:
        return f":{drive}" if not path else f":{drive}:{path}"

    def _split(self, path: str) -> tuple[str, str]:
        if not path.startswith(":"):
            raise FileNotFoundError(path)
        drive, separator, inner = path[1:].partition(":")
        if drive not in self._mounts or (not separator and inner):
            raise FileNotFoundError(path)
        return drive, inner

    def stat(self, path: str) -> Entry:
        if path == "":
            return Entry(name="", is_dir=True, path="")
        drive, inner = self._split(path)
        if not inner:
            return Entry(name=drive, is_dir=True, path=f":{drive}")
        entry = self._mounts[drive].stat(inner)
        return Entry(
            name=entry.name,
            is_dir=entry.is_dir,
            length=entry.length,
            path=self._virtual_path(drive, entry.path),
        )

    def join(self, parent: str, name: str) -> str:
        if parent == "":
            if name not in self._mounts:
                raise FileNotFoundError(name)
            return f":{name}"
        drive, inner = self._split(parent)
        return self._virtual_path(drive, self._mounts[drive].join(inner, name))

    def iter_entries(self, path: str) -> Iterable[Entry]:
        if path == "":
            for drive in self._DESIGNATIONS:
                yield Entry(name=drive, is_dir=True, path=f":{drive}")
            return
        drive, inner = self._split(path)
        for entry in self._mounts[drive].iter_entries(inner):
            yield Entry(
                name=entry.name,
                is_dir=entry.is_dir,
                length=entry.length,
                path=self._virtual_path(drive, entry.path),
            )

    def exists(self, path: str) -> bool:
        if path == "":
            return True
        try:
            drive, inner = self._split(path)
        except FileNotFoundError:
            return False
        return not inner or bool(self._mounts[drive].exists(inner))

    def read_bytes(self, path: str) -> bytes:
        drive, inner = self._split(path)
        return bytes(self._mounts[drive].read_bytes(inner))

    def acorn_meta(self, path: str) -> AcornMeta:
        drive, inner = self._split(path)
        return self._mounts[drive].acorn_meta(inner)

    def free_bytes(self) -> int:
        return sum(int(mount.free_bytes()) for mount in self._mounts.values())

    def size_bytes(self) -> int:
        return sum(int(mount.size_bytes()) for mount in self._mounts.values())

    def available_bytes(self, path: str) -> int:
        """Return free bytes on the independently allocated target side."""

        drive, _inner = self._split(path)
        return int(self._mounts[drive].free_bytes())

    def write_bytes(self, path: str, data: bytes) -> None:
        drive, inner = self._split(path)
        self._mounts[drive].write_bytes(inner, data)

    def remove(self, path: str, *, force: bool = False) -> None:
        drive, inner = self._split(path)
        if not inner:
            raise PermissionError("DFS drive directories cannot be removed")
        self._mounts[drive].remove(inner, force=force)

    def rename(self, old_path: str, new_path: str) -> None:
        old_drive, old_inner = self._split(old_path)
        new_drive, new_inner = self._split(new_path)
        if old_drive != new_drive:
            raise OSError("DFS files cannot be renamed between disk sides")
        self._mounts[old_drive].rename(old_inner, new_inner)

    def set_acorn_meta(self, path: str, meta: AcornMeta) -> None:
        drive, inner = self._split(path)
        self._mounts[drive].set_acorn_meta(inner, meta)

    def validate(self) -> list[Any]:
        problems: list[Any] = []
        for mount in self._mounts.values():
            problems.extend(mount.validate())
        return problems


__all__ = ["DoubleSidedDFSMount"]
