import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyfuse3
import pytest

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.fuse_adapter.operations import ReadOnlyOperations
from tests.image_fixture import create_beebscsi_image


def run_async(function: Any, *args: Any) -> Any:
    async def invoke() -> Any:
        return await function(*args)

    return asyncio.run(invoke())


@pytest.fixture
def operations(tmp_path: Path) -> Any:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path) as image:
        yield ReadOnlyOperations(image)


def test_lookup_and_read_nested_file(operations: ReadOnlyOperations) -> None:
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    docs = run_async(operations.lookup, ROOT_INODE, b"DOCS", context)
    guide = run_async(operations.lookup, docs.st_ino, b"GUIDE", context)
    info = run_async(operations.open, guide.st_ino, 0, context)
    assert run_async(operations.read, info.fh, 0, 1024) == b"Nested file\r"
    assert guide.st_mode & 0o777 == 0o444


def test_write_access_is_rejected(operations: ReadOnlyOperations) -> None:
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
    with pytest.raises(pyfuse3.FUSEError):
        run_async(operations.open, readme.st_ino, 1, context)
