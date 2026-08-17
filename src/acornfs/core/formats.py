"""Content-driven image detection and capability selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oaknut.adfs.new_map import DiscRecord, calculate_zone_check, compute_bootmap
from oaknut.filesystem import (
    Geometry,
    create_filesystem,
    floppy_geometry,
    geometry_from_dsc,
    identify,
    reader_for,
    winchester_geometry,
)

from acornfs.core.beebscsi import BeebSCSIPair, discover_pair, parse_descriptor
from acornfs.core.mmb import (
    MMBFormatError,
    MMBLayout,
    detect_mmb,
    read_mmb_layout,
    require_mmb_content_evidence,
)
from acornfs.errors import PairDiscoveryError, UnsupportedImageError
from acornfs.i18n import _


@dataclass(frozen=True, slots=True)
class ImageCapabilities:
    """Operations safe for one detected image profile."""

    mount_read_only: bool
    mount_read_write: bool
    validate: bool
    repair: bool
    recover: bool
    properties: bool
    file_forge: bool


@dataclass(frozen=True, slots=True)
class ResolvedImage:
    """Canonical source identity plus the filesystem needed to open it."""

    primary_path: Path
    filesystem: str
    geometry: Geometry
    kind: str
    capabilities: ImageCapabilities
    companion_path: Path | None = None
    pair: BeebSCSIPair | None = None
    mmb_layout: MMBLayout | None = None
    case_sensitive_names: bool = False

    @property
    def identity_paths(self) -> tuple[Path, ...]:
        return (
            (self.primary_path, self.companion_path)
            if self.companion_path is not None
            else (self.primary_path,)
        )


_BEEBSCSI_CAPABILITIES = ImageCapabilities(True, True, True, True, True, True, True)
_STANDALONE_ADFS_CAPABILITIES = ImageCapabilities(True, True, True, False, True, True, False)
_DFS_CAPABILITIES = ImageCapabilities(True, True, True, False, True, True, False)
_MMB_CAPABILITIES = ImageCapabilities(True, True, True, False, True, True, False)
_ROMFS_CAPABILITIES = ImageCapabilities(True, False, False, False, False, True, False)
_ADFS_LOGICAL_SECTOR_BYTES = 256
_ADFS_BOOT_BLOCK_BYTES = 0xE00
_ADFS_HDF_HEADER_BYTES = 0x200


def _refine_adfs_floppy_geometry(path: Path, proposed: Geometry) -> Geometry:
    """Resolve same-sized D/E/E+ and F/G plus variants from on-disc state."""

    filesystem = create_filesystem("adfs")
    reader = reader_for(path)
    mount: Any | None = None
    try:
        mount = filesystem.open(reader, proposed)
        adfs = mount._adfs
        presets = filesystem.geometry_grammar().presets
        if adfs.is_new_map:
            disc_record = adfs._map.disc_record
            base_variant = {
                presets["e"].image_size: "e",
                presets["f"].image_size: "f",
                presets["g"].image_size: "g",
            }.get(reader.size)
            if base_variant is None:
                return proposed
            variant = f"{base_variant}+" if disc_record.uses_big_directories else base_variant
            return presets[variant]
        if type(adfs._dir_format).__name__ == "NewDirectoryFormat":
            return presets["d"]
        return proposed
    finally:
        close = getattr(getattr(mount, "_adfs", None), "close", None)
        if callable(close):
            close()
        reader.close()


def _adfs_new_map_state(path: Path) -> bool | None:
    """Return True for valid, False for malformed, or None for no New Map evidence."""

    reader = reader_for(path)
    malformed = False
    try:
        for base in (0, _ADFS_HDF_HEADER_BYTES):
            boot = reader.read(base, _ADFS_BOOT_BLOCK_BYTES)
            if len(boot) != _ADFS_BOOT_BLOCK_BYTES:
                continue
            single = DiscRecord.parse(boot, offset=0x04)
            if single.looks_valid() and single.nzones == 1:
                copies = reader.read(base, single.sector_size * 2)
                primary = copies[: single.sector_size]
                duplicate = copies[single.sector_size :]
                if (
                    len(duplicate) == single.sector_size
                    and primary == duplicate
                    and primary[0] == calculate_zone_check(primary, 0, single.log2_sector_size)
                ):
                    return True
                malformed = True
            partial = DiscRecord.parse(boot, offset=0xDC0)
            if not partial.looks_valid() or partial.nzones <= 1:
                continue
            map_offset = base + compute_bootmap(partial)
            map_length = partial.nzones * partial.sector_size
            copies = reader.read(map_offset, map_length * 2)
            if len(copies) != map_length * 2:
                malformed = True
                continue
            map_bytes = copies[:map_length]
            duplicate = copies[map_length:]
            full = DiscRecord.parse(map_bytes, offset=0x04)
            if (
                full.looks_valid()
                and full.nzones == partial.nzones
                and map_bytes == duplicate
                and all(
                    map_bytes[zone * full.sector_size]
                    == calculate_zone_check(map_bytes, zone, full.log2_sector_size)
                    for zone in range(full.nzones)
                )
            ):
                return True
            malformed = True
        return False if malformed else None
    finally:
        reader.close()


def resolve_image(selected: str | Path) -> ResolvedImage:
    """Resolve a supported image by pairing rules first, then content evidence."""

    selected_path = Path(selected).expanduser()
    if not selected_path.is_file():
        raise UnsupportedImageError(
            _("Image does not exist or is not a regular file: {path}").format(path=selected_path)
        )

    pair_error: PairDiscoveryError | None = None
    mmb_error: MMBFormatError | OSError | None = None
    mmb_layout: MMBLayout | None = None
    if selected_path.suffix.casefold() in {".dat", ".dsc"}:
        try:
            pair = discover_pair(selected_path)
        except PairDiscoveryError as exc:
            pair_error = exc
        else:
            try:
                descriptor = pair.dsc_path.read_bytes()
            except OSError as exc:
                raise UnsupportedImageError(
                    _("Could not read image descriptor {path}: {error}").format(
                        path=pair.dsc_path, error=exc
                    )
                ) from exc
            parse_descriptor(descriptor)
            geometry = geometry_from_dsc(descriptor)
            try:
                new_map = _adfs_new_map_state(pair.dat_path)
            except Exception as exc:
                raise UnsupportedImageError(
                    _("The paired ADFS image could not be classified safely: {error}").format(
                        error=exc
                    )
                ) from exc
            if new_map is False:
                raise UnsupportedImageError(
                    _("The paired ADFS image declares a New Map with invalid zone checks.")
                )
            if new_map is True:
                return ResolvedImage(
                    primary_path=pair.dat_path,
                    companion_path=pair.dsc_path,
                    filesystem="adfs",
                    geometry=geometry,
                    kind="adfs-hard-disc",
                    capabilities=_STANDALONE_ADFS_CAPABILITIES,
                )
            return ResolvedImage(
                primary_path=pair.dat_path,
                companion_path=pair.dsc_path,
                filesystem="adfs",
                geometry=geometry,
                kind="beebscsi-adfs",
                capabilities=_BEEBSCSI_CAPABILITIES,
                pair=pair,
            )

    try:
        if selected_path.suffix.casefold() == ".mmb":
            mmb_layout = read_mmb_layout(selected_path)
            require_mmb_content_evidence(selected_path, mmb_layout)
        else:
            mmb_layout = detect_mmb(selected_path)
    except (MMBFormatError, OSError) as exc:
        mmb_error = exc
        mmb_layout = None
    if mmb_layout is not None:
        return ResolvedImage(
            primary_path=selected_path.resolve(),
            filesystem="acorn-dfs",
            geometry=floppy_geometry(
                tracks=80, sides=1, sectors_per_track=10, label="MMB SSD slot"
            ),
            kind="mmb-container",
            capabilities=_MMB_CAPABILITIES,
            mmb_layout=mmb_layout,
        )

    try:
        candidates = identify(selected_path)
    except Exception as exc:
        raise UnsupportedImageError(
            _("Could not inspect image {path}: {error}").format(path=selected_path, error=exc)
        ) from exc
    if candidates:
        candidate = candidates[0]
        if candidate.filesystem == "acorn-romfs" and candidate.geometry is not None:
            return ResolvedImage(
                primary_path=selected_path.resolve(),
                filesystem="acorn-romfs",
                geometry=candidate.geometry,
                kind="romfs-image",
                capabilities=_ROMFS_CAPABILITIES,
                case_sensitive_names=True,
            )
        if candidate.filesystem in {"acorn-dfs", "watford-dfs"} and candidate.geometry is not None:
            return ResolvedImage(
                primary_path=selected_path.resolve(),
                filesystem=candidate.filesystem,
                geometry=candidate.geometry,
                kind="dfs-floppy",
                capabilities=_DFS_CAPABILITIES,
            )
        if candidate.filesystem != "adfs":
            raise UnsupportedImageError(
                _("Detected filesystem {filesystem} is not yet mountable by AcornFS.").format(
                    filesystem=candidate.filesystem
                )
            )
        if candidate.geometry is not None and candidate.geometry.cylinders is None:
            try:
                geometry = _refine_adfs_floppy_geometry(selected_path, candidate.geometry)
            except Exception as exc:
                raise UnsupportedImageError(
                    _("The ADFS floppy format could not be resolved safely: {error}").format(
                        error=exc
                    )
                ) from exc
            return ResolvedImage(
                primary_path=selected_path.resolve(),
                filesystem="adfs",
                geometry=geometry,
                kind="adfs-floppy",
                capabilities=_STANDALONE_ADFS_CAPABILITIES,
            )
        if candidate.geometry is None:
            size = selected_path.stat().st_size
            if size > 0 and size % _ADFS_LOGICAL_SECTOR_BYTES == 0:
                return ResolvedImage(
                    primary_path=selected_path.resolve(),
                    filesystem="adfs",
                    geometry=winchester_geometry(
                        cylinders=1,
                        heads=1,
                        sectors_per_track=size // _ADFS_LOGICAL_SECTOR_BYTES,
                        label="ADFS hard disc (CHS unavailable)",
                    ),
                    kind="adfs-hard-disc",
                    capabilities=_STANDALONE_ADFS_CAPABILITIES,
                )

    if pair_error is not None:
        raise UnsupportedImageError(str(pair_error)) from pair_error
    if mmb_error is not None:
        raise UnsupportedImageError(
            _("The MMB container could not be opened safely: {error}").format(error=mmb_error)
        ) from mmb_error
    raise UnsupportedImageError(
        _("The file is not a supported AcornFS image: {path}").format(path=selected_path)
    )


__all__ = ["ImageCapabilities", "ResolvedImage", "resolve_image"]
