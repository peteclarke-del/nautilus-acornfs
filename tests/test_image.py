from pathlib import Path

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
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
