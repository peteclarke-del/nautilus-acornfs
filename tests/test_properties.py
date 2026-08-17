from pathlib import Path
from unittest.mock import Mock

import pytest

from acornfs.core import read_image_properties
from acornfs.errors import AcornFSError
from acornfs_nautilus.logic import image_property_rows
from tests.image_fixture import (
    create_adfs_floppy,
    create_beebscsi_image,
    create_dfs_floppy,
    create_mmb_image,
    create_romfs_image,
)


def test_image_properties_report_format_geometry_and_validation(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    properties = read_image_properties(dsc_path)

    assert properties.dat_path == str(dat_path.resolve())
    assert properties.dsc_path == str(dsc_path.resolve())
    assert properties.image_type == "BeebSCSI DAT/DSC pair"
    assert properties.filesystem_format == "ADFS old map"
    assert properties.directory_format == "Old directory (Hugo)"
    assert properties.title == "ACORNFS"
    assert properties.cylinders == 80
    assert properties.heads == 2
    assert properties.sectors_per_track == 33
    assert properties.capacity_bytes == 80 * 2 * 33 * 256
    assert properties.used_bytes + properties.free_bytes == properties.adfs_bytes
    assert properties.safe_for_write
    assert dict(image_property_rows(properties))["Validation"].startswith("Safe")


@pytest.mark.parametrize(
    ("format_name", "tracks", "sides"), [("s", 40, 1), ("m", 80, 1), ("l", 80, 2)]
)
def test_adfs_floppy_properties_are_explicitly_read_only(
    tmp_path: Path, format_name: str, tracks: int, sides: int
) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name=format_name)

    properties = read_image_properties(image_path)

    assert properties.image_type == "Standalone ADFS floppy image"
    assert properties.cylinders == tracks
    assert properties.heads == sides
    assert properties.sectors_per_track == 16
    assert properties.capacity_bytes == tracks * sides * 16 * 256
    assert not properties.write_supported
    rows = dict(image_property_rows(properties))
    assert rows["Validation"] == "Supported read-only"
    assert rows["Geometry"] == (
        f"{tracks} tracks × {sides} {'side' if sides == 1 else 'sides'} × 16 sectors/track"
    )


@pytest.mark.parametrize("double_sided", [False, True])
def test_dfs_properties_cover_all_sides(tmp_path: Path, double_sided: bool) -> None:
    image_path = create_dfs_floppy(tmp_path, double_sided=double_sided)

    properties = read_image_properties(image_path)

    assert properties.image_type == "DFS floppy image"
    assert properties.filesystem_format == "Acorn DFS"
    assert properties.directory_format == "Flat catalogue prefixes"
    assert properties.heads == (2 if double_sided else 1)
    assert properties.capacity_bytes == image_path.stat().st_size
    assert properties.used_bytes + properties.free_bytes == properties.adfs_bytes
    assert not properties.write_supported
    rows = dict(image_property_rows(properties))
    assert rows["DFS size"] == rows["Capacity"]
    assert "Disc cycle ID" not in rows
    assert rows["Validation"] == "Supported read-only"


def test_dfs_properties_close_an_open_side_when_the_next_side_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = create_dfs_floppy(tmp_path, double_sided=True)
    first_side = Mock()
    filesystem = Mock()
    filesystem.open.side_effect = [first_side, RuntimeError("bad second catalogue")]
    monkeypatch.setattr("acornfs.core.properties.create_filesystem", lambda _name: filesystem)

    with pytest.raises(AcornFSError, match="bad second catalogue"):
        read_image_properties(image_path)

    first_side.close.assert_called_once_with()


def test_watford_dfs_properties_report_the_detected_variant(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, filesystem_name="watford-dfs")

    properties = read_image_properties(image_path)

    assert properties.filesystem_format == "Watford DFS"


