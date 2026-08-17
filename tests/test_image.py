import os
from pathlib import Path
from unittest.mock import patch

import pytest
from oaknut.adfs.exceptions import ADFSDiscFullError
from oaknut.file import AcornMeta

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.errors import AcornFSError
from acornfs.recovery import recover_image
from tests.image_fixture import (
    create_adfs_floppy,
    create_beebscsi_image,
    create_dfs_floppy,
    create_mmb_image,
)


def test_indexes_nested_image_and_reads_files(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path) as image:
        root_names = {image.nodes[inode].name for inode in image.children[ROOT_INODE]}
        assert root_names == {b"DOCS", b"EMPTY", b"README"}
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert docs is not None
        guide = image.lookup(docs.inode, b"GUIDE")
        assert guide is not None
        assert image.read(guide.inode, 0, 1024) == b"Nested file\r"
        assert image.read(guide.inode, 7, 4) == b"file"


@pytest.mark.parametrize("format_name", ["s", "m", "l"])
def test_indexes_and_reads_standalone_adfs_floppies(tmp_path: Path, format_name: str) -> None:
    image_path = create_adfs_floppy(tmp_path, format_name=format_name)

    with ReadOnlyImage.open(image_path) as image:
        hello = image.lookup(ROOT_INODE, b"HELLO")
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert hello is not None
        assert docs is not None
        guide = image.lookup(docs.inode, b"GUIDE")
        assert guide is not None
        assert image.read(hello.inode, 0, 1024) == b"Hello from floppy\r"
        assert image.read(guide.inode, 0, 1024) == b"Floppy guide\r"
        assert image.source.kind == "adfs-floppy"
        assert not image.writable

    with pytest.raises(AcornFSError, match="Read-write mounting is not supported"):
        ReadOnlyImage.open(image_path, writable=True)


def test_indexes_dfs_catalogue_prefixes_as_directories(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path)

    with ReadOnlyImage.open(image_path) as image:
        root_names = {image.nodes[inode].name for inode in image.children[ROOT_INODE]}
        assert root_names == {b"$", b"A"}
        default = image.lookup(ROOT_INODE, b"$")
        catalogue_a = image.lookup(ROOT_INODE, b"A")
        assert default is not None
        assert catalogue_a is not None
        hello = image.lookup(default.inode, b"HELLO")
        notes = image.lookup(catalogue_a.inode, b"NOTES")
        assert hello is not None
        assert notes is not None
        assert image.read(hello.inode, 0, 1024) == b"Hello from DFS drive 0\r"
        assert image.acorn_metadata(hello.inode).load_address == 0
        assert image.source.filesystem == "acorn-dfs"

    with pytest.raises(AcornFSError, match="Read-write mounting is not supported"):
        ReadOnlyImage.open(image_path, writable=True)


def test_indexes_and_reads_watford_dfs(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, filesystem_name="watford-dfs")

    with ReadOnlyImage.open(image_path) as image:
        default = image.lookup(ROOT_INODE, b"$")
        assert default is not None
        hello = image.lookup(default.inode, b"HELLO")
        assert hello is not None
        assert image.read(hello.inode, 0, 1024) == b"Hello from DFS drive 0\r"
        assert image.source.filesystem == "watford-dfs"


def test_indexes_both_dsd_sides_as_drive_directories(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path, double_sided=True)

    with ReadOnlyImage.open(image_path) as image:
        assert [image.nodes[inode].name for inode in image.children[ROOT_INODE]] == [b"0", b"2"]
        drive_zero = image.lookup(ROOT_INODE, b"0")
        drive_two = image.lookup(ROOT_INODE, b"2")
        assert drive_zero is not None
        assert drive_two is not None
        zero_default = image.lookup(drive_zero.inode, b"$")
        two_default = image.lookup(drive_two.inode, b"$")
        assert zero_default is not None
        assert two_default is not None
        hello = image.lookup(zero_default.inode, b"HELLO")
        other = image.lookup(two_default.inode, b"OTHER")
        assert hello is not None
        assert other is not None
        assert image.read(hello.inode, 0, 1024) == b"Hello from DFS drive 0\r"
        assert image.read(other.inode, 0, 1024) == b"Hello from DFS drive 2\r"
        assert image.total_bytes == image_path.stat().st_size
        assert 0 < image.free_bytes < image.total_bytes


