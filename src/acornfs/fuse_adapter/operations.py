"""pyfuse3 operations for a read-only Acorn image."""

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
        attributes.st_mode = (stat.S_IFDIR | 0o555) if node.is_dir else (stat.S_IFREG | 0o444)
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
        if flags & os.O_ACCMODE != os.O_RDONLY or flags & (os.O_TRUNC | os.O_APPEND):
            raise pyfuse3.FUSEError(errno.EROFS)
        return pyfuse3.FileInfo(fh=inode)

    async def read(self, fh: int, off: int, size: int) -> bytes:
        try:
            return self.image.read(fh, off, size)
        except KeyError as exc:
            raise pyfuse3.FUSEError(errno.ENOENT) from exc
        except IsADirectoryError as exc:
            raise pyfuse3.FUSEError(errno.EISDIR) from exc
        except Exception as exc:
            raise pyfuse3.FUSEError(errno.EIO) from exc

    async def release(self, fh: int) -> None:
        return None

    async def access(self, inode: int, mode: int, ctx: pyfuse3.RequestContext) -> bool:
        self._node(inode)
        if mode & os.W_OK:
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
