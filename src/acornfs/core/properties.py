"""Typed compatibility, capacity and validation metadata for Acorn images."""

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
    open_locked_reader,
    parse_descriptor,
)
from acornfs.core.formats import ResolvedImage, resolve_image
from acornfs.core.mmb import MMB_HEADER_BYTES, MMB_SLOT_BYTES, MMBMount
from acornfs.core.storage import open_locked_image
from acornfs.core.validation import validate_open_mount
from acornfs.errors import AcornFSError
from acornfs.i18n import _
from acornfs.operations import OperationBudget

_DIRECTORY_FORMATS = {
    "OldDirectoryFormat": "Old directory (Hugo)",
    "NewDirectoryFormat": "New directory (Nick)",
    "BigDirectoryFormat": "Big directory",
}


def _directory_format(adfs: Any) -> str:
    class_name = type(adfs._dir_format).__name__
    return _DIRECTORY_FORMATS.get(class_name, class_name)


def _close_mount(mount: Mount | None) -> None:
    close = getattr(mount, "close", None)
    if callable(close):
        with suppress(Exception):
            close()
        return
    close = getattr(getattr(mount, "_adfs", None), "close", None)
    if callable(close):
        with suppress(Exception):
            close()


def _close_reader(reader: ImageReader | None, closeables: tuple[Any, ...]) -> None:
    if reader is not None:
        with suppress(Exception):
            reader.close()
    for closeable in closeables:
        with suppress(Exception):
            if closeable is not None:
                closeable.close()


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
    geometry_kind: str = "winchester"
    write_supported: bool = True
    filesystem_size_label: str = "ADFS size"
    show_disc_id: bool = True
    show_disc_name: bool = True
    boot_label: str = "Boot option"
    slot_count: int = 0
    formatted_slots: int = 0
    geometry_description: str = ""
    reserved_label: str = "Reserved tail"
    show_boot_option: bool = True
    show_space_breakdown: bool = True
    extent_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def read_image_properties(
    selected: str | Path, *, budget: OperationBudget | None = None
) -> ImageProperties:
    """Read metadata and a complete validation result under one shared lock."""

    operation = budget or OperationBudget.create()
    operation.checkpoint()
    source = resolve_image(selected)
    operation.checkpoint()
    pair = source.pair
    if pair is None:
        return _read_standalone_properties(source, budget=operation)
    reader: ImageReader | None = None
    mount: Mount | None = None
    closeables: tuple[Any, ...] = ()
    try:
        reader, closeables, descriptor = open_locked_reader(pair, writable=False)
        descriptor_geometry = parse_descriptor(descriptor)
        geometry = geometry_from_dsc(descriptor)
        mount = create_filesystem("adfs").open(reader, geometry)
        mount = cast(Any, mount)
        adfs = mount._adfs
        free_space_map = adfs._fsm
        report = validate_open_mount(pair, mount, descriptor_geometry, budget=operation)
        adfs_sectors = int(report.adfs_sectors or 0)
        used_sectors = int(report.used_sectors or 0)
        free_sectors = int(report.free_sectors or 0)
        boot = mount.boot_option
        directory_format = _directory_format(adfs)
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
        raise AcornFSError(
            _("The ADFS image properties could not be read safely: {error}").format(error=exc)
        ) from exc
    finally:
        _close_mount(mount)
        if reader is not None:
            with suppress(Exception):
                reader.close()
        for closeable in closeables:
            with suppress(Exception):
                closeable.close()


