"""Typed compatibility and capacity metadata for BeebSCSI ADFS images."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from oaknut.filesystem import create_filesystem, geometry_from_dsc
from oaknut.filesystem.capabilities import Mount
from oaknut.filesystem.reader import ImageReader

from acornfs.core.beebscsi import (
    SECTOR_SIZE,
    discover_pair,
    open_locked_reader,
    parse_descriptor,
)
from acornfs.core.validation import validate_open_mount
from acornfs.errors import AcornFSError


@dataclass(frozen=True, slots=True)
class ImageProperties:
    """Stable, serialisable metadata shown by user interfaces."""

    dat_path: str
    dsc_path: str
    image_type: str
    filesystem_format: str
    directory_format: str
    hardware_profile: str
    title: str
    disc_name: str
    disc_id: int
    boot_option: str
    cylinders: int
    heads: int
    sectors_per_track: int
    sector_size: int
    geometry_sectors: int
    adfs_sectors: int
    capacity_bytes: int
    adfs_bytes: int
    used_bytes: int
    free_bytes: int
    reserved_bytes: int
    safe_for_write: bool
    fatal_findings: int
    warning_findings: int
    advice_findings: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def read_image_properties(selected: str | Path) -> ImageProperties:
    """Read metadata and a complete validation result under one shared lock."""

    pair = discover_pair(selected)
    reader: ImageReader | None = None
    mount: Mount | None = None
    closeables: tuple[Any, ...] = ()
    try:
        descriptor = pair.dsc_path.read_bytes()
        descriptor_geometry = parse_descriptor(descriptor)
        geometry = geometry_from_dsc(descriptor)
        reader, closeables = open_locked_reader(pair, writable=False)
        mount = create_filesystem("adfs").open(reader, geometry)
        mount = cast(Any, mount)
        adfs = mount._adfs
        free_space_map = adfs._fsm
        report = validate_open_mount(pair, mount, descriptor_geometry)
        adfs_sectors = int(report.adfs_sectors or 0)
        used_sectors = int(report.used_sectors or 0)
        free_sectors = int(report.free_sectors or 0)
        boot = mount.boot_option
        directory_class = type(adfs._dir_format).__name__
        directory_format = {
            "OldDirectoryFormat": "Old directory (Hugo)",
            "NewDirectoryFormat": "New directory (Nick)",
            "BigDirectoryFormat": "Big directory",
        }.get(directory_class, directory_class)
        return ImageProperties(
            dat_path=str(pair.dat_path),
            dsc_path=str(pair.dsc_path),
            image_type="BeebSCSI DAT/DSC pair",
            filesystem_format="ADFS old map",
            directory_format=directory_format,
            hardware_profile="BeebSCSI hard disc (BBC Master / RISC OS old-map ADFS)",
            title=str(mount.title),
            disc_name=str(free_space_map.disc_name),
            disc_id=int(free_space_map.disc_id),
            boot_option=f"{boot.name.title()} ({int(boot)})",
            cylinders=descriptor_geometry.cylinders,
            heads=descriptor_geometry.heads,
            sectors_per_track=descriptor_geometry.sectors_per_track,
            sector_size=descriptor_geometry.sector_size,
            geometry_sectors=descriptor_geometry.sectors,
            adfs_sectors=adfs_sectors,
            capacity_bytes=descriptor_geometry.capacity,
            adfs_bytes=adfs_sectors * SECTOR_SIZE,
            used_bytes=used_sectors * SECTOR_SIZE,
            free_bytes=free_sectors * SECTOR_SIZE,
            reserved_bytes=max(0, descriptor_geometry.sectors - adfs_sectors) * SECTOR_SIZE,
            safe_for_write=report.safe_for_write,
            fatal_findings=len(report.fatal_findings),
            warning_findings=len(report.warning_findings),
            advice_findings=len(report.advice_findings),
        )
    except AcornFSError:
        raise
    except Exception as exc:
        raise AcornFSError(f"The ADFS image properties could not be read safely: {exc}") from exc
    finally:
        if mount is not None:
            close = getattr(getattr(mount, "_adfs", None), "close", None)
            if callable(close):
                with suppress(Exception):
                    close()
        if reader is not None:
            with suppress(Exception):
                reader.close()
        for closeable in closeables:
            with suppress(Exception):
                closeable.close()


__all__ = ["ImageProperties", "read_image_properties"]
