import tomllib
from importlib.metadata import version
from pathlib import Path

from oaknut.filesystem import filesystem_names

from acornfs.core.image import ReadOnlyImage
from tests.image_fixture import create_adfs_floppy, create_beebscsi_image


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
    assert {pinned for _package, _separator, pinned in oaknut} == {"12.15.1"}
    assert {version(package) for package, _separator, _pinned in oaknut} == {"12.15.1"}
    assert "acorn-romfs" in filesystem_names()


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


def test_pinned_new_map_floppy_adapter_contract(tmp_path: Path) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name="e+")

    with ReadOnlyImage.open(image_path) as image:
        adfs = image._mount._adfs  # type: ignore[attr-defined]
        disc_record = adfs._map.disc_record

        assert adfs.is_new_map
        assert disc_record.disc_size == image_path.stat().st_size
        assert disc_record.nzones == 1
        assert disc_record.sector_size == 1024
        assert disc_record.uses_big_directories
        assert hasattr(disc_record, "disc_name")
        assert hasattr(disc_record, "disc_id")
        assert adfs._dir_format.size_in_bytes > 0
        assert callable(adfs._allocate_file_space)
        assert callable(adfs._release_object)
