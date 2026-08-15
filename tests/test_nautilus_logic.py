from pathlib import Path

from acornfs_nautilus.logic import is_supported_image, mounted_file_property_rows
from tests.image_fixture import create_beebscsi_image


def test_only_complete_pair_is_supported(tmp_path: Path) -> None:
    incomplete = tmp_path / "missing.dat"
    incomplete.touch()
    assert not is_supported_image(incomplete)
    dat_path, dsc_path = create_beebscsi_image(tmp_path, stem="complete")
    assert is_supported_image(dat_path)
    assert is_supported_image(dsc_path)


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
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "os.getxattr", lambda _path, name: attributes[name]
    )

    rows = dict(mounted_file_property_rows(path))
    assert rows == {
        "Source filesystem": "ADFS",
        "Original pathname": "$.README",
        "Load address": "FFF12345",
        "Execute address": "00001234",
        "RISC OS filetype": "FFD",
        "Locked": "1",
    }