def _read_standalone_properties(
    source: ResolvedImage, *, budget: OperationBudget
) -> ImageProperties:
    """Read the capability-backed summary for a detected standalone image."""

    if source.kind == "dfs-floppy":
        return _read_dfs_properties(source, budget=budget)
    if source.kind == "mmb-container":
        return _read_mmb_properties(source, budget=budget)
    if source.kind == "romfs-image":
        return _read_romfs_properties(source, budget=budget)

    reader: ImageReader | None = None
    mount: Mount | None = None
    closeables: tuple[Any, ...] = ()
    try:
        reader, closeables = open_locked_image(
            source.primary_path,
            writable=False,
            companion=source.companion_path,
        )
        mount = create_filesystem(source.filesystem).open(reader, source.geometry)
        budget.checkpoint(items=1)
        api = cast(Any, mount)
        adfs = api._adfs
        total_bytes = int(api.size_bytes())
        free_bytes = int(api.free_bytes())
        specs = source.geometry.surface_specs
        directory_format = _directory_format(adfs)
        boot = api.boot_option
        validation_problems = tuple(api.validate())
        if adfs.is_new_map:
            map_metadata = adfs._map.disc_record
            filesystem_format = "ADFS new map"
        else:
            map_metadata = adfs._fsm
            filesystem_format = "ADFS old map"
        hard_disc = source.kind == "adfs-hard-disc"
        if hard_disc:
            paired_geometry = source.companion_path is not None
            internal_geometry = source.geometry if paired_geometry else adfs.geometry
            tracks = int(internal_geometry.cylinders)
            sides = int(internal_geometry.heads)
            sectors_per_track = int(internal_geometry.sectors_per_track)
            sector_size = int(map_metadata.sector_size) if adfs.is_new_map else SECTOR_SIZE
            zones = int(map_metadata.nzones) if adfs.is_new_map else 0
            geometry_description = (
                _(
                    "{cylinders} cylinders × {heads} heads × {sectors} sectors/track; "
                    "FileCore New Map, {zones} zones, {sector_size}-byte sectors"
                ).format(
                    cylinders=tracks,
                    heads=sides,
                    sectors=sectors_per_track,
                    zones=zones,
                    sector_size=sector_size,
                )
                if paired_geometry and adfs.is_new_map
                else _("FileCore New Map, {zones} zones, {sector_size}-byte sectors").format(
                    zones=zones, sector_size=sector_size
                )
                if adfs.is_new_map
                else _("ADFS old-map linear image; physical CHS unavailable")
            )
        else:
            tracks = max(spec.num_tracks for spec in specs)
            sides = len(specs)
            sectors_per_track = specs[0].sectors_per_track
            sector_size = specs[0].bytes_per_sector
            geometry_description = ""
        return ImageProperties(
            dat_path=str(source.primary_path),
            dsc_path=str(source.companion_path) if source.companion_path is not None else "",
            image_type=(
                "ADFS DAT/DSC pair (New Map)"
                if hard_disc and source.companion_path is not None
                else "Standalone ADFS hard-disc image"
                if hard_disc
                else "Standalone ADFS floppy image"
            ),
            filesystem_format=filesystem_format,
            directory_format=directory_format,
            hardware_profile=("RISC OS FileCore hard disc" if hard_disc else "Acorn ADFS floppy"),
            title=str(api.title),
            disc_name=str(map_metadata.disc_name),
            disc_id=int(map_metadata.disc_id),
            boot_option=f"{boot.name.title()} ({int(boot)})",
            cylinders=tracks,
            heads=sides,
            sectors_per_track=sectors_per_track,
            sector_size=sector_size,
            geometry_sectors=source.geometry.num_sectors,
            adfs_sectors=total_bytes // sector_size,
            capacity_bytes=(
                source.primary_path.stat().st_size if hard_disc else source.geometry.image_size
            ),
            adfs_bytes=total_bytes,
            used_bytes=total_bytes - free_bytes,
            free_bytes=free_bytes,
            reserved_bytes=max(0, source.geometry.image_size - total_bytes),
            safe_for_write=not validation_problems,
            fatal_findings=len(validation_problems),
            warning_findings=0,
            advice_findings=0,
            geometry_kind="container" if hard_disc else "floppy",
            write_supported=True,
            geometry_description=geometry_description,
        )
    except AcornFSError:
        raise
    except Exception as exc:
        raise AcornFSError(
            _("The standalone ADFS image properties could not be read safely: {error}").format(
                error=exc
            )
        ) from exc
    finally:
        _close_mount(mount)
        _close_reader(reader, closeables)


