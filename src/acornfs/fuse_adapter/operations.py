"""pyfuse3 operations for Acorn images, read-only unless explicitly writable."""

from __future__ import annotations

import errno
import os
import stat

import pyfuse3
from oaknut.adfs.exceptions import (
    ADFSDirectoryFullError,
    ADFSDirectoryNotEmptyError,
    ADFSDiscFullError,
    ADFSEntryExistsError,
    ADFSFileLockedError,
    ADFSPathError,
)

from acornfs.core.image import ROOT_INODE, ImageNode, ReadOnlyImage

BLOCK_SIZE = 256
CACHE_TIMEOUT = 60.0
XATTR_LOAD = b"user.acorn.load"
XATTR_EXECUTE = b"user.acorn.execute"
XATTR_FILETYPE = b"user.acorn.filetype"
XATTR_LOCKED = b"user.acorn.locked"
XATTR_SOURCE = b"user.acorn.source"
XATTR_PATH = b"user.acorn.path"
XATTR_NAMES = (XATTR_LOAD, XATTR_EXECUTE, XATTR_LOCKED, XATTR_SOURCE, XATTR_PATH)


class ReadOnlyOperations(pyfuse3.Operations):
    """Expose an indexed :class:`ReadOnlyImage` through FUSE 3."""

    enable_writeback_cache = False

    def __init__(self, image: ReadOnlyImage) -> None:
        super().__init__()
        self.image = image
        self._write_buffers: dict[int, bytearray] = {}
        self._dirty: set[int] = set()
        self._handles: dict[int, int] = {}
        self._next_fh = 1

    @staticmethod
    def _raise_fuse(exc: Exception) -> None:
        if isinstance(exc, FileNotFoundError):
            code = errno.ENOENT
        elif isinstance(exc, (FileExistsError, ADFSEntryExistsError)):
            code = errno.EEXIST
        elif isinstance(exc, ADFSDirectoryNotEmptyError) or (
            isinstance(exc, OSError) and exc.errno == errno.ENOTEMPTY
        ):
            code = errno.ENOTEMPTY
        elif isinstance(exc, (ADFSDiscFullError, ADFSDirectoryFullError)):
            code = errno.ENOSPC
        elif isinstance(exc, ADFSFileLockedError):
            code = errno.EACCES
        elif isinstance(exc, IsADirectoryError):
            code = errno.EISDIR
        elif isinstance(exc, NotADirectoryError):
            code = errno.ENOTDIR
        elif isinstance(exc, (ValueError, UnicodeError)):
            code = errno.ENAMETOOLONG if "10 bytes" in str(exc) else errno.EINVAL
        elif isinstance(exc, ADFSPathError):
            code = errno.ENOENT
        else:
            code = errno.EIO
        raise pyfuse3.FUSEError(code) from exc

    def _new_handle(self, inode: int) -> int:
        fh = self._next_fh
        self._next_fh += 1
        self._handles[fh] = inode
        return fh

    def _handle_inode(self, fh: int) -> int:
        try:
            return self._handles[fh]
        except KeyError as exc:
            raise pyfuse3.FUSEError(errno.EBADF) from exc

    def _node(self, inode: int) -> ImageNode:
        try:
            return self.image.nodes[inode]
        except KeyError as exc:
            raise pyfuse3.FUSEError(errno.ENOENT) from exc

    def _attributes(self, node: ImageNode) -> pyfuse3.EntryAttributes:
        attributes = pyfuse3.EntryAttributes()
        attributes.st_ino = node.inode
        attributes.generation = 0
        timeout = 0.0 if self.image.writable else CACHE_TIMEOUT
        attributes.entry_timeout = timeout
        attributes.attr_timeout = timeout
        if node.is_dir:
            writable = self.image.writable and not node.locked
            attributes.st_mode = stat.S_IFDIR | (0o755 if writable else 0o555)
        else:
            writable = self.image.writable and not node.locked
            attributes.st_mode = stat.S_IFREG | (0o644 if writable else 0o444)
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
        if wants_write and node.locked:
            raise pyfuse3.FUSEError(errno.EACCES)
        if wants_write:
            try:
                data = bytearray(self.image.read(inode, 0, node.size))
            except Exception as exc:
                raise pyfuse3.FUSEError(errno.EIO) from exc
            if flags & os.O_TRUNC:
                existing = self._write_buffers.get(inode)
                if existing is not None:
                    existing.clear()
                else:
                    data.clear()
                self._dirty.add(inode)
            self._write_buffers.setdefault(inode, data)
        return pyfuse3.FileInfo(fh=self._new_handle(inode))

    async def read(self, fh: int, off: int, size: int) -> bytes:
        inode = self._handle_inode(fh)
        buffered = self._write_buffers.get(inode)
        if buffered is not None:
            return bytes(buffered[off : off + size])
        try:
            return self.image.read(inode, off, size)
        except KeyError as exc:
            raise pyfuse3.FUSEError(errno.ENOENT) from exc
        except IsADirectoryError as exc:
            raise pyfuse3.FUSEError(errno.EISDIR) from exc
        except Exception as exc:
            raise pyfuse3.FUSEError(errno.EIO) from exc

    async def write(self, fh: int, off: int, buf: bytes) -> int:
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        inode = self._handle_inode(fh)
        data = self._write_buffers.get(inode)
        if data is None:
            raise pyfuse3.FUSEError(errno.EBADF)
        end = off + len(buf)
        if end > len(data):
            data.extend(b"\0" * (end - len(data)))
        data[off:end] = buf
        self._dirty.add(inode)
        return len(buf)

    async def create(
        self,
        parent_inode: int,
        name: bytes,
        mode: int,
        flags: int,
        ctx: pyfuse3.RequestContext,
    ) -> tuple[pyfuse3.FileInfo, pyfuse3.EntryAttributes]:
        del mode, flags, ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        try:
            node = self.image.create_file(parent_inode, name)
        except Exception as exc:
            self._raise_fuse(exc)
        self._write_buffers[node.inode] = bytearray()
        info = pyfuse3.FileInfo(fh=self._new_handle(node.inode))
        return info, self._attributes(node)

    async def mkdir(
        self, parent_inode: int, name: bytes, mode: int, ctx: pyfuse3.RequestContext
    ) -> pyfuse3.EntryAttributes:
        del mode, ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        try:
            node = self.image.make_directory(parent_inode, name)
        except Exception as exc:
            self._raise_fuse(exc)
        return self._attributes(node)

    async def unlink(self, parent_inode: int, name: bytes, ctx: pyfuse3.RequestContext) -> None:
        del ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        node = self.image.lookup(parent_inode, name)
        if node is not None and node.inode in self._handles.values():
            raise pyfuse3.FUSEError(errno.EBUSY)
        try:
            self.image.remove(parent_inode, name, directory=False)
        except Exception as exc:
            self._raise_fuse(exc)

    async def rmdir(self, parent_inode: int, name: bytes, ctx: pyfuse3.RequestContext) -> None:
        del ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        try:
            self.image.remove(parent_inode, name, directory=True)
        except Exception as exc:
            self._raise_fuse(exc)

    async def rename(
        self,
        parent_inode_old: int,
        name_old: bytes,
        parent_inode_new: int,
        name_new: bytes,
        flags: int,
        ctx: pyfuse3.RequestContext,
    ) -> None:
        del ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        if flags & pyfuse3.RENAME_EXCHANGE or flags & ~pyfuse3.RENAME_NOREPLACE:
            raise pyfuse3.FUSEError(errno.ENOTSUP)
        destination = self.image.lookup(parent_inode_new, name_new)
        if flags & pyfuse3.RENAME_NOREPLACE and destination is not None:
            raise pyfuse3.FUSEError(errno.EEXIST)
        if destination is not None and destination.inode in self._handles.values():
            raise pyfuse3.FUSEError(errno.EBUSY)
        try:
            self.image.rename(parent_inode_old, name_old, parent_inode_new, name_new)
        except Exception as exc:
            self._raise_fuse(exc)

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
        unsupported = fields.update_uid or fields.update_gid
        if unsupported:
            raise pyfuse3.FUSEError(errno.ENOTSUP)
        if fields.update_size:
            self._flush_inode(inode)
        return self._attributes(self._node(inode))

    def _flush_inode(self, inode: int) -> None:
        if inode not in self._dirty:
            return
        try:
            self.image.replace_file(inode, bytes(self._write_buffers[inode]))
        except Exception as exc:
            self._raise_fuse(exc)
        self._dirty.discard(inode)
        if inode not in self._handles.values():
            self._write_buffers.pop(inode, None)

    async def flush(self, fh: int) -> None:
        self._flush_inode(self._handle_inode(fh))

    async def fsync(self, fh: int, datasync: bool) -> None:
        del datasync
        await self.flush(fh)

    async def release(self, fh: int) -> None:
        await self.flush(fh)
        inode = self._handles.pop(fh, None)
        if inode is not None and inode not in self._handles.values():
            self._write_buffers.pop(inode, None)

    async def access(self, inode: int, mode: int, ctx: pyfuse3.RequestContext) -> bool:
        node = self._node(inode)
        if mode & os.W_OK and not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        if mode & os.W_OK and node.locked:
            raise pyfuse3.FUSEError(errno.EACCES)
        return True

    async def listxattr(self, inode: int, ctx: pyfuse3.RequestContext) -> tuple[bytes, ...]:
        del ctx
        node = self._node(inode)
        if node.inode == ROOT_INODE:
            return (XATTR_SOURCE, XATTR_PATH)
        names: tuple[bytes, ...] = XATTR_NAMES
        if self.image.filetype(inode) is not None:
            names = (*names, XATTR_FILETYPE)
        return names

    async def getxattr(self, inode: int, name: bytes, ctx: pyfuse3.RequestContext) -> bytes:
        del ctx
        node = self._node(inode)
        if name == XATTR_SOURCE:
            return b"adfs"
        if name == XATTR_PATH:
            return node.acorn_path.encode("utf-8")
        try:
            metadata = self.image.acorn_metadata(inode)
        except ValueError as exc:
            raise pyfuse3.FUSEError(errno.ENODATA) from exc
        if name == XATTR_LOAD:
            return f"{int(metadata.load_address or 0):08X}".encode("ascii")
        if name == XATTR_EXECUTE:
            return f"{int(metadata.exec_address or 0):08X}".encode("ascii")
        if name == XATTR_LOCKED:
            return b"1" if node.locked else b"0"
        if name == XATTR_FILETYPE:
            filetype = self.image.filetype(inode)
            if filetype is None:
                raise pyfuse3.FUSEError(errno.ENODATA)
            return f"{filetype:03X}".encode("ascii")
        raise pyfuse3.FUSEError(errno.ENODATA)

    async def setxattr(
        self, inode: int, name: bytes, value: bytes, ctx: pyfuse3.RequestContext
    ) -> None:
        del ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        try:
            text = value.decode("ascii").strip()
            if name == XATTR_LOAD:
                if len(text) != 8:
                    raise ValueError("load address must be exactly 8 hexadecimal digits")
                self.image.set_acorn_metadata(inode, load_address=int(text, 16))
            elif name == XATTR_EXECUTE:
                if len(text) != 8:
                    raise ValueError("execute address must be exactly 8 hexadecimal digits")
                self.image.set_acorn_metadata(inode, exec_address=int(text, 16))
            elif name == XATTR_FILETYPE:
                if len(text) != 3:
                    raise ValueError("filetype must be exactly 3 hexadecimal digits")
                filetype = int(text, 16)
                if not 0 <= filetype <= 0xFFF:
                    raise ValueError("filetype must be 000..FFF")
                self.image.set_acorn_metadata(inode, filetype=filetype)
            elif name == XATTR_LOCKED:
                normalised = text.casefold()
                if normalised not in {"0", "1", "false", "true"}:
                    raise ValueError("locked must be 0, 1, false or true")
                self.image.set_acorn_metadata(inode, locked=normalised in {"1", "true"})
            elif name in {XATTR_SOURCE, XATTR_PATH}:
                raise pyfuse3.FUSEError(errno.EPERM)
            else:
                raise pyfuse3.FUSEError(errno.ENOTSUP)
        except pyfuse3.FUSEError:
            raise
        except (UnicodeError, ValueError) as exc:
            raise pyfuse3.FUSEError(errno.EINVAL) from exc
        except Exception as exc:
            self._raise_fuse(exc)

    async def removexattr(self, inode: int, name: bytes, ctx: pyfuse3.RequestContext) -> None:
        del inode, name, ctx
        raise pyfuse3.FUSEError(errno.ENOTSUP)

    async def statfs(self, ctx: pyfuse3.RequestContext) -> pyfuse3.StatvfsData:
        result = pyfuse3.StatvfsData()
        result.f_bsize = BLOCK_SIZE
        result.f_frsize = BLOCK_SIZE
        result.f_blocks = self.image.total_bytes // BLOCK_SIZE
        result.f_bfree = self.image.free_bytes // BLOCK_SIZE
        result.f_bavail = result.f_bfree
        result.f_files = len(self.image.nodes)
        result.f_ffree = max(0, self.image.max_nodes - len(self.image.nodes))
        result.f_favail = result.f_ffree
        result.f_namemax = 10
        return result
