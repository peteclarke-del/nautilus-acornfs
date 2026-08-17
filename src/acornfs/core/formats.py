"""Content-driven image detection and capability selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oaknut.filesystem import Geometry, floppy_geometry, geometry_from_dsc, identify

from acornfs.core.beebscsi import BeebSCSIPair, discover_pair, parse_descriptor
from acornfs.core.mmb import (
    ExtendedMMBError,
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
_ADFS_FLOPPY_CAPABILITIES = ImageCapabilities(True, False, False, False, False, True, False)
_DFS_CAPABILITIES = ImageCapabilities(True, False, False, False, False, True, False)
_MMB_CAPABILITIES = ImageCapabilities(True, False, False, False, False, True, False)
_ROMFS_CAPABILITIES = ImageCapabilities(True, False, False, False, False, True, False)


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
            return ResolvedImage(
                primary_path=pair.dat_path,
                companion_path=pair.dsc_path,
                filesystem="adfs",
                geometry=geometry_from_dsc(descriptor),
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
    except ExtendedMMBError as exc:
        raise UnsupportedImageError(
            _("The MMB container could not be opened safely: {error}").format(error=exc)
        ) from exc
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
            return ResolvedImage(
                primary_path=selected_path.resolve(),
                filesystem="adfs",
                geometry=candidate.geometry,
                kind="adfs-floppy",
                capabilities=_ADFS_FLOPPY_CAPABILITIES,
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
