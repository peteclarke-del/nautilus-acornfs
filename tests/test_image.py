import os
from pathlib import Path
from unittest.mock import patch

import pytest
from oaknut.file import AcornMeta

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.errors import AcornFSError
from acornfs.recovery import recover_image
from tests.image_fixture import create_beebscsi_image


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


def test_unexpected_mutation_failure_blocks_session_and_retains_recovery(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    with (
        patch.object(image._mount, "write_bytes", side_effect=RuntimeError("injected failure")),
        pytest.raises(RuntimeError, match="injected failure"),
    ):
        image.create_file(ROOT_INODE, b"FAIL")
    with pytest.raises(AcornFSError, match="session has failed"):
        image.create_file(ROOT_INODE, b"BLOCKED")
    image.close()
    assert recover_image(dat_path, discard=True) == "Recovery checkpoint discarded."
