from pathlib import Path

import pytest

from acornfs.core import resolve_image
from acornfs.core.mmb import (
    MMB_SLOT_BYTES,
    MMB_SLOT_COUNT,
    MMB_STANDARD_BYTES,
    read_mmb_layout,
)
from acornfs.errors import UnsupportedImageError
from acornfs_nautilus.logic import image_capabilities
from tests.image_fixture import (
    corrupt_adfs_new_map,
    create_adfs_floppy,
    create_adfs_hard_disc,
    create_adfs_new_map_pair,
    create_beebscsi_image,
    create_dfs_floppy,
    create_mmb_image,
    create_romfs_image,
)


@pytest.mark.parametrize("format_name", ["s", "m", "l", "d", "e", "e+", "f", "f+", "g", "g+"])
def test_detects_adfs_floppies_from_content_and_geometry(tmp_path: Path, format_name: str) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name=format_name)

    resolved = resolve_image(image_path)

    assert resolved.kind == "adfs-floppy"
    assert resolved.filesystem == "adfs"
    assert resolved.geometry.label.startswith(f"ADFS {format_name.upper()}")
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.mount_read_write
    assert resolved.capabilities.validate
    assert resolved.capabilities.recover
    assert not resolved.capabilities.repair


def test_content_detection_does_not_depend_on_a_floppy_extension(tmp_path: Path) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name="e+", filename="renamed.bin")
    resolved = resolve_image(image_path)
    assert resolved.kind == "adfs-floppy"
    assert resolved.geometry.variant == "e+"


def test_corrupt_new_map_floppy_is_not_accepted(tmp_path: Path) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name="f")
    corrupt_adfs_new_map(image_path)

    with pytest.raises(UnsupportedImageError, match="not a supported"):
        resolve_image(image_path)


def test_detects_standalone_filecore_hard_disc_from_content(tmp_path: Path) -> None:
    image_path = create_adfs_hard_disc(tmp_path, filename="renamed.bin")

    resolved = resolve_image(image_path)

    assert resolved.kind == "adfs-hard-disc"
    assert resolved.filesystem == "adfs"
    assert resolved.geometry.label == "ADFS hard disc (CHS unavailable)"
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.properties
    assert resolved.capabilities.mount_read_write
    assert resolved.capabilities.validate


