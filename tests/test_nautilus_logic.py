from pathlib import Path

from acornfs.core import image_capabilities_hint
from acornfs_nautilus.logic import (
    image_capabilities,
    is_supported_image,
    mounted_file_property_rows,
)
from tests.image_fixture import (
    create_adfs_floppy,
    create_adfs_hard_disc,
    create_beebscsi_image,
    create_dfs_floppy,
    create_mmb_image,
    create_romfs_image,
)


def test_only_complete_pair_is_supported(tmp_path: Path) -> None:
    incomplete = tmp_path / "missing.dat"
    incomplete.touch()
    assert not is_supported_image(incomplete)
    dat_path, dsc_path = create_beebscsi_image(tmp_path, stem="complete")
    assert is_supported_image(dat_path)
    assert is_supported_image(dsc_path)


def test_menu_capability_hint_does_not_open_the_image(tmp_path: Path) -> None:
    missing = tmp_path / "offline.adl"

    capabilities = image_capabilities_hint(missing)

    assert capabilities is not None
    assert capabilities.mount_read_write
    assert image_capabilities_hint(tmp_path / "ordinary.txt") is None


def test_adfs_floppy_support_exposes_protected_write_actions(tmp_path: Path) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name="e+")
    capabilities = image_capabilities(image_path)
    assert capabilities is not None
    assert capabilities.mount_read_only
    assert capabilities.properties
    assert capabilities.mount_read_write
    assert capabilities.validate
    assert not capabilities.repair
    assert capabilities.recover
    assert not capabilities.file_forge


def test_dfs_support_exposes_protected_write_actions(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, double_sided=True)
    capabilities = image_capabilities(image_path)
    assert capabilities is not None
    assert capabilities.mount_read_only
    assert capabilities.properties
    assert capabilities.mount_read_write
    assert capabilities.validate
    assert not capabilities.repair
    assert capabilities.recover
    assert not capabilities.file_forge


def test_filecore_hard_disc_support_exposes_protected_write_actions(tmp_path: Path) -> None:
    image_path = create_adfs_hard_disc(tmp_path)
    capabilities = image_capabilities(image_path)
    assert capabilities is not None
    assert capabilities.mount_read_only
    assert capabilities.properties
    assert capabilities.mount_read_write
    assert capabilities.validate
    assert not capabilities.repair
    assert capabilities.recover
    assert not capabilities.file_forge


def test_mmb_support_exposes_protected_write_actions(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path)
    capabilities = image_capabilities(image_path)
    assert capabilities is not None
    assert capabilities.mount_read_only
    assert capabilities.properties
    assert capabilities.mount_read_write
    assert capabilities.validate
    assert not capabilities.repair
    assert capabilities.recover
    assert not capabilities.file_forge


def test_extended_mmb_support_exposes_protected_write_actions(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path, extent_count=2, slot_indexes=(0, 511))
    capabilities = image_capabilities(image_path)
    assert capabilities is not None
    assert capabilities.mount_read_only
    assert capabilities.properties
    assert capabilities.mount_read_write
    assert capabilities.validate
    assert not capabilities.repair
    assert capabilities.recover
    assert not capabilities.file_forge


def test_romfs_support_exposes_only_safe_actions(tmp_path: Path) -> None:
    image_path = create_romfs_image(tmp_path)
    capabilities = image_capabilities(image_path)
    assert capabilities is not None
    assert capabilities.mount_read_only
    assert capabilities.properties
    assert not capabilities.mount_read_write
    assert not capabilities.validate
    assert not capabilities.repair
    assert not capabilities.recover
    assert not capabilities.file_forge


def test_mounted_file_properties_are_derived_from_acorn_xattrs(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "README"
    path.touch()
    attributes = {
        "user.acorn.source": b"adfs",
        "user.acorn.path": b"$.README",
        "user.acorn.load": b"FFF12345",
        "user.acorn.execute": b"00001234",
        "user.acorn.filetype": b"FFD",
        "user.acorn.locked": b"1",
    }

    def getxattr(_path: str, name: str) -> bytes:
        try:
            return attributes[name]
        except KeyError as exc:
            raise OSError(name) from exc

    monkeypatch.setattr("os.getxattr", getxattr)  # type: ignore[attr-defined]

    rows = dict(mounted_file_property_rows(path))
    assert rows == {
        "Source filesystem": "ADFS",
        "Original pathname": "$.README",
        "Load address": "FFF12345",
        "Execute address": "00001234",
        "RISC OS filetype": "FFD",
        "Locked": "1",
    }


def test_mounted_dfs_file_properties_use_dfs_source_name(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "HELLO"
    path.touch()
    attributes = {
        "user.acorn.source": b"acorn-dfs",
        "user.acorn.path": b"$.HELLO",
        "user.acorn.load": b"00001900",
        "user.acorn.execute": b"00001900",
        "user.acorn.locked": b"0",
    }

    def getxattr(_path: str, name: str) -> bytes:
        try:
            return attributes[name]
        except KeyError as exc:
            raise OSError(name) from exc

    monkeypatch.setattr("os.getxattr", getxattr)  # type: ignore[attr-defined]

    rows = dict(mounted_file_property_rows(path))
    assert rows["Source filesystem"] == "Acorn DFS"
    assert rows["Original pathname"] == "$.HELLO"
    assert "RISC OS filetype" not in rows


def test_mounted_romfs_file_properties_include_run_only(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "HELLO"
    path.touch()
    attributes = {
        "user.acorn.source": b"acorn-romfs",
        "user.acorn.path": b"HELLO",
        "user.acorn.load": b"FFFF8000",
        "user.acorn.execute": b"FFFF8000",
        "user.acorn.locked": b"0",
        "user.acorn.run_only": b"1",
    }

    def getxattr(_path: str, name: str) -> bytes:
        try:
            return attributes[name]
        except KeyError as exc:
            raise OSError(name) from exc

    monkeypatch.setattr("os.getxattr", getxattr)  # type: ignore[attr-defined]

    rows = dict(mounted_file_property_rows(path))
    assert rows["Source filesystem"] == "Acorn ROMFS"
    assert rows["Original pathname"] == "HELLO"
    assert rows["Run-only"] == "1"
