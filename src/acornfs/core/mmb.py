"""Read-only support for standard 511-slot BBC Micro MMB containers."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import oaknut.codecs  # noqa: F401 - register the Acorn character codec
from oaknut.file import AcornMeta
from oaknut.filesystem import Entry, create_filesystem, identify
from oaknut.filesystem.reader import ImageReader, reader_for

MMB_HEADER_BYTES = 8 * 1024
MMB_SLOT_BYTES = 200 * 1024
MMB_SLOT_COUNT = 511
MMB_ENTRY_BYTES = 16
MMB_STANDARD_BYTES = MMB_HEADER_BYTES + MMB_SLOT_COUNT * MMB_SLOT_BYTES

_STATUS_LOCKED = 0x00
_STATUS_READ_WRITE = 0x0F
_STATUS_UNFORMATTED = 0xF0
_STATUS_INVALID = 0xFF
_KNOWN_STATUSES = {
    _STATUS_LOCKED,
    _STATUS_READ_WRITE,
    _STATUS_UNFORMATTED,
    _STATUS_INVALID,
}


class MMBFormatError(ValueError):
    """The container does not have a safe standard MMB structure."""


class ExtendedMMBError(MMBFormatError):
    """The container uses the separately laid-out extended MMB format."""


@dataclass(frozen=True, slots=True)
class MMBSlot:
    """One formatted SSD slot described by the MMB catalogue."""

    index: int
    label: str
    status: int
    offset: int

    @property
    def display_name(self) -> str:
        return f"{self.index:03d} - {self.label}" if self.label else f"{self.index:03d}"


@dataclass(frozen=True, slots=True)
class MMBLayout:
    """Validated standard-container header and its visible slots."""

    slots: tuple[MMBSlot, ...]
    boot_slots: tuple[int, int, int, int]
    total_slots: int = MMB_SLOT_COUNT

    @property
    def formatted_slots(self) -> int:
        return len(self.slots)


def read_mmb_layout(path: str | Path) -> MMBLayout:
    """Parse a standard MMB catalogue without reading its SSD payloads."""

    image_path = Path(path)
    size = image_path.stat().st_size
    with image_path.open("rb") as handle:
        header = handle.read(MMB_HEADER_BYTES)
    if len(header) != MMB_HEADER_BYTES:
        raise MMBFormatError("MMB catalogue is shorter than 8192 bytes")

    extension_marker = header[8]
    extension_count = extension_marker & 0x0F if extension_marker & 0xF0 == 0xA0 else 0
    if extension_count:
        declared_size = (extension_count + 1) * MMB_STANDARD_BYTES
        if size == declared_size:
            raise ExtendedMMBError(
                "Extended MMB containers are not yet supported; "
                "split it into standard extents first."
            )
        raise MMBFormatError(
            f"MMB header declares {extension_count + 1} extents "
            f"({declared_size} bytes); found {size}."
        )
    if size != MMB_STANDARD_BYTES:
        raise MMBFormatError(
            f"Standard MMB length must be {MMB_STANDARD_BYTES} bytes; found {size}."
        )

    boot_slots = (
        header[0] | (header[4] << 8),
        header[1] | (header[5] << 8),
        header[2] | (header[6] << 8),
        header[3] | (header[7] << 8),
    )
    if any(slot >= MMB_SLOT_COUNT for slot in boot_slots):
        raise MMBFormatError("MMB boot configuration refers outside its 511 slots")

    slots: list[MMBSlot] = []
    for index in range(MMB_SLOT_COUNT):
        entry_offset = (index + 1) * MMB_ENTRY_BYTES
        entry = header[entry_offset : entry_offset + MMB_ENTRY_BYTES]
        status = entry[15]
        if status not in _KNOWN_STATUSES:
            raise MMBFormatError(f"MMB slot {index} has unknown catalogue status 0x{status:02X}")
        if status not in {_STATUS_LOCKED, _STATUS_READ_WRITE}:
            continue
        label = entry[:12].decode("acorn").rstrip(" \x00")
        slots.append(
            MMBSlot(
                index=index,
                label=label,
                status=status,
                offset=MMB_HEADER_BYTES + index * MMB_SLOT_BYTES,
            )
        )
    return MMBLayout(slots=tuple(slots), boot_slots=boot_slots)


def detect_mmb(path: str | Path) -> MMBLayout | None:
    """Return a standard MMB layout when structural evidence is sufficient."""

    try:
        layout = read_mmb_layout(path)
        require_mmb_content_evidence(path, layout)
        return layout
    except (MMBFormatError, OSError):
        return None


def require_mmb_content_evidence(path: str | Path, layout: MMBLayout) -> None:
    """Require a marked-formatted container to contain at least one DFS payload."""

    if not layout.slots:
        return
    first = layout.slots[0]
    reader = reader_for(path)
    try:
        candidates = identify(reader.window(first.offset, MMB_SLOT_BYTES))
    finally:
        reader.close()
    if not any(
        candidate.filesystem in {"acorn-dfs", "watford-dfs"} and candidate.geometry is not None
        for candidate in candidates
    ):
        raise MMBFormatError(
            f"MMB slot {first.index} is marked formatted but does not contain recognisable DFS"
        )


class MMBMount:
    """Present formatted MMB slots as directories backed by Oaknut DFS mounts."""

    def __init__(self, reader: ImageReader, layout: MMBLayout, *, mount_cache_slots: int = 8):
        if mount_cache_slots < 1:
            raise ValueError("MMB mount cache must retain at least one slot")
        self._reader = reader
        self.layout = layout
        self._slots = {slot.index: slot for slot in layout.slots}
        self._cache_limit = mount_cache_slots
        self._mounts: OrderedDict[int, Any] = OrderedDict()

    def close(self) -> None:
        while self._mounts:
            _index, mount = self._mounts.popitem(last=False)
            close = getattr(mount, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

    def path_root(self) -> str:
        return ""

    @staticmethod
    def _virtual_path(index: int, inner: str) -> str:
        return f"@{index}" if not inner else f"@{index}:{inner}"

    def _split(self, path: str) -> tuple[MMBSlot, str]:
        if not path.startswith("@"):
            raise FileNotFoundError(path)
        index_text, separator, inner = path[1:].partition(":")
        if not index_text.isdecimal():
            raise FileNotFoundError(path)
        slot = self._slots.get(int(index_text))
        if slot is None or (not separator and inner):
            raise FileNotFoundError(path)
        return slot, inner

    def _slot_mount(self, slot: MMBSlot) -> Any:
        cached = self._mounts.pop(slot.index, None)
        if cached is not None:
            self._mounts[slot.index] = cached
            return cached
        region = self._reader.window(slot.offset, MMB_SLOT_BYTES)
        candidates = identify(region)
        candidate = next(
            (
                item
                for item in candidates
                if item.filesystem in {"acorn-dfs", "watford-dfs"} and item.geometry is not None
            ),
            None,
        )
        if candidate is None:
            raise MMBFormatError(f"MMB slot {slot.index} is marked formatted but is not valid DFS")
        mount = create_filesystem(candidate.filesystem).open(region, candidate.geometry)
        self._mounts[slot.index] = mount
        while len(self._mounts) > self._cache_limit:
            _old_index, old_mount = self._mounts.popitem(last=False)
            close = getattr(old_mount, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()
        return mount

    def stat(self, path: str) -> Entry:
        if path == "":
            return Entry(name="", is_dir=True, path="")
        slot, inner = self._split(path)
        if not inner:
            return Entry(name=slot.display_name, is_dir=True, path=f"@{slot.index}")
        entry = self._slot_mount(slot).stat(inner)
        return Entry(
            name=entry.name,
            is_dir=entry.is_dir,
            length=entry.length,
            path=self._virtual_path(slot.index, entry.path),
        )

    def join(self, parent: str, name: str) -> str:
        if parent == "":
            match = next((slot for slot in self.layout.slots if slot.display_name == name), None)
            if match is None:
                raise FileNotFoundError(name)
            return f"@{match.index}"
        slot, inner = self._split(parent)
        joined = self._slot_mount(slot).join(inner, name)
        return self._virtual_path(slot.index, joined)

    def iter_entries(self, path: str) -> Iterable[Entry]:
        if path == "":
            for slot in self.layout.slots:
                yield Entry(name=slot.display_name, is_dir=True, path=f"@{slot.index}")
            return
        slot, inner = self._split(path)
        for entry in self._slot_mount(slot).iter_entries(inner):
            yield Entry(
                name=entry.name,
                is_dir=entry.is_dir,
                length=entry.length,
                path=self._virtual_path(slot.index, entry.path),
            )

    def exists(self, path: str) -> bool:
        if path == "":
            return True
        try:
            slot, inner = self._split(path)
        except FileNotFoundError:
            return False
        return not inner or bool(self._slot_mount(slot).exists(inner))

    def read_bytes(self, path: str) -> bytes:
        slot, inner = self._split(path)
        return bytes(self._slot_mount(slot).read_bytes(inner))

    def acorn_meta(self, path: str) -> AcornMeta:
        slot, inner = self._split(path)
        return self._slot_mount(slot).acorn_meta(inner)

    def write_bytes(self, path: str, data: bytes) -> None:
        raise PermissionError("MMB mounts are read-only")

    def remove(self, path: str, *, force: bool = False) -> None:
        raise PermissionError("MMB mounts are read-only")

    def rename(self, old_path: str, new_path: str) -> None:
        raise PermissionError("MMB mounts are read-only")

    def set_acorn_meta(self, path: str, meta: AcornMeta) -> None:
        raise PermissionError("MMB mounts are read-only")


__all__ = [
    "ExtendedMMBError",
    "MMBFormatError",
    "MMBLayout",
    "MMBMount",
    "MMBSlot",
    "MMB_HEADER_BYTES",
    "MMB_SLOT_BYTES",
    "MMB_STANDARD_BYTES",
    "detect_mmb",
    "read_mmb_layout",
    "require_mmb_content_evidence",
]
