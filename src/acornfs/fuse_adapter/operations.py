"""pyfuse3 operations for Acorn images, read-only unless explicitly writable."""

from __future__ import annotations

import errno
import os
import stat

import pyfuse3

from acornfs.core.image import ImageNode, ReadOnlyImage

BLOCK_SIZE = 256
CACHE_TIMEOUT = 60.0


class ReadOnlyOperations(pyfuse3.Operations):
    """Expose an indexed :class:`ReadOnlyImage` through FUSE 3."""

    enable_writeback_cache = False

    def __init__(self, image: ReadOnlyImage) -> None:
        super().__init__()
        self.image = image
        self._write_buffers: dict[int, bytearray] = {}
        self._dirty: set[int] = set()

    def _node(self, inode: int) -> ImageNode:
        try:
            return self.image.nodes[inode]
        except KeyError as exc:
            raise pyfuse3.FUSEError(errno.ENOENT) from exc

    def _attributes(self, node: ImageNode) -> pyfuse3.EntryAttributes:
        attributes = pyfuse3.EntryAttributes()
        attributes.st_ino = node.inode
        attributes.generation = 0
        attributes.entry_timeout = CACHE_TIMEOUT
        attributes.attr_timeout = CACHE_TIMEOUT
        if node.is_dir:
            attributes.st_mode = stat.S_IFDIR | (0o755 if self.image.writable else 0o555)
        else:
            attributes.st_mode = stat.S_IFREG | (0o644 if self.image.writable else 0o444)
        attributes.st_nlink = 2 if node.is_dir else 1
        attributes.st_uid = os.getuid()
        attributes.st_gid = os.getgid()
        attributes.st_rdev = 0
        attributes.st_size = 0 if node.is_dir else node.size
        attributes.st_blksize = BLOCK_SIZE
        attributes.st_blocks = (node.size + 511) // 512
        attributes.st_atime_ns = self.image.timestamp_ns
        attributes.st_mtime_ns = self.image.timestamp_ns
        attributes.st_ctime_ns = self.image.timestamp_ns
        return attributes

    async def getattr(
        self, inode: int, ctx: pyfuse3.RequestContext | None = None
    ) -> pyfuse3.EntryAttributes:
        return self._attributes(self._node(inode))

    async def lookup(
        self, parent_inode: int, name: bytes, ctx: pyfuse3.RequestContext
    ) -> pyfuse3.EntryAttributes:
        parent = self._node(parent_inode)
        if not parent.is_dir:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        if name == b".":
            return self._attributes(parent)
        if name == b"..":
            return self._attributes(self._node(parent.parent_inode))
        node = self.image.lookup(parent_inode, name)
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._attributes(node)

    async def opendir(self, inode: int, ctx: pyfuse3.RequestContext) -> int:
        if not self._node(inode).is_dir:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        return inode

    async def readdir(self, fh: int, start_id: int, token: pyfuse3.ReaddirToken) -> None:
        node = self._node(fh)
        if not node.is_dir:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        children = self.image.children.get(fh, ())
        for index, inode in enumerate(children, start=1):
            if index <= start_id:
                continue
            child = self._node(inode)
            if not pyfuse3.readdir_reply(token, child.name, self._attributes(child), index):
                break

    async def releasedir(self, fh: int) -> None:
        return None

    async def open(self, inode: int, flags: int, ctx: pyfuse3.RequestContext) -> pyfuse3.FileInfo:
        node = self._node(inode)
        if node.is_dir:
            raise pyfuse3.FUSEError(errno.EISDIR)
        wants_write = flags & os.O_ACCMODE != os.O_RDONLY
        if wants_write and not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        if wants_write:
            try:
                data = bytearray(self.image.read(inode, 0, node.size))
            except Exception as exc:
                raise pyfuse3.FUSEError(errno.EIO) from exc
            if flags & os.O_TRUNC:
                data.clear()
                self._dirty.add(inode)
            self._write_buffers[inode] = data
        return pyfuse3.FileInfo(fh=inode)

    async def read(self, fh: int, off: int, size: int) -> bytes:
        buffered = self._write_buffers.get(fh)
        if buffered is not None:
            return bytes(buffered[off : off + size])
        try:
            return self.image.read(fh, off, size)
        except KeyError as exc:
            raise pyfuse3.FUSEError(errno.ENOENT) from exc
        except IsADirectoryError as exc:
            raise pyfuse3.FUSEError(errno.EISDIR) from exc
        except Exception as exc:
            raise pyfuse3.FUSEError(errno.EIO) from exc

    async def write(self, fh: int, off: int, buf: bytes) -> int:
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        data = self._write_buffers.get(fh)
        if data is None:
            raise pyfuse3.FUSEError(errno.EBADF)
        end = off + len(buf)
        if end > len(data):
            data.extend(b"\0" * (end - len(data)))
        data[off:end] = buf
        self._dirty.add(fh)
        return len(buf)

    async def setattr(
        self,
        inode: int,
        attr: pyfuse3.EntryAttributes,
        fields: pyfuse3.SetattrFields,
        fh: int | None,
        ctx: pyfuse3.RequestContext,
    ) -> pyfuse3.EntryAttributes:
        del fh, ctx
        node = self._node(inode)
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        if fields.update_size:
            if node.is_dir:
                raise pyfuse3.FUSEError(errno.EISDIR)
            data = self._write_buffers.setdefault(
                inode, bytearray(self.image.read(inode, 0, node.size))
            )
            new_size = attr.st_size
            if new_size < len(data):
                del data[new_size:]
            elif new_size > len(data):
                data.extend(b"\0" * (new_size - len(data)))
            self._dirty.add(inode)
        unsupported = fields.update_mode or fields.update_uid or fields.update_gid
        if unsupported:
            raise pyfuse3.FUSEError(errno.ENOTSUP)
        if fields.update_size:
            await self.flush(inode)
        return self._attributes(self._node(inode))

    async def flush(self, fh: int) -> None:
        if fh not in self._dirty:
            return
        try:
            self.image.replace_file(fh, bytes(self._write_buffers[fh]))
            self.image.sync()
        except Exception as exc:
            raise pyfuse3.FUSEError(errno.EIO) from exc
        self._dirty.discard(fh)

    async def fsync(self, fh: int, datasync: bool) -> None:
        del datasync
        await self.flush(fh)

    async def release(self, fh: int) -> None:
        await self.flush(fh)
        self._write_buffers.pop(fh, None)

    async def access(self, inode: int, mode: int, ctx: pyfuse3.RequestContext) -> bool:
        self._node(inode)
        if mode & os.W_OK and not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        return True

    async def statfs(self, ctx: pyfuse3.RequestContext) -> pyfuse3.StatvfsData:
        result = pyfuse3.StatvfsData()
        result.f_bsize = BLOCK_SIZE
        result.f_frsize = BLOCK_SIZE
        result.f_blocks = self.image.total_bytes // BLOCK_SIZE
        result.f_bfree = self.image.free_bytes // BLOCK_SIZE
        result.f_bavail = result.f_bfree
        result.f_files = len(self.image.nodes)
        result.f_ffree = 0
        result.f_favail = 0
        result.f_namemax = 255
        return result