def _read_dfs_properties(source: ResolvedImage, *, budget: OperationBudget) -> ImageProperties:
    """Read a summary across every independently catalogued DFS side."""

    reader: ImageReader | None = None
    mounts: list[Any] = []
    closeables: tuple[Any, ...] = ()
    try:
        reader, closeables = open_locked_image(source.primary_path, writable=False)
        filesystem = create_filesystem(source.filesystem)
        side_count = len(source.geometry.surface_specs)
        for side in range(side_count):
            budget.checkpoint(items=1)
            mounts.append(filesystem.open(reader, source.geometry, surface=side))
        free_bytes = sum(int(mount.free_bytes()) for mount in mounts)
        filesystem_bytes = sum(int(mount.size_bytes()) for mount in mounts)
        titles = [str(mount.title) for mount in mounts]
        boots = [mount.boot_option for mount in mounts]
        validation_problems = tuple(problem for mount in mounts for problem in mount.validate())
        boot = boots[0]
        boot_text = (
            f"{boot.name.title()} ({int(boot)})"
            if all(item == boot for item in boots)
            else _("Different on each side")
        )
        specs = source.geometry.surface_specs
        tracks = max(spec.num_tracks for spec in specs)
        sectors_per_track = specs[0].sectors_per_track
        filesystem_name = "Watford DFS" if source.filesystem == "watford-dfs" else "Acorn DFS"
        return ImageProperties(
            dat_path=str(source.primary_path),
            dsc_path="",
            image_type="DFS floppy image",
            filesystem_format=filesystem_name,
            directory_format="Flat catalogue prefixes",
            hardware_profile="BBC Micro DFS floppy",
            title=" / ".join(titles),
            disc_name="",
            disc_id=0,
            boot_option=boot_text,
            cylinders=tracks,
            heads=side_count,
            sectors_per_track=sectors_per_track,
            sector_size=SECTOR_SIZE,
            geometry_sectors=source.geometry.num_sectors,
            adfs_sectors=filesystem_bytes // SECTOR_SIZE,
            capacity_bytes=source.geometry.image_size,
            adfs_bytes=filesystem_bytes,
            used_bytes=filesystem_bytes - free_bytes,
            free_bytes=free_bytes,
            reserved_bytes=max(0, source.geometry.image_size - filesystem_bytes),
            safe_for_write=not validation_problems,
            fatal_findings=len(validation_problems),
            warning_findings=0,
            advice_findings=0,
            geometry_kind="floppy",
            write_supported=True,
            filesystem_size_label="DFS size",
            show_disc_id=False,
        )
    except AcornFSError:
        raise
    except Exception as exc:
        raise AcornFSError(
            _("The DFS floppy properties could not be read safely: {error}").format(error=exc)
        ) from exc
    finally:
        for mount in mounts:
            _close_mount(mount)
        _close_reader(reader, closeables)


