from pathlib import Path

import pytest
from oaknut.adfs.exceptions import ADFSDirectoryFullError

from acornfs.core import ReadOnlyImage, validate_image_report
from acornfs.core.image import ROOT_INODE, _display_name
from acornfs.errors import AcornFSError
from tests.image_fixture import create_beebscsi_image


def test_deep_tree_indexes_at_configured_depth_boundary(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path, populated=False)
    depth = 64
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        parent = ROOT_INODE
        for level in range(depth):
            parent = image.make_directory(parent, f"D{level:02d}".encode("ascii")).inode
        leaf = image.create_file(parent, b"LEAF")
        image.replace_file(leaf.inode, b"deep data")

    with pytest.raises(AcornFSError, match="exceeds 63 levels"):
        ReadOnlyImage.open(dat_path, max_depth=depth - 1)
    with pytest.raises(AcornFSError, match="more than 65 entries"):
        ReadOnlyImage.open(dat_path, max_nodes=65)

    with ReadOnlyImage.open(dat_path, max_depth=depth, max_nodes=66) as image:
        parent = ROOT_INODE
        for level in range(depth):
            node = image.lookup(parent, f"d{level:02d}".encode("ascii"))
            assert node is not None and node.is_dir
            parent = node.inode
        leaf = image.lookup(parent, b"leaf")
        assert leaf is not None
        assert image.read(leaf.inode, 0, 1024) == b"deep data"

    assert validate_image_report(dat_path).safe_for_write


def test_old_directory_accepts_47_entries_and_refuses_the_48th(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path, populated=False)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        for index in range(47):
            image.create_file(ROOT_INODE, f"F{index:02d}".encode("ascii"))

        assert len(image.children[ROOT_INODE]) == 47
        with pytest.raises(ADFSDirectoryFullError, match="maximum 47"):
            image.create_file(ROOT_INODE, b"OVERFLOW")
        assert image.lookup(ROOT_INODE, b"OVERFLOW") is None

    with ReadOnlyImage.open(dat_path) as image:
        assert len(image.children[ROOT_INODE]) == 47
    assert validate_image_report(dat_path).safe_for_write


def test_boundary_names_and_reversible_display_mappings_round_trip(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path, populated=False)
    control_names = tuple(
        f"C{chr(0x2400 + codepoint)}".encode() for codepoint in range(1, 32) if codepoint != 13
    )
    displayed_names = (b"TENCHARS10", "A∕B".encode(), "D␡".encode(), *control_names)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        for name in displayed_names:
            image.create_file(ROOT_INODE, name)

    with ReadOnlyImage.open(dat_path) as image:
        assert {image.nodes[inode].name for inode in image.children[ROOT_INODE]} == set(
            displayed_names
        )
        for name in displayed_names:
            assert image.lookup(ROOT_INODE, name) is not None


def test_every_non_posix_display_mapping_is_unambiguous() -> None:
    raw = "/" + "".join(chr(codepoint) for codepoint in range(32)) + chr(127)
    expected = "∕" + "".join(chr(0x2400 + codepoint) for codepoint in range(32)) + "␡"

    assert _display_name(raw) == expected.encode()
    assert _display_name(".") == "．".encode()
    assert _display_name("..") == "．．".encode()


@pytest.mark.parametrize(
    "name",
    [
        b"",
        b"ELEVENCHARS",
        b"HAS.DOT",
        b"HAS:COLON",
        b"HAS\rRETURN",
        "HAS␀NUL".encode(),
        b"NONASCII\xff",
    ],
)
def test_invalid_boundary_names_are_rejected_without_catalogue_changes(
    tmp_path: Path, name: bytes
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path, populated=False)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        with pytest.raises((UnicodeError, ValueError)):
            image.create_file(ROOT_INODE, name)
        assert image.children[ROOT_INODE] == ()
