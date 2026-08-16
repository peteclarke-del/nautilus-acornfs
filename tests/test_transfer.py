from pathlib import Path
from unittest.mock import patch

import pytest
from oaknut.file import AcornMeta

from acornfs.core import export_file, import_file
from acornfs.core.image import ROOT_INODE, ImageNode, ReadOnlyImage
from acornfs.core.transfer import format_inf_record, parse_inf_record
from acornfs.errors import AcornFSError
from tests.image_fixture import create_beebscsi_image


def test_inf_record_round_trips_quoted_path_and_locked_metadata() -> None:
    node = ImageNode(2, 1, b"MY FILE", "$.MY FILE", False, 0x1234)
    metadata = AcornMeta(load_address=0xFFFF1900, exec_address=0xFFFF8023, access=0x08)

    encoded = format_inf_record(node, metadata)
    decoded = parse_inf_record(encoded)

    assert encoded == '"$.MY FILE" FFFF1900 FFFF8023 00001234 Locked\n'
    assert decoded.name == "$.MY FILE"
    assert decoded.length == 0x1234
    assert decoded.metadata.load_address == 0xFFFF1900
    assert decoded.metadata.exec_address == 0xFFFF8023
    assert int(decoded.metadata.access or 0) & 0x08


@pytest.mark.parametrize(
    "record",
    [
        "FILE 1900 8023 Locked",
        "FILE &1900 0x8023 00000001 L",
        "0 ffff1900 ffff8023 17",
    ],
)
def test_inf_record_accepts_shared_legacy_forms(record: str) -> None:
    parsed = parse_inf_record(record)
    assert parsed.metadata.load_address is not None
    assert parsed.metadata.exec_address is not None


def test_inf_record_rejects_access_values_wider_than_one_byte() -> None:
    with pytest.raises(AcornFSError, match="8 bits"):
        parse_inf_record("0 1900 8023 100")


def test_export_then_import_preserves_content_and_metadata(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(images)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert docs is not None
        guide = image.lookup(docs.inode, b"GUIDE")
        assert guide is not None
        image.set_acorn_metadata(
            guide.inode,
            load_address=0xFFFF1900,
            exec_address=0xFFFF8023,
            locked=True,
        )

    target = tmp_path / "GUIDE"
    exported = export_file(dat_path, "$.DOCS.GUIDE", target)
    assert exported.data_path.read_bytes() == b"Nested file\r"
    assert "FFFF1900 FFFF8023 0000000C Locked" in exported.sidecar_path.read_text()

    imported = import_file(dat_path, target, name="COPY")
    assert imported.metadata_source == "INF sidecar GUIDE.inf"
    with ReadOnlyImage.open(dat_path) as image:
        copied = image.lookup(ROOT_INODE, b"COPY")
        assert copied is not None
        assert copied.acorn_path == imported.node.acorn_path
        assert image.read(copied.inode, 0, copied.size) == b"Nested file\r"
        metadata = image.acorn_metadata(copied.inode)
        assert metadata.load_address == 0xFFFF1900
        assert metadata.exec_address == 0xFFFF8023
        assert copied.locked


def test_export_refuses_either_collision_without_partial_output(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(images)
    target = tmp_path / "README"
    sidecar = tmp_path / "README.INF"
    sidecar.write_text("keep", encoding="ascii")

    with pytest.raises(AcornFSError, match="overwrite"):
        export_file(dat_path, "$.README", target)

    assert not target.exists()
    assert sidecar.read_text(encoding="ascii") == "keep"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["README.INF", "images"]


def test_export_removes_temporary_files_after_premature_image_eof(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(images)
    target = tmp_path / "README"

    with (
        patch.object(ReadOnlyImage, "read", return_value=b""),
        pytest.raises(AcornFSError, match="ended at 0 bytes"),
    ):
        export_file(dat_path, "$.README", target)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["images"]


def test_import_rejects_mismatched_inf_length_before_image_write(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(images)
    source = tmp_path / "PROGRAM"
    source.write_bytes(b"payload")
    source.with_name("PROGRAM.inf").write_text(
        "$.PROGRAM 00001900 00008023 00000008\n", encoding="ascii"
    )
    before = dat_path.read_bytes()

    with pytest.raises(AcornFSError, match="does not match"):
        import_file(dat_path, source)

    assert dat_path.read_bytes() == before


def test_import_uses_shared_encoded_filename_when_no_inf_exists(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(images)
    source = tmp_path / "PROGRAM,1900-8023"
    source.write_bytes(b"payload")

    imported = import_file(dat_path, source)

    assert imported.node.name == b"PROGRAM"
    assert imported.metadata_source == "encoded host filename"
    with ReadOnlyImage.open(dat_path) as image:
        node = image.lookup(ROOT_INODE, b"PROGRAM")
        assert node is not None
        metadata = image.acorn_metadata(node.inode)
        assert metadata.load_address == 0x1900
        assert metadata.exec_address == 0x8023


def test_import_refuses_file_larger_than_free_space_before_reading(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(images)
    with ReadOnlyImage.open(dat_path) as image:
        too_large = image.free_bytes + 1
    source = tmp_path / "TOOBIG"
    with source.open("wb") as handle:
        handle.truncate(too_large)
    before = dat_path.read_bytes()

    with pytest.raises(AcornFSError, match="bytes free"):
        import_file(dat_path, source)

    assert dat_path.read_bytes() == before


def test_import_rolls_back_content_and_metadata_together(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    def fail_after_import(stage: str) -> None:
        if stage == "import.after":
            raise RuntimeError("injected import failure")

    with ReadOnlyImage.open(dat_path, writable=True, fault_injector=fail_after_import) as image:
        with pytest.raises(RuntimeError, match="injected"):
            image.import_file(
                ROOT_INODE,
                b"IMPORTED",
                b"payload",
                AcornMeta(load_address=0x1900, exec_address=0x8023, access=0x08),
            )
        assert image.lookup(ROOT_INODE, b"IMPORTED") is None
        image.create_file(ROOT_INODE, b"STILLGOOD")
