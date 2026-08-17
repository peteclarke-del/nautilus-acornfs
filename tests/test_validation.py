import shutil
from pathlib import Path

import pytest

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.core.validation import (
    COMPATIBILITY_PROFILE_ID,
    COMPATIBILITY_PROFILE_VERSION,
    VALIDATION_REPORT_SCHEMA_VERSION,
    FindingSeverity,
    validate_image_report,
)
from acornfs.errors import AcornFSError, OperationCancelled
from acornfs.recovery import pending_recovery
from tests.image_fixture import (
    create_beebscsi_image,
    reserve_adfs_tail,
    rewrite_old_map,
    set_root_entry_length,
    set_root_entry_start,
)


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


def test_validation_json_has_a_versioned_compatibility_contract(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    payload = validate_image_report(dat_path).as_dict()

    assert payload["schema_version"] == VALIDATION_REPORT_SCHEMA_VERSION
    assert payload["compatibility_profile"] == {
        "id": COMPATIBILITY_PROFILE_ID,
        "version": COMPATIBILITY_PROFILE_VERSION,
    }


def test_free_space_overlapping_allocated_data_is_fatal(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    def overlap(data: bytearray) -> None:
        data[0:3] = (7).to_bytes(3, "little")

    rewrite_old_map(dat_path, overlap)
    report = validate_image_report(dat_path)
    assert not report.safe_for_write
    assert "extent.free_used_overlap" in {item.code for item in report.fatal_findings}


def test_file_extent_overlap_and_out_of_range_are_fatal(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    set_root_entry_start(dat_path, "README", 8)
    assert "extent.used_overlap" in _codes(dat_path)

    dat_path, _dsc_path = create_beebscsi_image(tmp_path, stem="outside")
    set_root_entry_start(dat_path, "README", 5280)
    assert "extent.used_out_of_range" in _codes(dat_path)


def test_unaccounted_sector_range_is_fatal(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    def lose_last_sector(data: bytearray) -> None:
        length = int.from_bytes(data[0x100:0x103], "little")
        data[0x100:0x103] = (length - 1).to_bytes(3, "little")

    rewrite_old_map(dat_path, lose_last_sector)
    assert "extent.unaccounted" in _codes(dat_path)


def test_invalid_descriptor_is_a_classified_fatal_finding(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    dsc_path.write_bytes(b"bad")
    report = validate_image_report(dat_path)
    assert not report.safe_for_write
    assert report.fatal_findings[0].code == "geometry.descriptor_invalid"


def test_validation_text_is_localised_without_changing_stable_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    dsc_path.write_bytes(b"bad")
    monkeypatch.setattr("acornfs.core.validation._", lambda message: f"translated:{message}")
    monkeypatch.setattr(
        "acornfs.core.validation.ngettext",
        lambda singular, plural, count: f"plural:{singular if count == 1 else plural}",
    )

    report = validate_image_report(dat_path)

    assert report.fatal_findings[0].code == "geometry.descriptor_invalid"
    assert report.as_dict()["findings"][0]["code"] == "geometry.descriptor_invalid"
    assert report.fatal_findings[0].message.startswith("translated:")
    assert report.format_text().startswith("translated:Validation found")
    assert "plural:finding" in report.format_text()
    assert "[translated:FATAL]" in report.format_text()


def test_warning_and_reserved_tail_advice_do_not_block_writes(tmp_path: Path) -> None:
    warning_dat, _dsc_path = create_beebscsi_image(tmp_path, stem="warning")
    set_root_entry_length(warning_dat, "DOCS", 0)
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

    rewrite_old_map(advice_dat, reserve_tail)
    advice_report = validate_image_report(advice_dat)
    assert advice_report.safe_for_write
    assert advice_report.advice_findings[0].code == "geometry.reserved_tail"
    with ReadOnlyImage.open(advice_dat, writable=True):
        pass


def test_writable_gate_refuses_overlap_without_creating_checkpoint(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    set_root_entry_start(dat_path, "README", 8)
    with pytest.raises(AcornFSError, match="Writable mount refused"):
        ReadOnlyImage.open(dat_path, writable=True)
    assert pending_recovery(dat_path) is None
    with ReadOnlyImage.open(dat_path) as image:
        assert image.lookup(ROOT_INODE, b"README") is not None


def test_invalid_map_checksum_and_broken_directory_fail_cleanly(tmp_path: Path) -> None:
    bad_map, _dsc_path = create_beebscsi_image(tmp_path, stem="bad-map")
    with bad_map.open("r+b") as handle:
        handle.seek(0)
        original = handle.read(1)
        handle.seek(0)
        handle.write(bytes([original[0] ^ 0x01]))
    map_report = validate_image_report(bad_map)
    assert {item.code for item in map_report.fatal_findings} == {"adfs.open_failed"}
    with pytest.raises(AcornFSError, match="could not be opened safely"):
        ReadOnlyImage.open(bad_map)

    bad_directory, _dsc_path = create_beebscsi_image(tmp_path, stem="bad-directory")
    with ReadOnlyImage.open(bad_directory) as image:
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert docs is not None
        _parent, entry = image._mount._navigate(docs.acorn_path)._resolve()  # type: ignore[attr-defined]
        checksum_offset = entry.start_sector * 256 + (5 * 256 - 1)
    with bad_directory.open("r+b") as handle:
        handle.seek(checksum_offset)
        original = handle.read(1)
        handle.seek(checksum_offset)
        handle.write(bytes([original[0] ^ 0xFF]))
    directory_report = validate_image_report(bad_directory)
    assert "directory.unreadable" in {item.code for item in directory_report.fatal_findings}
    with pytest.raises(AcornFSError, match="could not be opened safely"):
        ReadOnlyImage.open(bad_directory)


def test_truncated_oversized_sparse_and_mismatched_pairs_are_classified(tmp_path: Path) -> None:
    truncated, _dsc_path = create_beebscsi_image(tmp_path, stem="truncated")
    with truncated.open("r+b") as handle:
        handle.truncate(truncated.stat().st_size - 256)
    assert "geometry.dat_short" in _codes(truncated)

    oversized, _dsc_path = create_beebscsi_image(tmp_path, stem="oversized")
    with oversized.open("ab") as handle:
        handle.write(bytes(256))
    assert "geometry.dat_oversized" in _codes(oversized)

    source, source_dsc = create_beebscsi_image(tmp_path, stem="source")
    sparse = tmp_path / "sparse.dat"
    sparse_dsc = tmp_path / "sparse.dsc"
    source_data = source.read_bytes()
    with sparse.open("wb") as handle:
        handle.truncate(len(source_data))
        for offset in range(0, len(source_data), 4096):
            block = source_data[offset : offset + 4096]
            if any(block):
                handle.seek(offset)
                handle.write(block)
    shutil.copyfile(source_dsc, sparse_dsc)
    assert validate_image_report(sparse).safe_for_write

    mismatched, mismatched_dsc = create_beebscsi_image(tmp_path, stem="mismatched")
    descriptor = bytearray(mismatched_dsc.read_bytes())
    descriptor[13:15] = (81).to_bytes(2, "big")
    mismatched_dsc.write_bytes(descriptor)
    assert "geometry.dat_missing_reserved_tail" in _codes(mismatched)


def test_trimmed_reserved_tail_is_repairable_but_blocks_ordinary_writes(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    capacity = dat_path.stat().st_size
    reserve_adfs_tail(dat_path, 128)
    with dat_path.open("r+b") as handle:
        handle.truncate(capacity - 128 * 256)

    report = validate_image_report(dat_path)
    assert report.safe_for_write
    assert {finding.code for finding in report.warning_findings} == {
        "geometry.dat_missing_reserved_tail"
    }
    with pytest.raises(AcornFSError, match="low-risk repair"):
        ReadOnlyImage.open(dat_path, writable=True)
    assert pending_recovery(dat_path) is None


def test_validation_can_cancel_during_structural_checks(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    calls = 0

    def cancel_during_scan() -> bool:
        nonlocal calls
        calls += 1
        return calls == 3

    with pytest.raises(OperationCancelled, match="cancelled safely"):
        validate_image_report(dat_path, cancelled=cancel_during_scan)
    assert calls == 3


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
