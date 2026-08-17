"""BeebSCSI pair discovery and descriptor validation."""

from __future__ import annotations

import fcntl
import mmap
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from oaknut.filesystem.reader import ImageReader

from acornfs.errors import DescriptorError, PairDiscoveryError
from acornfs.i18n import _

DESCRIPTOR_SIZE = 22
SECTOR_SIZE = 256
SECTORS_PER_TRACK = 33
MAX_ADFS_SECTORS = 0x1FFFFF


@dataclass(frozen=True, slots=True)
class BeebSCSIGeometry:
    """Geometry encoded by a BeebSCSI mode descriptor."""

    cylinders: int
    heads: int
    sectors_per_track: int = SECTORS_PER_TRACK
    sector_size: int = SECTOR_SIZE

    @property
    def sectors(self) -> int:
        return self.cylinders * self.heads * self.sectors_per_track

    @property
    def capacity(self) -> int:
        return self.sectors * self.sector_size


@dataclass(frozen=True, slots=True)
class BeebSCSIPair:
    """Canonical paths to the two members of a BeebSCSI image."""

    dat_path: Path
    dsc_path: Path


def open_locked_reader(
    pair: BeebSCSIPair, *, writable: bool
) -> tuple[ImageReader, tuple[Any, ...]]:
    """Lock both pair members and map the DAT for the requested access mode."""

    mode = "r+b" if writable else "rb"
    lock_mode = fcntl.LOCK_EX if writable else fcntl.LOCK_SH
    stack = ExitStack()
    try:
        dat_lock = stack.enter_context(pair.dat_path.open(mode))
        dsc_lock = stack.enter_context(pair.dsc_path.open(mode))
        mapping_handle = stack.enter_context(pair.dat_path.open(mode))
        fcntl.flock(dat_lock, lock_mode | fcntl.LOCK_NB)
        fcntl.flock(dsc_lock, lock_mode | fcntl.LOCK_NB)
        access = mmap.ACCESS_WRITE if writable else mmap.ACCESS_COPY
        mapping = mmap.mmap(mapping_handle.fileno(), 0, access=access)
        stack.callback(mapping.close)
        reader = ImageReader(mapping, suffix=pair.dat_path.suffix, writable=True)
    except Exception:
        stack.close()
        raise
    stack.pop_all()
    return reader, (mapping, mapping_handle, dat_lock, dsc_lock)


def _matching_files(directory: Path, expected_name: str) -> list[Path]:
    try:
        children = directory.iterdir()
        return sorted(
            (
                child
                for child in children
                if child.name.casefold() == expected_name and child.is_file()
            ),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise PairDiscoveryError(
            _("Cannot inspect image directory {directory}: {error}").format(
                directory=directory, error=exc
            )
        ) from exc


def discover_pair(selected: str | Path) -> BeebSCSIPair:
    """Find an unambiguous matching DAT/DSC pair from either selected member."""

    selected_path = Path(selected).expanduser()
    if not selected_path.is_file():
        raise PairDiscoveryError(
            _("Image member does not exist or is not a file: {path}").format(path=selected_path)
        )

    suffix = selected_path.suffix.casefold()
    if suffix not in {".dat", ".dsc"}:
        raise PairDiscoveryError(_("A BeebSCSI image member must use the DAT or DSC extension."))

    stem = selected_path.stem.casefold()
    dat_candidates = _matching_files(selected_path.parent, f"{stem}.dat")
    dsc_candidates = _matching_files(selected_path.parent, f"{stem}.dsc")
    if len(dat_candidates) != 1 or len(dsc_candidates) != 1:
        raise PairDiscoveryError(
            _(
                "Expected exactly one matching DAT/DSC pair for {name}; found {dat_count} DAT "
                "and {dsc_count} DSC candidates."
            ).format(
                name=selected_path.name,
                dat_count=len(dat_candidates),
                dsc_count=len(dsc_candidates),
            )
        )

    return BeebSCSIPair(dat_path=dat_candidates[0].resolve(), dsc_path=dsc_candidates[0].resolve())


def parse_descriptor(data: bytes) -> BeebSCSIGeometry:
    """Validate and decode an official 22-byte BeebSCSI descriptor."""

    if len(data) != DESCRIPTOR_SIZE:
        raise DescriptorError(
            _("The DSC descriptor must be exactly {expected} bytes; got {actual}.").format(
                expected=DESCRIPTOR_SIZE, actual=len(data)
            )
        )

    cylinders = int.from_bytes(data[13:15], "big")
    heads = data[15]
    if cylinders == 0:
        raise DescriptorError(_("The DSC descriptor declares zero cylinders."))
    if heads == 0:
        raise DescriptorError(_("The DSC descriptor declares zero heads."))

    geometry = BeebSCSIGeometry(cylinders=cylinders, heads=heads)
    if geometry.sectors > MAX_ADFS_SECTORS:
        raise DescriptorError(
            _("The DSC geometry exceeds the 21-bit sector limit of old-format ADFS.")
        )
    return geometry


def inspect_pair(selected: str | Path) -> dict[str, Any]:
    """Return safe, serialisable metadata for a BeebSCSI pair."""

    pair = discover_pair(selected)
    try:
        descriptor = pair.dsc_path.read_bytes()
        dat_size = pair.dat_path.stat().st_size
    except OSError as exc:
        raise PairDiscoveryError(
            _("Cannot read the BeebSCSI pair: {error}").format(error=exc)
        ) from exc

    geometry = parse_descriptor(descriptor)
    if dat_size > geometry.capacity:
        raise DescriptorError(
            _("The DAT is {actual} bytes but its DSC geometry allows only {capacity}.").format(
                actual=dat_size, capacity=geometry.capacity
            )
        )

    warnings: list[str] = []
    if dat_size < geometry.capacity:
        warnings.append(
            "The DAT is shorter than the descriptor capacity; "
            "ADFS map validation is still required."
        )

    return {
        "format": "beebscsi",
        "dat": str(pair.dat_path),
        "dsc": str(pair.dsc_path),
        "dat_size": dat_size,
        "geometry": {
            **asdict(geometry),
            "sectors": geometry.sectors,
            "capacity": geometry.capacity,
        },
        "warnings": warnings,
        "default_read_only": True,
        "writable_supported": True,
    }
