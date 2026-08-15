"""Generated BeebSCSI test image; no private media required."""

from __future__ import annotations

from pathlib import Path

from oaknut.filesystem import create_filesystem, reader_for, winchester_geometry


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
