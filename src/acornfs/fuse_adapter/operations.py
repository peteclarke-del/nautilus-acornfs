"""pyfuse3 operations for Acorn images, read-only unless explicitly writable."""

from __future__ import annotations

import errno
import os
import stat
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass

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
from acornfs.errors import AcornFSError, FilenameTooLongError
from acornfs.i18n import _

BLOCK_SIZE = 256
CACHE_TIMEOUT = 60.0
DEFAULT_READ_AHEAD_BYTES = 256 * 1024
DEFAULT_READ_AHEAD_CACHE_BYTES = 4 * 1024 * 1024
DEFAULT_SEQUENTIAL_READ_THRESHOLD = 2
XATTR_LOAD = b"user.acorn.load"
XATTR_EXECUTE = b"user.acorn.execute"
XATTR_FILETYPE = b"user.acorn.filetype"
XATTR_LOCKED = b"user.acorn.locked"
XATTR_SOURCE = b"user.acorn.source"
XATTR_PATH = b"user.acorn.path"
XATTR_RUN_ONLY = b"user.acorn.run_only"
XATTR_NAMES = (XATTR_LOAD, XATTR_EXECUTE, XATTR_LOCKED, XATTR_SOURCE, XATTR_PATH)


@dataclass(slots=True)
class _ReadState:
    next_offset: int | None = None
    sequential_reads: int = 0
    buffer_offset: int = 0
    buffer: bytes = b""
    buffer_position: int = 0


@dataclass(slots=True)
class _MetadataUpdate:
    load_address: int | None = None
    exec_address: int | None = None
    filetype: int | None = None
    locked: bool | None = None