def test_mmb_properties_report_slots_without_opening_every_payload(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path)

    properties = read_image_properties(image_path)

    assert properties.image_type == "Standard MMB container"
    assert properties.filesystem_format == "MMB with Acorn DFS slots"
    assert properties.slot_count == 511
    assert properties.formatted_slots == 2
    assert properties.used_bytes == 2 * 200 * 1024
    assert properties.free_bytes == 509 * 200 * 1024
    assert properties.reserved_bytes == 8192
    rows = dict(image_property_rows(properties))
    assert rows["Boot slots"] == "0, 1, 2, 3"
    assert rows["Geometry"] == "511 × 200 KiB SSD slots"
    assert rows["Formatted slots"] == "2 / 511"
    assert rows["Slot payload"] == "99.8 MiB"
    assert rows["Container catalogue"] == "8.0 KiB"
    assert "Disc name" not in rows
    assert "Disc cycle ID" not in rows
    assert rows["Validation"] == "Supported read-only"


def test_extended_mmb_properties_report_all_extents_and_global_capacity(tmp_path: Path) -> None:
    image_path = create_mmb_image(
        tmp_path,
        extent_count=2,
        slot_indexes=(0, 511, 1021),
        boot_slots=(0, 510, 511, 1021),
    )

    properties = read_image_properties(image_path)

    assert properties.image_type == "Extended MMB container"
    assert properties.filesystem_format == "Extended MMB with Acorn DFS slots"
    assert properties.extent_count == 2
    assert properties.slot_count == 1022
    assert properties.formatted_slots == 3
    assert properties.capacity_bytes == image_path.stat().st_size
    assert properties.reserved_bytes == 2 * 8192
    rows = dict(image_property_rows(properties))
    assert rows["Boot slots"] == "0, 510, 511, 1021"
    assert rows["Geometry"] == "2 extents × 511 slots × 200 KiB"
    assert rows["Extents"] == "2"
    assert rows["Formatted slots"] == "3 / 1022"
    assert rows["Container catalogues"] == "16.0 KiB"
    assert rows["Validation"] == "Supported read-only"


def test_romfs_properties_report_title_capacity_and_file_payload(tmp_path: Path) -> None:
    image_path = create_romfs_image(tmp_path)

    properties = read_image_properties(image_path)

    assert properties.image_type == "Acorn ROMFS image"
    assert properties.filesystem_format == "Acorn ROMFS"
    assert properties.directory_format == "Flat ROM catalogue"
    assert properties.title == "ACORNFS"
    assert properties.capacity_bytes == 8192
    assert properties.adfs_bytes == 63
    assert not properties.write_supported
    rows = dict(image_property_rows(properties))
    assert rows["Geometry"] == "8 KiB linear paged ROM"
    assert rows["File payload"] == "63 bytes"
    assert rows["Validation"] == "Supported read-only"
    assert "Boot option" not in rows
    assert "Used" not in rows
    assert "Free" not in rows
    assert "Disc name" not in rows


@pytest.mark.parametrize(("cylinders", "heads"), [(40, 1), (160, 2), (80, 4)])
def test_properties_follow_descriptor_geometry(tmp_path: Path, cylinders: int, heads: int) -> None:
    dat_path, _dsc_path = create_beebscsi_image(
        tmp_path, populated=False, cylinders=cylinders, heads=heads
    )
    properties = read_image_properties(dat_path)
    assert properties.cylinders == cylinders
    assert properties.heads == heads
    assert properties.capacity_bytes == cylinders * heads * 33 * 256


def test_known_property_values_are_localised_but_image_text_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    properties = read_image_properties(dat_path)
    monkeypatch.setattr("acornfs_nautilus.logic._", lambda message: f"translated:{message}")

    rows = dict(image_property_rows(properties))

    assert rows["translated:Image type"] == "translated:BeebSCSI DAT/DSC pair"
    assert rows["translated:Directory format"] == "translated:Old directory (Hugo)"
    assert rows["translated:Boot option"] == "translated:Off (0)"
    assert rows["translated:Title"] == "ACORNFS"
