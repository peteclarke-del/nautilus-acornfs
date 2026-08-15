"""Pure selection logic for the Nautilus extension."""

from __future__ import annotations

import os
from pathlib import Path

from acornfs.core import ImageProperties, discover_pair
from acornfs.errors import PairDiscoveryError


def is_supported_image(path: str | Path) -> bool:
    try:
        discover_pair(path)
    except PairDiscoveryError:
        return False
    return True


def _size(value: int) -> str:
    units = ("bytes", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{int(amount)} {unit}" if unit == "bytes" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def image_property_rows(properties: ImageProperties) -> tuple[tuple[str, str], ...]:
    """Convert typed core metadata to stable labels for Nautilus."""

    validation = "Safe for read-write mounting" if properties.safe_for_write else "Unsafe"
    if properties.warning_findings or properties.advice_findings:
        warnings = properties.warning_findings
        advice = properties.advice_findings
        validation += f" ({warnings} warning, {advice} advice)"
    rows = [
        ("Image type", properties.image_type),
        ("Filesystem", properties.filesystem_format),
        ("Directory format", properties.directory_format),
        ("Hardware profile", properties.hardware_profile),
        ("Title", properties.title or "—"),
        ("Disc name", properties.disc_name or "—"),
        ("Disc cycle ID", f"&{properties.disc_id:04X}"),
        ("Boot option", properties.boot_option),
        (
            "Geometry",
            f"{properties.cylinders} cylinders × {properties.heads} heads × "
            f"{properties.sectors_per_track} sectors/track",
        ),
        ("Capacity", _size(properties.capacity_bytes)),
        ("ADFS size", _size(properties.adfs_bytes)),
        ("Used", _size(properties.used_bytes)),
        ("Free", _size(properties.free_bytes)),
    ]
    if properties.reserved_bytes:
        rows.append(("Reserved tail", _size(properties.reserved_bytes)))
    rows.append(("Validation", validation))
    return tuple(rows)


def mounted_file_property_rows(path: str | Path) -> tuple[tuple[str, str], ...]:
    """Read Acorn metadata exposed by an active AcornFS FUSE mount."""

    target = os.fspath(path)

    def value(name: str) -> str | None:
        try:
            return os.getxattr(target, name).decode("ascii")
        except (OSError, UnicodeDecodeError):
            return None

    source = value("user.acorn.source")
    if source != "adfs":
        return ()
    rows = [("Source filesystem", "ADFS")]
    labels = (
        ("user.acorn.path", "Original pathname"),
        ("user.acorn.load", "Load address"),
        ("user.acorn.execute", "Execute address"),
        ("user.acorn.filetype", "RISC OS filetype"),
        ("user.acorn.locked", "Locked"),
    )
    for attribute, label in labels:
        item = value(attribute)
        if item is not None:
            rows.append((label, item))
    return tuple(rows)
