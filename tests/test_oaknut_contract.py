import tomllib
from importlib.metadata import version
from pathlib import Path

from acornfs.core.image import ReadOnlyImage
from tests.image_fixture import create_beebscsi_image


def test_oaknut_family_remains_one_exactly_pinned_release() -> None:
    configuration = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = configuration["project"]["dependencies"]
    oaknut = [
        dependency.partition("==")
        for dependency in dependencies
        if dependency.startswith("oaknut-")
    ]

    assert oaknut
    assert all(separator == "==" and pinned for _package, separator, pinned in oaknut)
    assert {pinned for _package, _separator, pinned in oaknut} == {"12.13.1"}
    assert {version(package) for package, _separator, _pinned in oaknut} == {"12.13.1"}


def test_pinned_old_adfs_private_adapter_contract(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    with ReadOnlyImage.open(dat_path) as image:
        adfs = image._mount._adfs  # type: ignore[attr-defined]
        free_space_map = adfs._fsm
        _parent, entry = adfs.path("$.README")._resolve()

        assert callable(adfs._resolve_parent)
        assert callable(adfs._read_directory_at)
        assert callable(adfs._read_root_directory)
        assert callable(adfs._disc.sector_range)
        assert adfs._dir_format.size_in_sectors > 0
        assert adfs._dir_format.size_in_bytes > 0
        assert len(free_space_map._data) > 0x1FC
        assert callable(free_space_map._data.__getitem__)
        assert callable(free_space_map._data.__setitem__)
        assert callable(free_space_map._recalculate_checksums)
        assert entry is not None
        assert hasattr(entry, "indirect_disc_address")
