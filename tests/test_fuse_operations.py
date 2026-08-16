import asyncio
import errno
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import call, patch

import pyfuse3
import pytest
from oaknut.file import AcornMeta

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.errors import AcornFSError
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


def test_large_sequential_reads_use_bounded_read_ahead(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    contents = bytes(range(256)) * 16
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        large = image.create_file(ROOT_INODE, b"LARGE")
        image.replace_file(large.inode, contents)

    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, cache_bytes=64) as image:
        large = image.lookup(ROOT_INODE, b"LARGE")
        assert large is not None
        operations = ReadOnlyOperations(image, read_ahead_bytes=512, read_ahead_cache_bytes=1024)
        info = run_async(operations.open, large.inode, os.O_RDONLY, context)
        with patch.object(image, "read", wraps=image.read) as read:
            assert run_async(operations.read, info.fh, 0, 256) == contents[:256]
            assert run_async(operations.read, info.fh, 256, 256) == contents[256:512]
            assert read.call_args_list == [
                call(large.inode, 0, 256),
                call(large.inode, 256, 768),
            ]

            assert run_async(operations.read, info.fh, 512, 256) == contents[512:768]
            assert read.call_count == 2
            assert operations._read_ahead_size <= 1024

            assert run_async(operations.read, info.fh, 3000, 128) == contents[3000:3128]
            assert read.call_args_list[-1] == call(large.inode, 3000, 128)
            assert operations._read_ahead_size == 0

        run_async(operations.release, info.fh)
        assert info.fh not in operations._read_states


def test_read_ahead_total_budget_evicts_oldest_handle(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    contents = bytes(range(256)) * 16
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        large = image.create_file(ROOT_INODE, b"LARGE")
        image.replace_file(large.inode, contents)

    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, cache_bytes=64) as image:
        large = image.lookup(ROOT_INODE, b"LARGE")
        assert large is not None
        operations = ReadOnlyOperations(image, read_ahead_bytes=512, read_ahead_cache_bytes=512)
        first = run_async(operations.open, large.inode, os.O_RDONLY, context)
        second = run_async(operations.open, large.inode, os.O_RDONLY, context)

        for info in (first, second):
            run_async(operations.read, info.fh, 0, 128)
            run_async(operations.read, info.fh, 128, 128)

        assert operations._read_states[first.fh].buffer == b""
        assert len(operations._read_states[second.fh].buffer) == 512
        assert operations._read_ahead_size == 512

        run_async(operations.release, first.fh)
        run_async(operations.release, second.fh)
        assert operations._read_ahead_size == 0


def test_writable_access_discards_read_ahead_on_every_handle(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    contents = bytes(range(256)) * 16
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        large = image.create_file(ROOT_INODE, b"LARGE")
        image.replace_file(large.inode, contents)

    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True, cache_bytes=64) as image:
        large = image.lookup(ROOT_INODE, b"LARGE")
        assert large is not None
        operations = ReadOnlyOperations(image, read_ahead_bytes=512, read_ahead_cache_bytes=1024)
        reader = run_async(operations.open, large.inode, os.O_RDONLY, context)
        run_async(operations.read, reader.fh, 0, 128)
        run_async(operations.read, reader.fh, 128, 128)
        assert operations._read_ahead_size == 512

        writer = run_async(operations.open, large.inode, os.O_WRONLY, context)
        assert operations._read_ahead_size == 0
        assert operations._read_states[reader.fh].buffer == b""

        run_async(operations.release, writer.fh)
        run_async(operations.release, reader.fh)


def test_read_ahead_configuration_rejects_unsafe_limits(operations: ReadOnlyOperations) -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        ReadOnlyOperations(operations.image, read_ahead_bytes=-1)
    with pytest.raises(ValueError, match="at least two"):
        ReadOnlyOperations(operations.image, sequential_read_threshold=1)


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


def test_multiple_writable_handles_share_one_coherent_buffer(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        first = run_async(operations.open, readme.st_ino, os.O_RDWR | os.O_TRUNC, context)
        second = run_async(operations.open, readme.st_ino, os.O_RDWR, context)

        run_async(operations.write, first.fh, 0, b"shared")
        assert run_async(operations.read, second.fh, 0, 6) == b"shared"
        run_async(operations.write, second.fh, 6, b"-buffer")
        assert run_async(operations.getattr, readme.st_ino, context).st_size == 13
        run_async(operations.fsync, first.fh, False)
        run_async(operations.release, first.fh)
        run_async(operations.write, second.fh, 13, b"!")
        run_async(operations.release, second.fh)

    with ReadOnlyImage.open(dat_path) as image:
        readme_node = image.lookup(ROOT_INODE, b"README")
        assert readme_node is not None
        assert image.read(readme_node.inode, 0, 14) == b"shared-buffer!"


def test_truncate_from_one_handle_is_visible_to_every_handle(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        first = run_async(operations.open, readme.st_ino, os.O_RDWR, context)
        second = run_async(operations.open, readme.st_ino, os.O_RDWR | os.O_TRUNC, context)

        assert run_async(operations.read, first.fh, 0, 1024) == b""
        assert run_async(operations.getattr, readme.st_ino, context).st_size == 0
        run_async(operations.write, second.fh, 0, b"replacement")
        assert run_async(operations.getattr, readme.st_ino, context).st_size == 11
        run_async(operations.release, first.fh)
        run_async(operations.release, second.fh)

    with ReadOnlyImage.open(dat_path) as image:
        readme_node = image.lookup(ROOT_INODE, b"README")
        assert readme_node is not None
        assert image.read(readme_node.inode, 0, 1024) == b"replacement"


def test_graceful_shutdown_flushes_dirty_open_handles(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        info = run_async(operations.open, readme.st_ino, os.O_WRONLY | os.O_TRUNC, context)
        run_async(operations.write, info.fh, 0, b"dirty but recoverable")

        operations.flush_pending()

        assert operations._dirty == set()
        assert info.fh in operations._handles

    with ReadOnlyImage.open(dat_path) as image:
        readme_node = image.lookup(ROOT_INODE, b"README")
        assert readme_node is not None
        assert image.read(readme_node.inode, 0, 1024) == b"dirty but recoverable"


def test_failed_shutdown_flush_keeps_buffer_dirty_for_recovery(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    context = SimpleNamespace(uid=1000, gid=1000, pid=1, umask=0)
    image = ReadOnlyImage.open(dat_path, writable=True)
    try:
        operations = ReadOnlyOperations(image)
        readme = run_async(operations.lookup, ROOT_INODE, b"README", context)
        info = run_async(operations.open, readme.st_ino, os.O_WRONLY | os.O_TRUNC, context)
        run_async(operations.write, info.fh, 0, b"cannot be flushed")

        with (
            patch.object(image, "replace_file", side_effect=AcornFSError("injected failure")),
            pytest.raises(AcornFSError, match="pending FUSE write buffers"),
        ):
            operations.flush_pending()

        assert readme.st_ino in operations._dirty
        assert bytes(operations._write_buffers[readme.st_ino]) == b"cannot be flushed"
    finally:
        image.close(clean=False)


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
