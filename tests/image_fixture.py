"""Generated BeebSCSI test image; no private media required."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from oaknut.file import Access, AcornMeta
from oaknut.filesystem import create_filesystem, geometry_from_dsc, reader_for, winchester_geometry

from acornfs.core.mmb import MMB_HEADER_BYTES, MMB_SLOT_BYTES, MMB_STANDARD_BYTES


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


def create_adfs_floppy(
    directory: Path, *, format_name: str = "l", filename: str | None = None
) -> Path:
    """Create a populated standalone ADFS S, M or L floppy image."""

    filesystem = create_filesystem("adfs")
    geometry = filesystem.geometry_grammar().presets[format_name]
    suffix = {"s": ".ads", "m": ".adm", "l": ".adl"}[format_name]
    image_path = directory / (filename or f"floppy-{format_name}{suffix}")
    filesystem.create(image_path, geometry, title=f"ADFS-{format_name.upper()}")
    reader = reader_for(image_path, writable=True)
    mount = filesystem.open(reader, geometry)
    try:
        mount.write_bytes("$.HELLO", b"Hello from floppy\r")
        mount.make_directory("$.DOCS", title="Documents")  # type: ignore[attr-defined]
        mount.write_bytes("$.DOCS.GUIDE", b"Floppy guide\r")
    finally:
        adfs = getattr(mount, "_adfs", None)
        close = getattr(adfs, "close", None)
        if callable(close):
            close()
        reader.close()
    return image_path


def create_dfs_floppy(
    directory: Path,
    *,
    double_sided: bool = False,
    filename: str | None = None,
    filesystem_name: str = "acorn-dfs",
) -> Path:
    """Create a populated Acorn or Watford DFS SSD or DSD image."""

    filesystem = create_filesystem(filesystem_name)
    suffix = ".dsd" if double_sided else ".ssd"
    geometry = filesystem.default_geometry(suffix)
    assert geometry is not None
    image_path = directory / (filename or f"dfs{suffix}")
    filesystem.create(image_path, geometry, title="DFS ZERO")
    reader = reader_for(image_path, writable=True)
    try:
        side_zero = filesystem.open(reader, geometry, surface=0)
        try:
            side_zero.write_bytes("$.HELLO", b"Hello from DFS drive 0\r")
            side_zero.write_bytes("A.NOTES", b"Catalogue A\r")
        finally:
            side_zero.close()  # type: ignore[attr-defined]
        if double_sided:
            side_two = filesystem.open(reader, geometry, surface=1)
            try:
                side_two.set_title("DFS TWO")  # type: ignore[attr-defined]
                side_two.write_bytes("$.OTHER", b"Hello from DFS drive 2\r")
                side_two.write_bytes("B.DATA", b"Catalogue B\r")
            finally:
                side_two.close()  # type: ignore[attr-defined]
    finally:
        reader.close()
    return image_path


def create_romfs_image(directory: Path, *, filename: str = "utilities.rom") -> Path:
    """Create a populated, CRC-valid 8 KiB ROMFS image."""

    filesystem = create_filesystem("acorn-romfs")
    geometry = filesystem.geometry_grammar().presets["8k"]
    image_path = directory / filename
    filesystem.create(image_path, geometry, title="ACORNFS")
    reader = reader_for(image_path, writable=True)
    mount = filesystem.open(reader, geometry)
    try:
        mount.write_bytes("HELLO", b"Hello from ROMFS\r")
        mount.write_bytes("Case", b"upper case name\r")
        mount.write_bytes("case", b"lower case name\r")
        mount.write_bytes("A/B", b"slash in name\r")
        mount.set_acorn_meta(
            "HELLO",
            AcornMeta(
                load_address=0xFFFF8000,
                exec_address=0xFFFF8000,
                access=int(Access.X),
            ),
        )
    finally:
        close = getattr(mount, "close", None)
        if callable(close):
            close()
        reader.close()
    return image_path


def create_mmb_image(
    directory: Path, *, filename: str = "BEEB.MMB", slot_indexes: tuple[int, ...] | None = None
) -> Path:
    """Create a sparse standard MMB with two populated DFS slots."""

    image_path = directory / filename
    header = bytearray(MMB_HEADER_BYTES)
    header[:8] = bytes((0, 1, 2, 3, 0, 0, 0, 0))
    for index in range(511):
        entry_offset = (index + 1) * 16
        header[entry_offset + 15] = 0xF0

    slots = (
        ((0, "WELCOME", b"Slot zero\r"), (42, "UTILITIES", b"Slot forty-two\r"))
        if slot_indexes is None
        else tuple(
            (index, f"SLOT {index}", f"Slot {index}\r".encode("ascii")) for index in slot_indexes
        )
    )
    with image_path.open("wb") as container:
        container.write(header)
        container.truncate(MMB_STANDARD_BYTES)

    filesystem = create_filesystem("acorn-dfs")
    geometry = filesystem.default_geometry(".ssd")
    assert geometry is not None
    for index, label, contents in slots:
        ssd_path = directory / f"slot-{index}.ssd"
        filesystem.create(ssd_path, geometry, title=label)
        reader = reader_for(ssd_path, writable=True)
        mount = filesystem.open(reader, geometry)
        try:
            mount.write_bytes("$.HELLO", contents)
            mount.write_bytes("A.INFO", f"Information for {index}\r".encode("ascii"))
        finally:
            mount.close()  # type: ignore[attr-defined]
            reader.close()
        payload = ssd_path.read_bytes()
        assert len(payload) == MMB_SLOT_BYTES
        with image_path.open("r+b") as container:
            entry_offset = (index + 1) * 16
            container.seek(entry_offset)
            container.write(label.encode("acorn").ljust(12, b" ") + b"\x00\x00\x00\x0f")
            container.seek(MMB_HEADER_BYTES + index * MMB_SLOT_BYTES)
            container.write(payload)
        ssd_path.unlink()
    return image_path