class ReadOnlyOperations(pyfuse3.Operations):
    """Expose an indexed :class:`ReadOnlyImage` through FUSE 3."""

    enable_writeback_cache = False

    def __init__(
        self,
        image: ReadOnlyImage,
        *,
        read_ahead_bytes: int = DEFAULT_READ_AHEAD_BYTES,
        read_ahead_cache_bytes: int = DEFAULT_READ_AHEAD_CACHE_BYTES,
        sequential_read_threshold: int = DEFAULT_SEQUENTIAL_READ_THRESHOLD,
    ) -> None:
        super().__init__()
        if read_ahead_bytes < 0 or read_ahead_cache_bytes < 0:
            raise ValueError("read-ahead limits cannot be negative")
        if sequential_read_threshold < 2:
            raise ValueError("sequential read detection requires at least two reads")
        self.image = image
        self._read_ahead_limit = min(read_ahead_bytes, read_ahead_cache_bytes)
        self._read_ahead_cache_limit = read_ahead_cache_bytes
        self._sequential_read_threshold = sequential_read_threshold
        self._write_buffers: dict[int, bytearray] = {}
        self._dirty: set[int] = set()
        self._metadata_updates: dict[int, _MetadataUpdate] = {}
        self._handles: dict[int, int] = {}
        self._read_states: dict[int, _ReadState] = {}
        self._read_ahead_lru: OrderedDict[int, None] = OrderedDict()
        self._read_ahead_size = 0
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
        elif isinstance(exc, (ADFSFileLockedError, PermissionError)):
            code = errno.EACCES
        elif isinstance(exc, IsADirectoryError):
            code = errno.EISDIR
        elif isinstance(exc, NotADirectoryError):
            code = errno.ENOTDIR
        elif isinstance(exc, FilenameTooLongError):
            code = errno.ENAMETOOLONG
        elif isinstance(exc, (ValueError, UnicodeError)):
            code = errno.EINVAL
        elif isinstance(exc, ADFSPathError):
            code = errno.ENOENT
        else:
            code = errno.EIO
        raise pyfuse3.FUSEError(code) from exc

    def _new_handle(self, inode: int) -> int:
        fh = self._next_fh
        self._next_fh += 1
        self._handles[fh] = inode
        self._read_states[fh] = _ReadState()
        return fh

    def _clear_read_ahead(self, fh: int) -> None:
        state = self._read_states.get(fh)
        if state is None:
            return
        self._read_ahead_size -= len(state.buffer)
        state.buffer = b""
        state.buffer_offset = 0
        state.buffer_position = 0
        self._read_ahead_lru.pop(fh, None)

    def _clear_inode_read_ahead(self, inode: int) -> None:
        for fh, handle_inode in self._handles.items():
            if handle_inode == inode:
                self._clear_read_ahead(fh)

    def _store_read_ahead(self, fh: int, offset: int, data: bytes) -> None:
        self._clear_read_ahead(fh)
        keep = min(len(data), self._read_ahead_cache_limit)
        if keep == 0:
            return
        data = data[:keep]
        while self._read_ahead_size + keep > self._read_ahead_cache_limit:
            old_fh = next(iter(self._read_ahead_lru))
            self._clear_read_ahead(old_fh)
        state = self._read_states[fh]
        state.buffer_offset = offset
        state.buffer = data
        state.buffer_position = 0
        self._read_ahead_size += keep
        self._read_ahead_lru[fh] = None

    def _consume_read_ahead(self, fh: int, offset: int, size: int) -> bytes | None:
        state = self._read_states[fh]
        if not state.buffer:
            return None
        available = len(state.buffer) - state.buffer_position
        if offset != state.buffer_offset or size > available:
            self._clear_read_ahead(fh)
            return None
        start = state.buffer_position
        result = state.buffer[start : start + size]
        state.buffer_position += len(result)
        state.buffer_offset += len(result)
        state.next_offset = offset + len(result)
        state.sequential_reads += 1
        if state.buffer_position < len(state.buffer):
            self._read_ahead_lru.move_to_end(fh)
        else:
            self._clear_read_ahead(fh)
        return result

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

    def _node_locked(self, node: ImageNode) -> bool:
        update = self._metadata_updates.get(node.inode)
        return node.locked if update is None or update.locked is None else update.locked

    @staticmethod
    def _invalidate_inode(inode: int, *, attr_only: bool = False) -> None:
        with suppress(OSError, RuntimeError):
            pyfuse3.invalidate_inode(inode, attr_only=attr_only)

    @staticmethod
    def _invalidate_entry(parent_inode: int, name: bytes, *, deleted: int = 0) -> None:
        with suppress(OSError, RuntimeError):
            pyfuse3.invalidate_entry_async(parent_inode, name, deleted=deleted, ignore_enoent=True)

    def _write_buffer(self, inode: int) -> bytearray:
        data = self._write_buffers.get(inode)
        if data is None:
            node = self._node(inode)
            data = bytearray(self.image.read(inode, 0, node.size))
            self._write_buffers[inode] = data
            self._clear_inode_read_ahead(inode)
        return data

    def _attributes(self, node: ImageNode) -> pyfuse3.EntryAttributes:
        attributes = pyfuse3.EntryAttributes()
        attributes.st_ino = node.inode
        attributes.generation = 0
        timeout = 0.0 if self.image.writable else CACHE_TIMEOUT
        attributes.entry_timeout = timeout
        attributes.attr_timeout = timeout
        if node.is_dir:
            writable = self.image.writable and not self._node_locked(node)
            attributes.st_mode = stat.S_IFDIR | (0o755 if writable else 0o555)
        else:
            writable = self.image.writable and not self._node_locked(node)
            attributes.st_mode = stat.S_IFREG | (0o644 if writable else 0o444)
        attributes.st_nlink = 2 if node.is_dir else 1
        attributes.st_uid = os.getuid()
        attributes.st_gid = os.getgid()
        attributes.st_rdev = 0
        visible_size = (
            len(self._write_buffers[node.inode]) if node.inode in self._write_buffers else node.size
        )
        attributes.st_size = 0 if node.is_dir else visible_size
        attributes.st_blksize = BLOCK_SIZE
        attributes.st_blocks = (attributes.st_size + 511) // 512
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
        if wants_write and self._node_locked(node):
            raise pyfuse3.FUSEError(errno.EACCES)
        if wants_write:
            try:
                data = self._write_buffer(inode)
            except Exception as exc:
                raise pyfuse3.FUSEError(errno.EIO) from exc
            if flags & os.O_TRUNC:
                data.clear()
                self._dirty.add(inode)
        return pyfuse3.FileInfo(fh=self._new_handle(inode))

    async def read(self, fh: int, off: int, size: int) -> bytes:
        inode = self._handle_inode(fh)
        buffered = self._write_buffers.get(inode)
        if buffered is not None:
            self._clear_read_ahead(fh)
            return bytes(buffered[off : off + size])
        if size <= 0:
            return b""
        prefetched = self._consume_read_ahead(fh, off, size)
        if prefetched is not None:
            return prefetched
        state = self._read_states[fh]
        if state.next_offset == off:
            state.sequential_reads += 1
        else:
            state.sequential_reads = 1
        try:
            read_size = size
            if (
                state.sequential_reads >= self._sequential_read_threshold
                and self._read_ahead_limit
                and self._read_ahead_cache_limit
                and self.image.uses_ranged_reads(inode)
            ):
                remaining = max(0, self._node(inode).size - off)
                read_size = min(size + self._read_ahead_limit, remaining)
            data = self.image.read(inode, off, read_size)
        except KeyError as exc:
            raise pyfuse3.FUSEError(errno.ENOENT) from exc
        except IsADirectoryError as exc:
            raise pyfuse3.FUSEError(errno.EISDIR) from exc
        except Exception as exc:
            raise pyfuse3.FUSEError(errno.EIO) from exc
        result = data[:size]
        state.next_offset = off + len(result)
        self._store_read_ahead(fh, state.next_offset, data[len(result) :])
        return result

    async def write(self, fh: int, off: int, buf: bytes) -> int:
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        inode = self._handle_inode(fh)
        self._clear_inode_read_ahead(inode)
        data = self._write_buffers.get(inode)
        if data is None:
            raise pyfuse3.FUSEError(errno.EBADF)
        if off < 0:
            raise pyfuse3.FUSEError(errno.EINVAL)
        end = off + len(buf)
        if end > len(data):
            try:
                self.image.preflight_file_size(inode, end)
            except Exception as exc:
                self._raise_fuse(exc)
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
        self._invalidate_entry(parent_inode, name)
        self._invalidate_inode(parent_inode, attr_only=True)
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
        self._invalidate_entry(parent_inode, name)
        self._invalidate_inode(parent_inode, attr_only=True)
        return self._attributes(node)

    async def unlink(self, parent_inode: int, name: bytes, ctx: pyfuse3.RequestContext) -> None:
        del ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        node = self.image.lookup(parent_inode, name)
        if node is not None and node.inode in self._handles.values():
            raise pyfuse3.FUSEError(errno.EBUSY)
        try:
            if node is not None:
                self._commit_metadata(node.inode)
            self.image.remove(parent_inode, name, directory=False)
        except Exception as exc:
            self._raise_fuse(exc)
        if node is not None:
            self._metadata_updates.pop(node.inode, None)
            self._invalidate_inode(node.inode)
        self._invalidate_entry(parent_inode, name, deleted=node.inode if node else 0)
        self._invalidate_inode(parent_inode, attr_only=True)

    async def rmdir(self, parent_inode: int, name: bytes, ctx: pyfuse3.RequestContext) -> None:
        del ctx
        if not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        node = self.image.lookup(parent_inode, name)
        try:
            if node is not None:
                self._commit_metadata(node.inode)
            self.image.remove(parent_inode, name, directory=True)
        except Exception as exc:
            self._raise_fuse(exc)
        if node is not None:
            self._metadata_updates.pop(node.inode, None)
            self._invalidate_inode(node.inode)
        self._invalidate_entry(parent_inode, name, deleted=node.inode if node else 0)
        self._invalidate_inode(parent_inode, attr_only=True)

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
        source = self.image.lookup(parent_inode_old, name_old)
        try:
            if source is not None:
                self._commit_metadata(source.inode)
            self.image.rename(parent_inode_old, name_old, parent_inode_new, name_new)
        except Exception as exc:
            self._raise_fuse(exc)
        self._invalidate_entry(
            parent_inode_old, name_old, deleted=source.inode if source is not None else 0
        )
        self._invalidate_entry(parent_inode_new, name_new)
        self._invalidate_inode(parent_inode_old, attr_only=True)
        if parent_inode_new != parent_inode_old:
            self._invalidate_inode(parent_inode_new, attr_only=True)
        if source is not None:
            self._invalidate_inode(source.inode)

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
            data = self._write_buffer(inode)
            new_size = attr.st_size
            try:
                self.image.preflight_file_size(inode, new_size)
            except Exception as exc:
                self._raise_fuse(exc)
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

    def _commit_buffer(self, inode: int) -> None:
        if inode not in self._dirty:
            return
        self.image.replace_file(inode, bytes(self._write_buffers[inode]))
        self._dirty.discard(inode)
        self._invalidate_inode(inode)
        if inode not in self._handles.values():
            self._write_buffers.pop(inode, None)

    def _commit_metadata(self, inode: int) -> None:
        update = self._metadata_updates.get(inode)
        if update is None:
            return
        self.image.set_acorn_metadata(
            inode,
            load_address=update.load_address,
            exec_address=update.exec_address,
            filetype=update.filetype,
            locked=update.locked,
        )
        self._metadata_updates.pop(inode, None)
        self._invalidate_inode(inode)

    def _flush_inode(self, inode: int) -> None:
        try:
            self._commit_buffer(inode)
        except Exception as exc:
            self._raise_fuse(exc)

    def flush_pending(self) -> None:
        """Durably commit every dirty per-inode buffer during graceful shutdown."""

        try:
            for inode in sorted(self._dirty):
                self._commit_buffer(inode)
            for inode in sorted(self._metadata_updates):
                self._commit_metadata(inode)
        except Exception as exc:
            raise AcornFSError(
                _("Could not flush pending FUSE data and metadata safely: {error}").format(
                    error=exc
                )
            ) from exc

    async def flush(self, fh: int) -> None:
        inode = self._handle_inode(fh)
        self._flush_inode(inode)
        try:
            self._commit_metadata(inode)
        except Exception as exc:
            self._raise_fuse(exc)

    async def fsync(self, fh: int, datasync: bool) -> None:
        del datasync
        await self.flush(fh)

    async def release(self, fh: int) -> None:
        await self.flush(fh)
        inode = self._handles.pop(fh, None)
        self._clear_read_ahead(fh)
        self._read_states.pop(fh, None)
        if inode is not None and inode not in self._handles.values():
            self._write_buffers.pop(inode, None)

    async def access(self, inode: int, mode: int, ctx: pyfuse3.RequestContext) -> bool:
        node = self._node(inode)
        if mode & os.W_OK and not self.image.writable:
            raise pyfuse3.FUSEError(errno.EROFS)
        if mode & os.W_OK and self._node_locked(node):
            raise pyfuse3.FUSEError(errno.EACCES)
        return True

    async def listxattr(self, inode: int, ctx: pyfuse3.RequestContext) -> tuple[bytes, ...]:
        del ctx
        node = self._node(inode)
        if node.inode == ROOT_INODE:
            return (XATTR_SOURCE, XATTR_PATH)
        if not self.image.has_acorn_metadata:
            return (XATTR_SOURCE, XATTR_PATH)
        names: tuple[bytes, ...] = XATTR_NAMES
        if self.image.source.filesystem == "acorn-romfs":
            names = (*names, XATTR_RUN_ONLY)
        update = self._metadata_updates.get(inode)
        if (update is not None and update.filetype is not None) or self.image.filetype(
            inode
        ) is not None:
            names = (*names, XATTR_FILETYPE)
        return names

    async def getxattr(self, inode: int, name: bytes, ctx: pyfuse3.RequestContext) -> bytes:
        del ctx
        node = self._node(inode)
        if name == XATTR_SOURCE:
            return self.image.source.filesystem.encode("ascii")
        if name == XATTR_PATH:
            return node.acorn_path.encode("utf-8")
        try:
            metadata = self.image.acorn_metadata(inode)
        except ValueError as exc:
            raise pyfuse3.FUSEError(errno.ENODATA) from exc
        if name == XATTR_LOAD:
            value = metadata.load_address
            if (update := self._metadata_updates.get(inode)) is not None:
                value = update.load_address if update.load_address is not None else value
            return f"{int(value or 0):08X}".encode("ascii")
        if name == XATTR_EXECUTE:
            value = metadata.exec_address
            if (update := self._metadata_updates.get(inode)) is not None:
                value = update.exec_address if update.exec_address is not None else value
            return f"{int(value or 0):08X}".encode("ascii")
        if name == XATTR_LOCKED:
            return b"1" if self._node_locked(node) else b"0"
        if name == XATTR_RUN_ONLY and self.image.source.filesystem == "acorn-romfs":
            return b"1" if node.run_only else b"0"
        if name == XATTR_FILETYPE:
            update = self._metadata_updates.get(inode)
            filetype = update.filetype if update is not None else None
            if filetype is None:
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
        self._node(inode)
        update = self._metadata_updates.get(inode) or _MetadataUpdate()
        try:
            text = value.decode("ascii").strip()
            if name == XATTR_LOAD:
                if len(text) != 8:
                    raise ValueError("load address must be exactly 8 hexadecimal digits")
                update.load_address = int(text, 16)
            elif name == XATTR_EXECUTE:
                if len(text) != 8:
                    raise ValueError("execute address must be exactly 8 hexadecimal digits")
                update.exec_address = int(text, 16)
            elif name == XATTR_FILETYPE:
                if len(text) != 3:
                    raise ValueError("filetype must be exactly 3 hexadecimal digits")
                filetype = int(text, 16)
                if not 0 <= filetype <= 0xFFF:
                    raise ValueError("filetype must be 000..FFF")
                update.filetype = filetype
            elif name == XATTR_LOCKED:
                normalised = text.casefold()
                if normalised not in {"0", "1", "false", "true"}:
                    raise ValueError("locked must be 0, 1, false or true")
                update.locked = normalised in {"1", "true"}
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
        self._metadata_updates[inode] = update
        self._invalidate_inode(inode)

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
