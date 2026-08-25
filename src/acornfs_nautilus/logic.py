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
        "ADFS DAT/DSC pair (New Map)": _("ADFS DAT/DSC pair (New Map)"),
        "Standalone ADFS floppy image": _("Standalone ADFS floppy image"),
        "Standalone ADFS hard-disc image": _("Standalone ADFS hard-disc image"),
        "DFS floppy image": _("DFS floppy image"),
        "HxC HFEv1 ADFS floppy image": _("HxC HFEv1 ADFS floppy image"),
        "HxC HFEv3 ADFS floppy image": _("HxC HFEv3 ADFS floppy image"),
        "HxC HFEv1 DFS floppy image": _("HxC HFEv1 DFS floppy image"),
        "HxC HFEv3 DFS floppy image": _("HxC HFEv3 DFS floppy image"),
        "Standard MMB container": _("Standard MMB container"),
        "Extended MMB container": _("Extended MMB container"),
        "Acorn ROMFS image": _("Acorn ROMFS image"),
        "ADFS old map": _("ADFS old map"),
        "ADFS new map": _("ADFS new map"),
        "Acorn DFS": _("Acorn DFS"),
        "Watford DFS": _("Watford DFS"),
        "MMB with Acorn DFS slots": _("MMB with Acorn DFS slots"),
        "Extended MMB with Acorn DFS slots": _("Extended MMB with Acorn DFS slots"),
        "Acorn ROMFS": _("Acorn ROMFS"),
        "Old directory (Hugo)": _("Old directory (Hugo)"),
        "New directory (Nick)": _("New directory (Nick)"),
        "Big directory": _("Big directory"),
        "Flat catalogue prefixes": _("Flat catalogue prefixes"),
        "Slots with flat DFS catalogue prefixes": _("Slots with flat DFS catalogue prefixes"),
        "Flat ROM catalogue": _("Flat ROM catalogue"),
        "BeebSCSI hard disc (BBC Master / RISC OS old-map ADFS)": _(
            "BeebSCSI hard disc (BBC Master / RISC OS old-map ADFS)"
        ),
        "Acorn ADFS floppy": _("Acorn ADFS floppy"),
        "RISC OS FileCore hard disc": _("RISC OS FileCore hard disc"),
        "BBC Micro DFS floppy": _("BBC Micro DFS floppy"),
        "BBC Micro MMC/SD MMB container": _("BBC Micro MMC/SD MMB container"),
        "BBC Micro / Acorn Electron paged ROM": _("BBC Micro / Acorn Electron paged ROM"),
        "ADFS size": _("ADFS size"),
        "DFS size": _("DFS size"),
        "Slot payload": _("Slot payload"),
        "File payload": _("File payload"),
        "Boot option": _("Boot option"),
        "Boot slots": _("Boot slots"),
        "Reserved tail": _("Reserved tail"),
        "Container catalogue": _("Container catalogue"),
        "Container catalogues": _("Container catalogues"),
        "511 × 200 KiB SSD slots": _("511 × 200 KiB SSD slots"),
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
    if properties.geometry_kind == "mmb" and properties.extent_count > 1:
        geometry = _("{extents} extents × 511 slots × 200 KiB").format(
            extents=properties.extent_count
        )
    elif properties.geometry_kind in {"container", "mmb"}:
        geometry = _property_value(properties.geometry_description)
    elif properties.geometry_kind == "floppy":
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
        (_("Geometry"), geometry),
        (_("Capacity"), _size(properties.capacity_bytes)),
        (_property_value(properties.filesystem_size_label), _size(properties.adfs_bytes)),
    ]
    if properties.show_boot_option:
        rows.insert(
            5, (_property_value(properties.boot_label), _boot_option(properties.boot_option))
        )
    if properties.show_space_breakdown:
        rows.extend(
            (
                (_("Used"), _size(properties.used_bytes)),
                (_("Free"), _size(properties.free_bytes)),
            )
        )
    if properties.show_disc_name:
        rows.insert(5, (_("Disc name"), properties.disc_name or "—"))
    if properties.show_disc_id:
        rows.insert(6, (_("Disc cycle ID"), f"&{properties.disc_id:04X}"))
    if properties.reserved_bytes:
        rows.append((_property_value(properties.reserved_label), _size(properties.reserved_bytes)))
    if properties.slot_count:
        if properties.extent_count > 1:
            rows.append((_("Extents"), str(properties.extent_count)))
        rows.append(
            (
                _("Formatted slots"),
                f"{properties.formatted_slots} / {properties.slot_count}",
            )
        )
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
    source_names = {
        "adfs": "ADFS",
        "acorn-dfs": "Acorn DFS",
        "watford-dfs": "Watford DFS",
        "acorn-romfs": "Acorn ROMFS",
    }
    if source not in source_names:
        return ()
    rows = [(_("Source filesystem"), _property_value(source_names[source]))]
    labels = (
        ("user.acorn.path", _("Original pathname")),
        ("user.acorn.load", _("Load address")),
        ("user.acorn.execute", _("Execute address")),
        ("user.acorn.filetype", _("RISC OS filetype")),
        ("user.acorn.locked", _("Locked")),
        ("user.acorn.run_only", _("Run-only")),
    )
    for attribute, label in labels:
        item = value(attribute)
        if item is not None:
            rows.append((label, item))
    return tuple(rows)
