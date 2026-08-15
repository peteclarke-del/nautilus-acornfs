from pathlib import Path

from acornfs_nautilus.logic import is_supported_image
from tests.image_fixture import create_beebscsi_image


def test_only_complete_pair_is_supported(tmp_path: Path) -> None:
    incomplete = tmp_path / "missing.dat"
    incomplete.touch()
    assert not is_supported_image(incomplete)
    dat_path, dsc_path = create_beebscsi_image(tmp_path, stem="complete")
    assert is_supported_image(dat_path)
    assert is_supported_image(dsc_path)
