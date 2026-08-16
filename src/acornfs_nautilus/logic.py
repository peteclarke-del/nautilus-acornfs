"""Pure selection logic for the Nautilus extension."""

from __future__ import annotations

import os
from pathlib import Path

from acornfs.core import ImageProperties, discover_pair
from acornfs.errors import PairDiscoveryError
from acornfs.i18n import _, ngettext


def is_supported_image(path: str | Path) -> bool:
    try:
        discover_pair(path)
    except PairDiscoveryError:
        return False
    return True


def _size(value: int) -> str:
    units = (_("bytes"), _("KiB"), _("MiB"), _("GiB"))
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{int(amount)} {unit}" if unit == _("bytes") else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def image_property_rows(properties: ImageProperties) -> tuple[tuple[str, str], ...]:
    """Convert typed core metadata to stable labels for Nautilus."""

    validation = _("Safe for read-write mounting") if properties.safe_for_write else _("Unsafe")
    if properties.warning_findings or properties.advice_findings:
        warnings = properties.warning_findings
        advice = properties.advice_findings
        warning_text = ngettext("{count} warning", "{count} warnings", warnings).format(
            count=warnings
        )
        advice_text = ngettext("{count} advice", "{count} advice", advice).format(count=advice)
        validation += _(" ({warnings}, {advice})").format(
            warnings=warning_text,
            advice=advice_text,
        )
    rows = [
        (_("Image type"), properties.image_type),
        (_("Filesystem"), properties.filesystem_format),
        (_("Directory format"), properties.directory_format),
        (_("Hardware profile"), properties.hardware_profile),
        (_("Title"), properties.title or "—"),
        (_("Disc name"), properties.disc_name or "—"),
        (_("Disc cycle ID"), f"&{properties.disc_id:04X}"),
        (_("Boot option"), properties.boot_option),
        (
            _("Geometry"),
            _("{cylinders} cylinders × {heads} heads × {sectors} sectors/track").format(
                cylinders=properties.cylinders,
                heads=properties.heads,
                sectors=properties.sectors_per_track,
            ),
        ),
        (_("Capacity"), _size(properties.capacity_bytes)),
        (_("ADFS size"), _size(properties.adfs_bytes)),
        (_("Used"), _size(properties.used_bytes)),
        (_("Free"), _size(properties.free_bytes)),
    ]
    if properties.reserved_bytes:
        rows.append((_("Reserved tail"), _size(properties.reserved_bytes)))
    rows.append((_("Validation"), validation))
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
    rows = [(_("Source filesystem"), "ADFS")]
    labels = (
        ("user.acorn.path", _("Original pathname")),
        ("user.acorn.load", _("Load address")),
        ("user.acorn.execute", _("Execute address")),
        ("user.acorn.filetype", _("RISC OS filetype")),
        ("user.acorn.locked", _("Locked")),
    )
    for attribute, label in labels:
        item = value(attribute)
        if item is not None:
            rows.append((label, item))
    return tuple(rows)
