import asyncio
import errno
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyfuse3
import pytest
from oaknut.file import AcornMeta

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


def test_writable_operation_flushes_existing_file(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        info = run_async(operations.open, readme.st_ino, os.O_WRONLY | os.O_TRUNC, context)
        assert run_async(operations.write, info.fh, 0, b"New contents\r") == 13
        run_async(operations.fsync, info.fh, False)
        run_async(operations.release, info.fh)
        read_info = run_async(operations.open, readme.st_ino, os.O_RDONLY, context)
        assert run_async(operations.read, read_info.fh, 0, 1024) == b"New contents\r"
        run_async(operations.release, read_info.fh)

    with ReadOnlyImage.open(dat_path) as image:
        readme_node = image.lookup(ROOT_INODE, b"README")
        assert readme_node is not None
        assert image.read(readme_node.inode, 0, 1024) == b"New contents\r"


def test_writable_fuse_namespace_lifecycle(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        folder = run_async(operations.mkdir, ROOT_INODE, b"WRITABLE", 0o755, context)
        info, created = run_async(
            operations.create, folder.st_ino, b"CREATED", 0o644, os.O_WRONLY, context
        )
        run_async(operations.write, info.fh, 0, b"created through FUSE")
        run_async(operations.release, info.fh)
        run_async(
            operations.rename,
            folder.st_ino,
            b"CREATED",
            ROOT_INODE,
            b"RENAMED",
            0,
            context,
        )
        renamed = run_async(operations.lookup, ROOT_INODE, b"RENAMED", context)
        assert renamed.st_ino == created.st_ino
        run_async(operations.unlink, ROOT_INODE, b"RENAMED", context)
        run_async(operations.rmdir, ROOT_INODE, b"WRITABLE", context)
        with pytest.raises(pyfuse3.FUSEError) as missing:
            run_async(operations.lookup, ROOT_INODE, b"RENAMED", context)
        assert missing.value.errno == errno.ENOENT


def test_writable_fuse_rejects_overlong_adfs_name(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        with pytest.raises(pyfuse3.FUSEError) as invalid:
            run_async(
                operations.create,
                ROOT_INODE,
                b"ELEVENCHARS",
                0o644,
                os.O_WRONLY,
                context,
            )
        assert invalid.value.errno == errno.ENAMETOOLONG


def test_acorn_locked_file_is_presented_and_enforced_read_only(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        image._mount.set_acorn_meta(  # type: ignore[attr-defined]
            readme.acorn_path,
            AcornMeta(load_address=0, exec_address=0, access=0x0B),
        )
        image.sync()

    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        assert readme.st_mode & 0o222 == 0
        with pytest.raises(pyfuse3.FUSEError) as locked:
            run_async(operations.open, readme.st_ino, os.O_WRONLY, context)
        assert locked.value.errno == errno.EACCES


def test_acorn_extended_attributes_are_listed_and_persisted(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)

        names = set(run_async(operations.listxattr, readme.st_ino, context))
        assert names == {
            b"user.acorn.load",
            b"user.acorn.execute",
            b"user.acorn.locked",
            b"user.acorn.source",
            b"user.acorn.path",
        }
        assert (
            run_async(operations.getxattr, readme.st_ino, b"user.acorn.source", context) == b"adfs"
        )
        assert (
            run_async(operations.getxattr, readme.st_ino, b"user.acorn.path", context)
            == b"$.README"
        )

        run_async(
            operations.setxattr,
            readme.st_ino,
            b"user.acorn.load",
            b"1234ABCD",
            context,
        )
        run_async(
            operations.setxattr,
            readme.st_ino,
            b"user.acorn.execute",
            b"10203040",
            context,
        )
        assert (
            run_async(operations.getxattr, readme.st_ino, b"user.acorn.load", context)
            == b"1234ABCD"
        )
        assert (
            run_async(operations.getxattr, readme.st_ino, b"user.acorn.execute", context)
            == b"10203040"
        )

        run_async(
            operations.setxattr,
            readme.st_ino,
            b"user.acorn.filetype",
            b"FFD",
            context,
        )
        assert b"user.acorn.filetype" in run_async(operations.listxattr, readme.st_ino, context)
        assert (
            run_async(operations.getxattr, readme.st_ino, b"user.acorn.filetype", context) == b"FFD"
        )

        run_async(
            operations.setxattr,
            readme.st_ino,
            b"user.acorn.locked",
            b"true",
            context,
        )
        assert run_async(operations.getxattr, readme.st_ino, b"user.acorn.locked", context) == b"1"
        locked = run_async(operations.getattr, readme.st_ino, context)
        assert locked.st_mode & 0o222 == 0
        run_async(
            operations.setxattr,
            readme.st_ino,
            b"user.acorn.locked",
            b"false",
            context,
        )
        assert run_async(operations.getxattr, readme.st_ino, b"user.acorn.locked", context) == b"0"
        unlocked = run_async(operations.getattr, readme.st_ino, context)
        assert unlocked.st_mode & 0o200
        run_async(
            operations.setxattr,
            readme.st_ino,
            b"user.acorn.locked",
            b"1",
            context,
        )

    with ReadOnlyImage.open(dat_path) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        assert (
            run_async(operations.getxattr, readme.st_ino, b"user.acorn.filetype", context) == b"FFD"
        )
        assert run_async(operations.getxattr, readme.st_ino, b"user.acorn.locked", context) == b"1"


def test_acorn_extended_attributes_reject_invalid_or_read_only_changes(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)

        for name, value in (
            (b"user.acorn.load", b"123"),
            (b"user.acorn.execute", b"NOTHEX!!"),
            (b"user.acorn.filetype", b"1000"),
            (b"user.acorn.locked", b"maybe"),
        ):
            with pytest.raises(pyfuse3.FUSEError) as invalid:
                run_async(operations.setxattr, readme.st_ino, name, value, context)
            assert invalid.value.errno == errno.EINVAL

        with pytest.raises(pyfuse3.FUSEError) as synthetic:
            run_async(
                operations.setxattr,
                readme.st_ino,
                b"user.acorn.source",
                b"other",
                context,
            )
        assert synthetic.value.errno == errno.EPERM

        with pytest.raises(pyfuse3.FUSEError) as unknown:
            run_async(operations.getxattr, readme.st_ino, b"user.acorn.unknown", context)
        assert unknown.value.errno == errno.ENODATA

    with ReadOnlyImage.open(dat_path) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        with pytest.raises(pyfuse3.FUSEError) as read_only:
            run_async(
                operations.setxattr,
                readme.st_ino,
                b"user.acorn.locked",
                b"1",
                context,
            )
        assert read_only.value.errno == errno.EROFS


def test_writable_fuse_reports_adfs_capacity_as_enospc(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        info, created = run_async(
            operations.create, ROOT_INODE, b"TOOBIG", 0o644, os.O_WRONLY, context
        )
        with pytest.raises(pyfuse3.FUSEError) as full:
            run_async(operations.write, info.fh, 0, b"x" * (image.free_bytes + 256))
        assert full.value.errno == errno.ENOSPC
        assert len(operations._write_buffers[created.st_ino]) == 0


def test_writable_fuse_rejects_oversized_truncate_before_buffer_growth(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        fields = SimpleNamespace(
            update_size=True,
            update_uid=False,
            update_gid=False,
        )
        attributes = SimpleNamespace(st_size=image.free_bytes + readme.st_size + 256)
        with pytest.raises(pyfuse3.FUSEError) as full:
            run_async(operations.setattr, readme.st_ino, attributes, fields, None, context)
        assert full.value.errno == errno.ENOSPC
        assert len(operations._write_buffers[readme.st_ino]) == readme.st_size
