"""Before-image transactions for protected Acorn filesystem mutations."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

SECTOR_BYTES = 256
MAP_SECTORS = 2
OLD_DISC_ID_OFFSET = 0x1FB


@dataclass(frozen=True, slots=True)
class _BeforeImage:
    offset: int
    data: bytes


class SectorTransaction:
    """Capture only metadata and live data sectors an operation may overwrite.

    Newly allocated sectors do not need a before-image: restoring the free-space
    map makes them free again. Existing file data is captured for replacement,
    because Oaknut may immediately reuse the file's old allocation.
    """

    def __init__(self, mount: Any, mapping: Any) -> None:
        self._adfs = cast(Any, mount)._adfs
        self._mapping = mapping
        self._before_images: list[_BeforeImage] = []
        self._captured: set[tuple[int, int]] = set()
        self.capture_sectors(0, MAP_SECTORS)

    def capture_sectors(self, start: int, count: int) -> None:
        if count <= 0:
            return
        key = (start, count)
        if key in self._captured:
            return
        offset = start * SECTOR_BYTES
        end = offset + count * SECTOR_BYTES
        self._before_images.append(_BeforeImage(offset, bytes(self._mapping[offset:end])))
        self._captured.add(key)

    def capture_parent(self, path: str) -> int:
        """Capture a path's parent directory and return its sector address."""

        _directory, sector = self._adfs._resolve_parent(path.split("."))
        self.capture_directory_at(int(sector))
        return int(sector)

    def capture_directory_at(self, sector: int) -> None:
        self.capture_sectors(sector, int(self._adfs._dir_format.size_in_sectors))

    def capture_file(self, path: str) -> None:
        """Capture an existing file's live allocation if it has one."""

        _parent, entry = self._adfs.path(path)._resolve()
        if entry is None or entry.is_directory:
            return
        count = (int(entry.length) + SECTOR_BYTES - 1) // SECTOR_BYTES
        self.capture_sectors(int(entry.start_sector), count)

    def capture_directory(self, path: str) -> int:
        """Capture an existing directory block and return its sector address."""

        _parent, entry = self._adfs.path(path)._resolve()
        if entry is None or not entry.is_directory:
            raise NotADirectoryError(path)
        sector = int(entry.start_sector)
        self.capture_directory_at(sector)
        return sector

    def restore(self) -> None:
        for before_image in reversed(self._before_images):
            end = before_image.offset + len(before_image.data)
            self._mapping[before_image.offset : end] = before_image.data

    def advance_disc_id(self) -> tuple[int, int]:
        """Advance the old-map cycle ID and refresh both map checksums.

        This intentionally lives beside the other pinned Oaknut-private
        integration.  The map sectors are captured by the constructor, so a
        failed logical mutation restores both the ID and its checksums.
        """

        free_space_map = self._adfs._fsm
        old_id = int(free_space_map.disc_id)
        new_id = (old_id + 1) & 0xFFFF
        data = free_space_map._data
        data[OLD_DISC_ID_OFFSET] = new_id & 0xFF
        data[OLD_DISC_ID_OFFSET + 1] = new_id >> 8
        free_space_map._recalculate_checksums()
        return old_id, new_id

    def complete(self) -> None:
        """Release transaction state after commit or verified rollback."""


class FileTransaction:
    """Whole-image before-image for formats without a sector transaction adapter.

    The snapshot is held in the session's private recovery directory. A reflink
    is used when the image and state directory share a compatible filesystem;
    otherwise a bounded copy preserves the same rollback guarantee.
    """

    def __init__(
        self,
        path: Path,
        mapping: Any,
        handle: Any,
        *,
        backup_directory: Path,
    ) -> None:
        from acornfs.recovery import _checkpoint_copy

        self._mapping = mapping
        self._backup = backup_directory / f"operation-{uuid.uuid4().hex}.bin"
        _checkpoint_copy(path, self._backup, source_handle=handle, destination_mode=0o600)

    def capture_sectors(self, _start: int, _count: int) -> None:
        pass

    def capture_parent(self, _path: str) -> int:
        return 0

    def capture_directory_at(self, _sector: int) -> None:
        pass

    def capture_file(self, _path: str) -> None:
        pass

    def capture_directory(self, _path: str) -> int:
        return 0

    def advance_disc_id(self) -> tuple[int, int]:
        return (0, 0)

    def restore(self) -> None:
        offset = 0
        with self._backup.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                self._mapping[offset : offset + len(chunk)] = chunk
                offset += len(chunk)
        if offset != len(self._mapping):
            raise OSError("transaction before-image length does not match the mounted image")

    def complete(self) -> None:
        self._backup.unlink(missing_ok=True)
        directory = os.open(self._backup.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


__all__ = ["FileTransaction", "SectorTransaction"]