def _read_romfs_properties(source: ResolvedImage, *, budget: OperationBudget) -> ImageProperties:
    """Read a ROMFS catalogue through Oaknut's public mount interface."""

    reader: ImageReader | None = None
    mount: Mount | None = None
    closeables: tuple[Any, ...] = ()
    try:
        reader, closeables = open_locked_image(source.primary_path, writable=False)
        mount = create_filesystem(source.filesystem).open(reader, source.geometry)
        entries = tuple(mount.iter_entries(mount.path_root()))
        budget.checkpoint(items=1 + len(entries))
        payload_bytes = sum(entry.length for entry in entries)
        capacity = source.primary_path.stat().st_size
        return ImageProperties(
            dat_path=str(source.primary_path),
            dsc_path="",
            image_type="Acorn ROMFS image",
            filesystem_format="Acorn ROMFS",
            directory_format="Flat ROM catalogue",
            hardware_profile="BBC Micro / Acorn Electron paged ROM",
            title=str(getattr(mount, "title", "")),
            disc_name="",
            disc_id=0,
            boot_option="",
            cylinders=0,
            heads=0,
            sectors_per_track=0,
            sector_size=1,
            geometry_sectors=capacity,
            adfs_sectors=payload_bytes,
            capacity_bytes=capacity,
            adfs_bytes=payload_bytes,
            used_bytes=payload_bytes,
            free_bytes=0,
            reserved_bytes=0,
            safe_for_write=False,
            fatal_findings=0,
            warning_findings=0,
            advice_findings=0,
            geometry_kind="container",
            write_supported=False,
            filesystem_size_label="File payload",
            show_disc_id=False,
            show_disc_name=False,
            geometry_description=f"{capacity // 1024} KiB linear paged ROM",
            show_boot_option=False,
            show_space_breakdown=False,
        )
    except AcornFSError:
        raise
    except Exception as exc:
        raise AcornFSError(
            _("The ROMFS image properties could not be read safely: {error}").format(error=exc)
        ) from exc
    finally:
        _close_mount(mount)
        _close_reader(reader, closeables)


def _read_mmb_properties(source: ResolvedImage, *, budget: OperationBudget) -> ImageProperties:
    """Report MMB catalogue, payload capacity and slot validation state."""

    layout = source.mmb_layout
    budget.checkpoint(items=1)
    if layout is None:
        raise AcornFSError(_("The MMB container catalogue is unavailable."))
    payload_bytes = layout.total_slots * MMB_SLOT_BYTES
    formatted_bytes = layout.formatted_slots * MMB_SLOT_BYTES
    catalogue_bytes = layout.extent_count * MMB_HEADER_BYTES
    boot_slots = ", ".join(str(slot) for slot in layout.boot_slots)
    extended = layout.extent_count > 1
    reader, closeables = open_locked_image(source.primary_path, writable=False)
    mount = MMBMount(reader, layout)
    try:
        validation_problems = tuple(mount.validate())
    finally:
        mount.close()
        _close_reader(reader, closeables)
    return ImageProperties(
        dat_path=str(source.primary_path),
        dsc_path="",
        image_type="Extended MMB container" if extended else "Standard MMB container",
        filesystem_format=(
            "Extended MMB with Acorn DFS slots" if extended else "MMB with Acorn DFS slots"
        ),
        directory_format="Slots with flat DFS catalogue prefixes",
        hardware_profile="BBC Micro MMC/SD MMB container",
        title="",
        disc_name="",
        disc_id=0,
        boot_option=boot_slots,
        cylinders=0,
        heads=0,
        sectors_per_track=0,
        sector_size=SECTOR_SIZE,
        geometry_sectors=source.primary_path.stat().st_size // SECTOR_SIZE,
        adfs_sectors=payload_bytes // SECTOR_SIZE,
        capacity_bytes=source.primary_path.stat().st_size,
        adfs_bytes=payload_bytes,
        used_bytes=formatted_bytes,
        free_bytes=payload_bytes - formatted_bytes,
        reserved_bytes=catalogue_bytes,
        safe_for_write=not validation_problems,
        fatal_findings=len(validation_problems),
        warning_findings=0,
        advice_findings=0,
        geometry_kind="mmb",
        write_supported=True,
        filesystem_size_label="Slot payload",
        show_disc_id=False,
        show_disc_name=False,
        boot_label="Boot slots",
        slot_count=layout.total_slots,
        formatted_slots=layout.formatted_slots,
        geometry_description="511 × 200 KiB SSD slots",
        reserved_label="Container catalogues" if extended else "Container catalogue",
        extent_count=layout.extent_count,
    )


__all__ = ["ImageProperties", "read_image_properties"]