def test_valid_unpaired_old_map_dat_is_writable_without_invented_chs(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    dsc_path.unlink()

    resolved = resolve_image(dat_path)

    assert resolved.kind == "adfs-hard-disc"
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.mount_read_write


def test_new_map_dat_dsc_pair_receives_protected_standalone_write_capabilities(
    tmp_path: Path,
) -> None:
    dat_path, dsc_path = create_adfs_new_map_pair(tmp_path)

    resolved = resolve_image(dsc_path)

    assert resolved.kind == "adfs-hard-disc"
    assert resolved.primary_path == dat_path.resolve()
    assert resolved.companion_path == dsc_path.resolve()
    assert resolved.pair is None
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.properties
    assert resolved.capabilities.mount_read_write
    assert resolved.capabilities.validate
    assert not resolved.capabilities.repair


def test_corrupt_new_map_pair_cannot_fall_back_to_old_map_write_capabilities(
    tmp_path: Path,
) -> None:
    _dat_path, dsc_path = create_adfs_new_map_pair(tmp_path)
    corrupt_adfs_new_map(dsc_path.with_suffix(".dat"))

    with pytest.raises(UnsupportedImageError, match="invalid zone checks"):
        resolve_image(dsc_path)


@pytest.mark.parametrize("double_sided", [False, True])
def test_detects_dfs_floppies_with_write_capabilities(tmp_path: Path, double_sided: bool) -> None:
    image_path = create_dfs_floppy(tmp_path, double_sided=double_sided)

    resolved = resolve_image(image_path)

    assert resolved.kind == "dfs-floppy"
    assert resolved.filesystem == "acorn-dfs"
    assert len(resolved.geometry.surface_specs) == (2 if double_sided else 1)
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.mount_read_write
    assert resolved.capabilities.validate
    assert resolved.capabilities.properties


def test_dfs_content_detection_does_not_depend_on_extension(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, filename="catalogue.bin")
    assert resolve_image(image_path).kind == "dfs-floppy"


def test_detects_romfs_by_crc_validated_content_with_read_only_capabilities(
    tmp_path: Path,
) -> None:
    image_path = create_romfs_image(tmp_path, filename="renamed.bin")

    resolved = resolve_image(image_path)

    assert resolved.kind == "romfs-image"
    assert resolved.filesystem == "acorn-romfs"
    assert resolved.geometry.label == "8 KiB ROM"
    assert resolved.case_sensitive_names
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.properties
    assert not resolved.capabilities.mount_read_write
    assert not resolved.capabilities.validate
    assert not resolved.capabilities.repair


def test_romfs_with_a_broken_block_crc_is_not_accepted(tmp_path: Path) -> None:
    image_path = create_romfs_image(tmp_path)
    contents = bytearray(image_path.read_bytes())
    offset = contents.index(b"Hello from ROMFS")
    contents[offset] ^= 0x01
    image_path.write_bytes(contents)

    with pytest.raises(UnsupportedImageError, match="not a supported"):
        resolve_image(image_path)


def test_detects_watford_dfs_as_a_writable_dfs_profile(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, filesystem_name="watford-dfs")

    resolved = resolve_image(image_path)

    assert resolved.kind == "dfs-floppy"
    assert resolved.filesystem == "watford-dfs"
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.mount_read_write


def test_detects_standard_mmb_with_write_capabilities(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path)

    resolved = resolve_image(image_path)

    assert resolved.kind == "mmb-container"
    assert resolved.mmb_layout is not None
    assert [slot.index for slot in resolved.mmb_layout.slots] == [0, 42]
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.properties
    assert resolved.capabilities.mount_read_write
    assert resolved.capabilities.validate


def test_mmb_detection_is_structural_not_extension_driven(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path, filename="library.bin")
    assert resolve_image(image_path).kind == "mmb-container"


def test_mmb_extension_does_not_override_valid_dfs_content(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, filename="renamed.mmb")

    resolved = resolve_image(image_path)

    assert resolved.kind == "dfs-floppy"
    assert resolved.filesystem == "acorn-dfs"


def test_malformed_mmb_files_are_rejected_clearly(tmp_path: Path) -> None:
    malformed = create_mmb_image(tmp_path, filename="malformed.mmb")
    with malformed.open("r+b") as handle:
        handle.seek(16 + 15)
        handle.write(b"\x55")
    with pytest.raises(UnsupportedImageError, match="unknown catalogue status"):
        resolve_image(malformed)


def test_detects_every_declared_extended_mmb_extent_from_content(tmp_path: Path) -> None:
    image_path = create_mmb_image(
        tmp_path,
        filename="extended.bin",
        extent_count=2,
        slot_indexes=(0, 510, 511, 1021),
        boot_slots=(0, 510, 511, 1021),
    )

    resolved = resolve_image(image_path)

    assert resolved.kind == "mmb-container"
    assert resolved.mmb_layout is not None
    assert resolved.mmb_layout.extent_count == 2
    assert resolved.mmb_layout.total_slots == 2 * MMB_SLOT_COUNT
    assert resolved.mmb_layout.boot_slots == (0, 510, 511, 1021)
    assert [slot.index for slot in resolved.mmb_layout.slots] == [0, 510, 511, 1021]
    assert [slot.display_name for slot in resolved.mmb_layout.slots] == [
        "0000 - SLOT 0",
        "0510 - SLOT 510",
        "0511 - SLOT 511",
        "1021 - SLOT 1021",
    ]
    assert resolved.capabilities.mount_read_only
    assert resolved.capabilities.mount_read_write


@pytest.mark.parametrize("extent_count", [2, 16])
def test_extended_mmb_accepts_its_exact_declared_sparse_length(
    tmp_path: Path, extent_count: int
) -> None:
    image_path = create_mmb_image(
        tmp_path,
        filename=f"extended-{extent_count}.mmb",
        extent_count=extent_count,
        slot_indexes=(),
    )

    layout = read_mmb_layout(image_path)

    assert layout.extent_count == extent_count
    assert layout.total_slots == extent_count * MMB_SLOT_COUNT


@pytest.mark.parametrize("delta", [-1, 1])
def test_extended_mmb_rejects_length_that_disagrees_with_first_header(
    tmp_path: Path, delta: int
) -> None:
    image_path = create_mmb_image(tmp_path, extent_count=2, slot_indexes=())
    with image_path.open("r+b") as handle:
        handle.truncate(2 * MMB_STANDARD_BYTES + delta)

    with pytest.raises(UnsupportedImageError, match="declares 2 extent"):
        resolve_image(image_path)


def test_extended_mmb_validates_secondary_catalogue_statuses(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path, extent_count=2, slot_indexes=())
    with image_path.open("r+b") as handle:
        handle.seek(MMB_STANDARD_BYTES + (42 + 1) * 16 + 15)
        handle.write(b"\x55")

    with pytest.raises(UnsupportedImageError, match="slot 553.*unknown catalogue status"):
        resolve_image(image_path)


def test_extended_mmb_rejects_global_boot_slot_outside_declared_extents(
    tmp_path: Path,
) -> None:
    image_path = create_mmb_image(tmp_path, extent_count=2, slot_indexes=())
    with image_path.open("r+b") as handle:
        handle.seek(0)
        handle.write((2 * MMB_SLOT_COUNT).to_bytes(2, "little")[:1])
        handle.seek(4)
        handle.write((2 * MMB_SLOT_COUNT).to_bytes(2, "little")[1:])

    with pytest.raises(UnsupportedImageError, match="boot configuration.*1022 slots"):
        resolve_image(image_path)


def test_extended_mmb_requires_dfs_evidence_in_each_populated_extent(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path, extent_count=2, slot_indexes=(0, 511))
    with image_path.open("r+b") as handle:
        handle.seek(MMB_STANDARD_BYTES + 8192)
        handle.write(b"\xaa" * MMB_SLOT_BYTES)

    with pytest.raises(UnsupportedImageError, match="slot 511.*recognisable DFS"):
        resolve_image(image_path)


def test_mmb_slot_statuses_select_only_formatted_entries(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path)
    with image_path.open("r+b") as handle:
        handle.seek((42 + 1) * 16 + 15)
        handle.write(b"\x00")  # locked, but still formatted and readable
        handle.seek((10 + 1) * 16 + 15)
        handle.write(b"\xff")  # invalid and hidden

    resolved = resolve_image(image_path)

    assert resolved.mmb_layout is not None
    assert [(slot.index, slot.status) for slot in resolved.mmb_layout.slots] == [
        (0, 0x0F),
        (42, 0x00),
    ]


def test_zero_filled_file_of_mmb_length_is_not_accepted_as_a_container(tmp_path: Path) -> None:
    image_path = tmp_path / "not-an-image.mmb"
    with image_path.open("wb") as handle:
        handle.truncate(MMB_STANDARD_BYTES)

    with pytest.raises(UnsupportedImageError, match="does not contain recognisable DFS"):
        resolve_image(image_path)


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


def test_adf_detection_probes_only_adfs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    image = tmp_path / "ambiguous.adf"
    image.write_bytes(bytes(800 * 1024))
    probed: list[set[str]] = []

    def identify(_path: Path, *, filesystems: dict[str, object] | None = None) -> list[object]:
        probed.append(set(filesystems or {}))
        return []

    monkeypatch.setattr("acornfs.core.formats.identify", identify)

    with pytest.raises(UnsupportedImageError):
        resolve_image(image)
    assert probed == [{"adfs"}]


def test_detection_failures_do_not_escape_into_nautilus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "hostile.img"
    image_path.write_bytes(b"input")
    monkeypatch.setattr("acornfs.core.formats.identify", lambda _path, **_kwargs: 1 / 0)

    assert image_capabilities(image_path) is None
