"""Pure selection logic for the Nautilus extension."""

from __future__ import annotations

import os
from pathlib import Path

from acornfs.core import ImageCapabilities, ImageProperties, resolve_image
from acornfs.errors import AcornFSError
from acornfs.i18n import _, ngettext


def image_capabilities(path: str | Path) -> ImageCapabilities | None:
    try:
        return resolve_image(path).capabilities
    except AcornFSError:
        return None


def is_supported_image(path: str | Path) -> bool:
    return image_capabilities(path) is not None


def _size(value: int) -> str:
    units = (_("bytes"), _("KiB"), _("MiB"), _("GiB"))
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{int(amount)} {unit}" if unit == _("bytes") else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _property_value(value: str) -> str:
    """Translate known technical display values while preserving image-owned text."""

    values = {
        "BeebSCSI DAT/DSC pair": _("BeebSCSI DAT/DSC pair"),
        "Standalone ADFS floppy image": _("Standalone ADFS floppy image"),
        "ADFS old map": _("ADFS old map"),
        "Old directory (Hugo)": _("Old directory (Hugo)"),
        "New directory (Nick)": _("New directory (Nick)"),
        "Big directory": _("Big directory"),
        "BeebSCSI hard disc (BBC Master / RISC OS old-map ADFS)": _(
            "BeebSCSI hard disc (BBC Master / RISC OS old-map ADFS)"
        ),
        "Acorn ADFS floppy": _("Acorn ADFS floppy"),
    }
    return values.get(value, value)


def _boot_option(value: str) -> str:
    """Translate the known option name while retaining its stable numeric value."""

    name, separator, number = value.partition(" (")
    names = {
        "Off": _("Off"),
        "Load": _("Load"),
        "Run": _("Run"),
        "Exec": _("Exec"),
    }
    translated = names.get(name, name)
    return f"{translated} ({number}" if separator else translated


def image_property_rows(properties: ImageProperties) -> tuple[tuple[str, str], ...]:
    """Convert typed core metadata to stable labels for Nautilus."""

    if not properties.write_supported:
        validation = _("Supported read-only")
    else:
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
    if properties.geometry_kind == "floppy":
        sides = ngettext("{count} side", "{count} sides", properties.heads).format(
            count=properties.heads
        )
        geometry = _("{tracks} tracks × {sides} × {sectors} sectors/track").format(
            tracks=properties.cylinders,
            sides=sides,
            sectors=properties.sectors_per_track,
        )
    else:
        geometry = _("{cylinders} cylinders × {heads} heads × {sectors} sectors/track").format(
            cylinders=properties.cylinders,
            heads=properties.heads,
            sectors=properties.sectors_per_track,
        )
    rows = [
        (_("Image type"), _property_value(properties.image_type)),
        (_("Filesystem"), _property_value(properties.filesystem_format)),
        (_("Directory format"), _property_value(properties.directory_format)),
        (_("Hardware profile"), _property_value(properties.hardware_profile)),
        (_("Title"), properties.title or "—"),
        (_("Disc name"), properties.disc_name or "—"),
        (_("Disc cycle ID"), f"&{properties.disc_id:04X}"),
        (_("Boot option"), _boot_option(properties.boot_option)),
        (_("Geometry"), geometry),
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