def test_indexes_mmb_slots_as_directories_and_reads_their_dfs_files(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path)

    with ReadOnlyImage.open(image_path) as image:
        root_names = [image.nodes[inode].name for inode in image.children[ROOT_INODE]]
        assert root_names == [b"000 - WELCOME", b"042 - UTILITIES"]
        slot_zero = image.lookup(ROOT_INODE, b"000 - WELCOME")
        slot_42 = image.lookup(ROOT_INODE, b"042 - UTILITIES")
        assert slot_zero is not None
        assert slot_42 is not None
        default_zero = image.lookup(slot_zero.inode, b"$")
        default_42 = image.lookup(slot_42.inode, b"$")
        assert default_zero is not None
        assert default_42 is not None
        hello_zero = image.lookup(default_zero.inode, b"HELLO")
        hello_42 = image.lookup(default_42.inode, b"HELLO")
        assert hello_zero is not None
        assert hello_42 is not None
        assert image.read(hello_zero.inode, 0, 1024) == b"Slot zero\r"
        assert image.read(hello_42.inode, 0, 1024) == b"Slot forty-two\r"
        assert image.acorn_metadata(hello_42.inode).load_address == 0
        assert image.total_bytes == image_path.stat().st_size
        assert image.free_bytes == 0

    with pytest.raises(AcornFSError, match="Read-write mounting is not supported"):
        ReadOnlyImage.open(image_path, writable=True)


def test_empty_mmb_mounts_with_an_empty_root(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path)
    with image_path.open("r+b") as handle:
        for index in (0, 42):
            handle.seek((index + 1) * 16 + 15)
            handle.write(b"\xf0")

    with ReadOnlyImage.open(image_path) as image:
        assert image.children[ROOT_INODE] == ()


def test_mmb_slot_mount_cache_stays_bounded(tmp_path: Path) -> None:
    image_path = create_mmb_image(tmp_path, slot_indexes=tuple(range(10)))

    with ReadOnlyImage.open(image_path) as image:
        mounts = image._mount._mounts  # type: ignore[attr-defined]
        assert len(mounts) == 8
        first_slot = image.lookup(ROOT_INODE, b"000 - SLOT 0")
        assert first_slot is not None
        default = image.lookup(first_slot.inode, b"$")
        assert default is not None
        hello = image.lookup(default.inode, b"HELLO")
        assert hello is not None
        assert image.read(hello.inode, 0, 1024) == b"Slot 0\r"
        assert len(mounts) == 8
        assert 0 in mounts


