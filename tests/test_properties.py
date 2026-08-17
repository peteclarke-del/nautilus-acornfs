from pathlib import Path

import pytest

from acornfs.core import read_image_properties
from acornfs_nautilus.logic import image_property_rows
from tests.image_fixture import create_adfs_floppy, create_beebscsi_image


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
