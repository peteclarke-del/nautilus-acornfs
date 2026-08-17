from pathlib import Path

import pytest

from acornfs.core import resolve_image
from acornfs.errors import UnsupportedImageError
from acornfs_nautilus.logic import image_capabilities
from tests.image_fixture import create_adfs_floppy, create_beebscsi_image, create_dfs_floppy


@pytest.mark.parametrize("format_name", ["s", "m", "l"])
def test_detects_adfs_floppies_from_content_and_geometry(tmp_path: Path, format_name: str) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name=format_name)

    resolved = resolve_image(image_path)

    assert resolved.kind == "adfs-floppy"
    assert resolved.filesystem == "adfs"
    assert resolved.geometry.label.startswith(f"ADFS {format_name.upper()}")
    assert resolved.capabilities.mount_read_only
    assert not resolved.capabilities.mount_read_write
    assert not resolved.capabilities.validate
    assert not resolved.capabilities.repair


def test_content_detection_does_not_depend_on_a_floppy_extension(tmp_path: Path) -> None:
    image_path = create_adfs_floppy(tmp_path, filename="renamed.bin")
    assert resolve_image(image_path).kind == "adfs-floppy"


@pytest.mark.parametrize("double_sided", [False, True])
def test_detects_dfs_floppies_with_read_only_capabilities(
    tmp_path: Path, double_sided: bool
) -> None:
    image_path = create_dfs_floppy(tmp_path, double_sided=double_sided)

    resolved = resolve_image(image_path)

    assert resolved.kind == "dfs-floppy"
    assert resolved.filesystem == "acorn-dfs"
    assert len(resolved.geometry.surface_specs) == (2 if double_sided else 1)
    assert resolved.capabilities.mount_read_only
    assert not resolved.capabilities.mount_read_write
    assert not resolved.capabilities.validate
    assert resolved.capabilities.properties


def test_dfs_content_detection_does_not_depend_on_extension(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, filename="catalogue.bin")
    assert resolve_image(image_path).kind == "dfs-floppy"


def test_detects_watford_dfs_as_a_read_only_dfs_profile(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, filesystem_name="watford-dfs")

    resolved = resolve_image(image_path)

    assert resolved.kind == "dfs-floppy"
    assert resolved.filesystem == "watford-dfs"
    assert resolved.capabilities.mount_read_only
    assert not resolved.capabilities.mount_read_write


def test_complete_beebscsi_pair_has_precedence_over_standalone_content(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)

    resolved = resolve_image(dat_path)

    assert resolved.kind == "beebscsi-adfs"
    assert resolved.primary_path == dat_path.resolve()
    assert resolved.companion_path == dsc_path.resolve()
    assert resolved.capabilities.mount_read_write
    assert resolved.capabilities.validate


def test_unrecognised_and_unsupported_images_are_rejected(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.img"
    unknown.write_bytes(b"not a filesystem")
    with pytest.raises(UnsupportedImageError, match="not a supported"):
        resolve_image(unknown)


def test_detection_failures_do_not_escape_into_nautilus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "hostile.img"
    image_path.write_bytes(b"input")
    monkeypatch.setattr("acornfs.core.formats.identify", lambda _path: 1 / 0)

    assert image_capabilities(image_path) is None
