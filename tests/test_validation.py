from collections.abc import Callable
from pathlib import Path

import pytest

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.core.validation import FindingSeverity, validate_image_report
from acornfs.errors import AcornFSError
from acornfs.recovery import pending_recovery
from tests.image_fixture import create_beebscsi_image


def _old_map_checksum(data: bytearray, start: int) -> int:
    total = 0
    carry = 0
    for offset in range(0xFE, -1, -1):
        total += data[start + offset] + carry
        if total > 0xFF:
            carry = 1
            total &= 0xFF
        else:
            carry = 0
    return total


def _rewrite_map(dat_path: Path, mutation: Callable[[bytearray], None]) -> None:
    data = bytearray(dat_path.read_bytes())
    mutation(data)
    data[0xFF] = 0
    data[0x1FF] = 0
    data[0xFF] = _old_map_checksum(data, 0)
    data[0x1FF] = _old_map_checksum(data, 0x100)
    dat_path.write_bytes(data)


def _set_root_entry_start(dat_path: Path, name: str, start_sector: int) -> None:
    with ReadOnlyImage.open(dat_path) as image:
        adfs = image._mount._adfs  # type: ignore[attr-defined]
        root = adfs._read_root_directory()
        index = next(index for index, entry in enumerate(root.entries) if entry.name == name)
    offset = 2 * 256 + 5 + index * 26 + 0x16
    with dat_path.open("r+b") as handle:
        handle.seek(offset)
        handle.write(start_sector.to_bytes(3, "little"))


def _set_root_entry_length(dat_path: Path, name: str, length: int) -> None:
    with ReadOnlyImage.open(dat_path) as image:
        adfs = image._mount._adfs  # type: ignore[attr-defined]
        root = adfs._read_root_directory()
        index = next(index for index, entry in enumerate(root.entries) if entry.name == name)
    offset = 2 * 256 + 5 + index * 26 + 0x12
    with dat_path.open("r+b") as handle:
        handle.seek(offset)
        handle.write(length.to_bytes(4, "little"))


def _codes(dat_path: Path) -> set[str]:
    return {finding.code for finding in validate_image_report(dat_path).findings}


def test_clean_image_has_complete_extent_accounting(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    report = validate_image_report(dat_path)
    assert report.safe_for_write
    assert report.findings == ()
    assert report.used_sectors is not None
    assert report.free_sectors is not None
    assert report.adfs_sectors == report.used_sectors + report.free_sectors

    empty, _dsc_path = create_beebscsi_image(tmp_path, stem="empty", populated=False)
    empty_report = validate_image_report(empty)
    assert empty_report.safe_for_write
    assert empty_report.used_sectors == 7
    assert empty_report.free_sectors == 5273


def test_free_space_overlapping_allocated_data_is_fatal(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    def overlap(data: bytearray) -> None:
        data[0:3] = (7).to_bytes(3, "little")

    _rewrite_map(dat_path, overlap)
    report = validate_image_report(dat_path)
    assert not report.safe_for_write
    assert "extent.free_used_overlap" in {item.code for item in report.fatal_findings}


def test_file_extent_overlap_and_out_of_range_are_fatal(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    _set_root_entry_start(dat_path, "README", 8)
    assert "extent.used_overlap" in _codes(dat_path)

    dat_path, _dsc_path = create_beebscsi_image(tmp_path, stem="outside")
    _set_root_entry_start(dat_path, "README", 5280)
    assert "extent.used_out_of_range" in _codes(dat_path)


def test_unaccounted_sector_range_is_fatal(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    def lose_last_sector(data: bytearray) -> None:
        length = int.from_bytes(data[0x100:0x103], "little")
        data[0x100:0x103] = (length - 1).to_bytes(3, "little")

    _rewrite_map(dat_path, lose_last_sector)
    assert "extent.unaccounted" in _codes(dat_path)


def test_invalid_descriptor_is_a_classified_fatal_finding(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    dsc_path.write_bytes(b"bad")
    report = validate_image_report(dat_path)
    assert not report.safe_for_write
    assert report.fatal_findings[0].code == "geometry.descriptor_invalid"


def test_warning_and_reserved_tail_advice_do_not_block_writes(tmp_path: Path) -> None:
    warning_dat, _dsc_path = create_beebscsi_image(tmp_path, stem="warning")
    _set_root_entry_length(warning_dat, "DOCS", 0)
    warning_report = validate_image_report(warning_dat)
    assert warning_report.safe_for_write
    assert warning_report.warning_findings[0].code == "directory.length_unusual"
    with ReadOnlyImage.open(warning_dat, writable=True):
        pass

    advice_dat, _dsc_path = create_beebscsi_image(tmp_path, stem="advice")

    def reserve_tail(data: bytearray) -> None:
        old_size = int.from_bytes(data[0xFC:0xFF], "little")
        free_length = int.from_bytes(data[0x100:0x103], "little")
        data[0xFC:0xFF] = (old_size - 1).to_bytes(3, "little")
        data[0x100:0x103] = (free_length - 1).to_bytes(3, "little")

    _rewrite_map(advice_dat, reserve_tail)
    advice_report = validate_image_report(advice_dat)
    assert advice_report.safe_for_write
    assert advice_report.advice_findings[0].code == "geometry.reserved_tail"
    with ReadOnlyImage.open(advice_dat, writable=True):
        pass


def test_writable_gate_refuses_overlap_without_creating_checkpoint(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    _set_root_entry_start(dat_path, "README", 8)
    with pytest.raises(AcornFSError, match="Writable mount refused"):
        ReadOnlyImage.open(dat_path, writable=True)
    assert pending_recovery(dat_path) is None
    with ReadOnlyImage.open(dat_path) as image:
        assert image.lookup(ROOT_INODE, b"README") is not None


def test_fragmented_and_completely_full_images_remain_valid(tmp_path: Path) -> None:
    fragmented, _dsc_path = create_beebscsi_image(tmp_path, stem="fragmented")
    with ReadOnlyImage.open(fragmented, writable=True) as image:
        first = image.create_file(ROOT_INODE, b"FIRST")
        image.replace_file(first.inode, b"a" * 4096)
        middle = image.create_file(ROOT_INODE, b"MIDDLE")
        image.replace_file(middle.inode, b"b" * 4096)
        last = image.create_file(ROOT_INODE, b"LAST")
        image.replace_file(last.inode, b"c" * 4096)
        image.remove(ROOT_INODE, b"MIDDLE", directory=False)
    fragmented_report = validate_image_report(fragmented)
    assert fragmented_report.safe_for_write
    assert not fragmented_report.fatal_findings

    nearly_full, _dsc_path = create_beebscsi_image(tmp_path, stem="nearly-full")
    with ReadOnlyImage.open(nearly_full, writable=True) as image:
        filler = image.create_file(ROOT_INODE, b"FILLER")
        image.replace_file(filler.inode, b"x" * (image.free_bytes - 256))
    nearly_full_report = validate_image_report(nearly_full)
    assert nearly_full_report.safe_for_write
    assert nearly_full_report.free_sectors == 1

    full, _dsc_path = create_beebscsi_image(tmp_path, stem="full")
    with ReadOnlyImage.open(full, writable=True) as image:
        filler = image.create_file(ROOT_INODE, b"FILLER")
        image.replace_file(filler.inode, b"x" * image.free_bytes)
    full_report = validate_image_report(full)
    assert full_report.safe_for_write
    assert full_report.free_sectors == 0
    assert all(item.severity is not FindingSeverity.FATAL for item in full_report.findings)