def test_adfs_lookup_is_case_insensitive_and_case_collisions_are_refused(
    tmp_path: Path,
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        readme = image.lookup(ROOT_INODE, b"readme")
        assert readme is not None
        assert readme.name == b"README"
        with pytest.raises(FileExistsError):
            image.create_file(ROOT_INODE, b"ReadMe")
        with pytest.raises(FileExistsError):
            image.make_directory(ROOT_INODE, b"readme")


def test_reports_filesystem_capacity(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path) as image:
        assert image.total_bytes > 0
        assert 0 < image.free_bytes < image.total_bytes


def test_open_is_read_only(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    before = dat_path.read_bytes()
    with ReadOnlyImage.open(dat_path) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert image.read(readme.inode, 0, 1024) == b"Hello from AcornFS\r"
    assert dat_path.read_bytes() == before


def test_writable_open_persists_replacement_and_locks_pair(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        image.replace_file(readme.inode, b"Changed through AcornFS\r")
        image.sync()
        with pytest.raises(AcornFSError, match="could not be opened safely"):
            ReadOnlyImage.open(dat_path, writable=True)
        with pytest.raises(AcornFSError, match="could not be opened safely"):
            ReadOnlyImage.open(dat_path)

    with ReadOnlyImage.open(dat_path) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert image.read(readme.inode, 0, 1024) == b"Changed through AcornFS\r"


def test_writable_namespace_operations_persist(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        created = image.create_file(ROOT_INODE, b"NEWFILE")
        image.replace_file(created.inode, b"new data")
        folder = image.make_directory(ROOT_INODE, b"NEWDIR")
        nested = image.create_file(folder.inode, b"NESTED")
        image.replace_file(nested.inode, b"nested data")
        moved = image.rename(folder.inode, b"NESTED", ROOT_INODE, b"MOVED")
        assert moved.inode == nested.inode
        image.remove(ROOT_INODE, b"NEWFILE", directory=False)
        image.remove(ROOT_INODE, b"NEWDIR", directory=True)

    with ReadOnlyImage.open(dat_path) as image:
        moved = image.lookup(ROOT_INODE, b"MOVED")
        assert moved is not None
        assert image.read(moved.inode, 0, 1024) == b"nested data"
        assert image.lookup(ROOT_INODE, b"NEWFILE") is None
        assert image.lookup(ROOT_INODE, b"NEWDIR") is None


def test_rename_replaces_existing_file_like_atomic_editor_save(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        temporary = image.create_file(ROOT_INODE, b"SAVEFILE")
        image.replace_file(temporary.inode, b"replacement")
        renamed = image.rename(ROOT_INODE, b"SAVEFILE", ROOT_INODE, b"README")
        assert renamed.inode == temporary.inode
        assert image.read(renamed.inode, 0, 1024) == b"replacement"

    with ReadOnlyImage.open(dat_path) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert image.read(readme.inode, 0, 1024) == b"replacement"


def test_external_dat_change_blocks_further_mutations(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    before = dat_path.stat()
    os.utime(dat_path, ns=(before.st_atime_ns, before.st_mtime_ns + 1))
    with pytest.raises(AcornFSError, match="changed outside AcornFS"):
        image.create_file(ROOT_INODE, b"BLOCKED")
    image.close()
    assert recover_image(dat_path, discard=True) == "Recovery checkpoint discarded."


def test_replacing_contents_preserves_acorn_metadata(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    expected = AcornMeta(load_address=0x12345678, exec_address=0xABCDEF01, access=3)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        image._mount.set_acorn_meta(readme.acorn_path, expected)  # type: ignore[attr-defined]
        image.sync()
        image.replace_file(readme.inode, b"new contents")
        assert image._mount.acorn_meta(readme.acorn_path) == expected  # type: ignore[attr-defined]

    with ReadOnlyImage.open(dat_path) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert image._mount.acorn_meta(readme.acorn_path) == expected  # type: ignore[attr-defined]


def test_unexpected_mutation_failure_rolls_back_and_keeps_session_usable(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    with (
        patch.object(image._mount, "write_bytes", side_effect=RuntimeError("injected failure")),
        pytest.raises(RuntimeError, match="injected failure"),
    ):
        image.create_file(ROOT_INODE, b"FAIL")
    assert image.lookup(ROOT_INODE, b"FAIL") is None
    image.create_file(ROOT_INODE, b"STILLGOOD")
    image.close()


def test_oversized_overwrite_is_rejected_before_freeing_original_file(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        original = image.read(readme.inode, 0, readme.size)
        with pytest.raises(ADFSDiscFullError, match="contiguous extent"):
            image.replace_file(readme.inode, b"x" * (image.free_bytes + 512))
        assert image.read(readme.inode, 0, readme.size) == original
        created = image.create_file(ROOT_INODE, b"STILLGOOD")
        image.replace_file(created.inode, b"safe after rejected overwrite")

    with ReadOnlyImage.open(dat_path) as image:
        validator = image._mount.validate  # type: ignore[attr-defined]
        assert validator() == []
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert image.read(readme.inode, 0, readme.size) == b"Hello from AcornFS\r"
        assert image.lookup(ROOT_INODE, b"STILLGOOD") is not None


def test_metadata_failure_rolls_back_and_keeps_session_usable(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        original = image.acorn_metadata(readme.inode)
        setter = image._mount.set_filetype  # type: ignore[attr-defined]
        with (
            patch.object(image._mount, "set_filetype", side_effect=RuntimeError("injected")),
            pytest.raises(RuntimeError, match="injected"),
        ):
            image.set_acorn_metadata(readme.inode, load_address=0x12345678, filetype=0xFFD)
        assert image.acorn_metadata(readme.inode) == original
        with patch.object(image._mount, "set_filetype", side_effect=setter):
            image.set_acorn_metadata(readme.inode, filetype=0xFFD)


def test_failed_cross_directory_rename_rolls_back_and_keeps_session_usable(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    docs = image.lookup(ROOT_INODE, b"DOCS")
    assert docs is not None
    with (
        patch.object(image._mount, "rename", side_effect=RuntimeError("injected")),
        pytest.raises(RuntimeError, match="injected"),
    ):
        image.rename(ROOT_INODE, b"README", docs.inode, b"README")
    assert image.lookup(ROOT_INODE, b"README") is not None
    assert image.lookup(docs.inode, b"README") is None
    image.create_file(ROOT_INODE, b"STILLGOOD")
    image.close()


def test_directory_cannot_be_moved_inside_itself(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        parent = image.make_directory(ROOT_INODE, b"PARENT")
        child = image.make_directory(parent.inode, b"CHILD")
        with pytest.raises(ValueError, match="inside itself"):
            image.rename(ROOT_INODE, b"PARENT", child.inode, b"PARENT")
        assert image.lookup(ROOT_INODE, b"PARENT") == parent
        image.create_file(ROOT_INODE, b"STILLGOOD")


def test_large_uncached_reads_fetch_only_the_requested_sector_range(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    contents = bytes(range(256)) * 16
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        large = image.create_file(ROOT_INODE, b"LARGE")
        image.replace_file(large.inode, contents)

    with ReadOnlyImage.open(dat_path, cache_bytes=64) as image:
        large = image.lookup(ROOT_INODE, b"LARGE")
        assert large is not None
        assert image.uses_ranged_reads(large.inode)
        with patch.object(
            image._mount, "read_bytes", side_effect=AssertionError("whole-file read")
        ):
            assert image.read(large.inode, 510, 9) == contents[510:519]
