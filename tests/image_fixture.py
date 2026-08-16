"""Generated BeebSCSI test image; no private media required."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from oaknut.filesystem import create_filesystem, geometry_from_dsc, reader_for, winchester_geometry


def _old_map_checksum(data: bytearray, start: int) -> int:
    total = 0
    carry = 0
    for offset in range(0xFE, -1, -1):
        total += data[start + offset] + carry
        if total > 0xFF:
            carry = 1
            total &= 0xFF
        else:
            carry = 0
    return total


def rewrite_old_map(dat_path: Path, mutation: Callable[[bytearray], None]) -> None:
    """Mutate generated old-map fixture bytes and restore valid checksums."""

    data = bytearray(dat_path.read_bytes())
    mutation(data)
    data[0xFF] = _old_map_checksum(data, 0)
    data[0x1FF] = _old_map_checksum(data, 0x100)
    dat_path.write_bytes(data)


def set_root_entry_field(dat_path: Path, name: str, offset: int, value: bytes) -> None:
    """Replace one generated root catalogue field without relying on entry order."""

    reader = reader_for(dat_path)
    geometry = geometry_from_dsc(dat_path.with_suffix(".dsc").read_bytes())
    mount = create_filesystem("adfs").open(reader, geometry)
    try:
        root = mount._adfs._read_root_directory()  # type: ignore[attr-defined]
        index = next(index for index, entry in enumerate(root.entries) if entry.name == name)
    finally:
        adfs = getattr(mount, "_adfs", None)
        close = getattr(adfs, "close", None)
        if callable(close):
            close()
        reader.close()
    with dat_path.open("r+b") as handle:
        handle.seek(2 * 256 + 5 + index * 26 + offset)
        handle.write(value)


def set_root_entry_length(dat_path: Path, name: str, length: int) -> None:
    set_root_entry_field(dat_path, name, 0x12, length.to_bytes(4, "little"))


def set_root_entry_start(dat_path: Path, name: str, start_sector: int) -> None:
    set_root_entry_field(dat_path, name, 0x16, start_sector.to_bytes(3, "little"))


def reserve_adfs_tail(dat_path: Path, sectors: int) -> None:
    """Reduce generated ADFS map capacity while retaining the DAT/DSC geometry."""

    def reserve(data: bytearray) -> None:
        adfs_sectors = int.from_bytes(data[0xFC:0xFF], "little")
        free_sectors = int.from_bytes(data[0x100:0x103], "little")
        data[0xFC:0xFF] = (adfs_sectors - sectors).to_bytes(3, "little")
        data[0x100:0x103] = (free_sectors - sectors).to_bytes(3, "little")

    rewrite_old_map(dat_path, reserve)


def create_beebscsi_image(
    directory: Path,
    *,
    stem: str = "scsi0",
    populated: bool = True,
    cylinders: int = 80,
    heads: int = 2,
) -> tuple[Path, Path]:
    dat_path = directory / f"{stem}.dat"
    dsc_path = directory / f"{stem}.dsc"
    geometry = winchester_geometry(cylinders=cylinders, heads=heads, sectors_per_track=33)
    create_filesystem("adfs").create(dat_path, geometry, title="ACORNFS")

    descriptor = bytearray(22)
    descriptor[13:15] = cylinders.to_bytes(2, "big")
    descriptor[15] = heads
    dsc_path.write_bytes(descriptor)

    if populated:
        reader = reader_for(dat_path, writable=True)
        mount = create_filesystem("adfs").open(reader, geometry)
        try:
            mount.write_bytes("$.README", b"Hello from AcornFS\r")
            mount.make_directory("$.DOCS", title="Documents")  # type: ignore[attr-defined]
            mount.write_bytes("$.DOCS.GUIDE", b"Nested file\r")
            mount.make_directory("$.EMPTY", title="Empty")  # type: ignore[attr-defined]
        finally:
            adfs = getattr(mount, "_adfs", None)
            close = getattr(adfs, "close", None)
            if callable(close):
                close()
            reader.close()
    return dat_path, dsc_path
