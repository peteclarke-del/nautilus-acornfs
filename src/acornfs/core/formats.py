"""Content-driven image detection and capability selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oaknut.filesystem import Geometry, geometry_from_dsc, identify

from acornfs.core.beebscsi import BeebSCSIPair, discover_pair, parse_descriptor
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

    @property
    def identity_paths(self) -> tuple[Path, ...]:
        return (
            (self.primary_path, self.companion_path)
            if self.companion_path is not None
            else (self.primary_path,)
        )


_BEEBSCSI_CAPABILITIES = ImageCapabilities(True, True, True, True, True, True, True)
_ADFS_FLOPPY_CAPABILITIES = ImageCapabilities(True, False, False, False, False, True, False)


def resolve_image(selected: str | Path) -> ResolvedImage:
    """Resolve a supported image by pairing rules first, then content evidence."""

    selected_path = Path(selected).expanduser()
    if not selected_path.is_file():
        raise UnsupportedImageError(
            _("Image does not exist or is not a regular file: {path}").format(path=selected_path)
        )

    pair_error: PairDiscoveryError | None = None
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
        candidates = identify(selected_path)
    except Exception as exc:
        raise UnsupportedImageError(
            _("Could not inspect image {path}: {error}").format(path=selected_path, error=exc)
        ) from exc
    if candidates:
        candidate = candidates[0]
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
    raise UnsupportedImageError(
        _("The file is not a supported AcornFS image: {path}").format(path=selected_path)
    )


__all__ = ["ImageCapabilities", "ResolvedImage", "resolve_image"]
