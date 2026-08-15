"""Cached view of an Acorn filesystem image, read-only unless explicitly writable."""

from __future__ import annotations

import fcntl
import mmap
import os
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, cast

from oaknut.adfs.exceptions import ADFSError
from oaknut.file import AcornMeta
from oaknut.filesystem import create_filesystem, geometry_from_dsc
from oaknut.filesystem.capabilities import Mount
from oaknut.filesystem.reader import ImageReader

from acornfs.core.beebscsi import BeebSCSIPair, discover_pair, inspect_pair
from acornfs.errors import AcornFSError

if TYPE_CHECKING:
    from acornfs.recovery import RecoveryCheckpoint

ROOT_INODE = 1
DEFAULT_CACHE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_NODES = 100_000
DEFAULT_MAX_DEPTH = 256


@dataclass(frozen=True, slots=True)
class ImageNode:
    """One immutable entry in the mounted directory index."""

    inode: int
    parent_inode: int
    name: bytes
    acorn_path: str
    is_dir: bool
    size: int
    locked: bool = False


def _display_name(name: str) -> bytes:
    """Map characters POSIX cannot represent to unambiguous Unicode glyphs."""

    mapped: list[str] = []
    for character in name:
        codepoint = ord(character)
        if character == "/":
            mapped.append("∕")
        elif codepoint < 32:
            mapped.append(chr(0x2400 + codepoint))
        elif codepoint == 127:
            mapped.append("␡")
        else:
            mapped.append(character)
    result = "".join(mapped)
    if result == ".":
        result = "．"
    elif result == "..":
        result = "．．"
    return result.encode("utf-8")


def _locked_reader(pair: BeebSCSIPair, *, writable: bool) -> tuple[ImageReader, tuple[Any, ...]]:
    """Lock both pair members and map the DAT for the requested access mode."""

    mode = "r+b" if writable else "rb"
    lock_mode = fcntl.LOCK_EX if writable else fcntl.LOCK_SH
    dat_lock = pair.dat_path.open(mode)
    dsc_lock = pair.dsc_path.open(mode)
    mapping_handle = pair.dat_path.open(mode)
    try:
        fcntl.flock(dat_lock, lock_mode | fcntl.LOCK_NB)
        fcntl.flock(dsc_lock, lock_mode | fcntl.LOCK_NB)
        access = mmap.ACCESS_WRITE if writable else mmap.ACCESS_COPY
        mapping = mmap.mmap(mapping_handle.fileno(), 0, access=access)
    except Exception:
        mapping_handle.close()
        dat_lock.close()
        dsc_lock.close()
        raise
    reader = ImageReader(mapping, suffix=pair.dat_path.suffix, writable=True)
    return reader, (mapping, mapping_handle, dat_lock, dsc_lock)


class ReadOnlyImage:
    """An eagerly indexed ADFS tree backed by one long-lived Oaknut mount."""

    def __init__(
        self,
        *,
        pair: BeebSCSIPair,
        reader: ImageReader,
        mount: Mount,
        cache_bytes: int = DEFAULT_CACHE_BYTES,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        writable: bool = False,
        closeables: tuple[Any, ...] = (),
        checkpoint: RecoveryCheckpoint | None = None,
    ) -> None:
        self.pair = pair
        self._reader = reader
        self._mount = mount
        self._cache_limit = cache_bytes
        self._max_nodes = max_nodes
        self.max_nodes = max_nodes
        self._max_depth = max_depth
        self.writable = writable
        self._closeables = closeables
        self._checkpoint = checkpoint
        self._mutation_lock = RLock()
        self._failed = False
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_size = 0
        self._closed = False
        self.timestamp_ns = pair.dat_path.stat().st_mtime_ns
        self.nodes: dict[int, ImageNode] = {}
        self.children: dict[int, tuple[int, ...]] = {}
        self.children_by_name: dict[int, dict[bytes, int]] = {}
        self._index_tree()
        self._next_inode = max(self.nodes) + 1
        self.total_bytes = self._reported_size(pair.dat_path.stat().st_size)
        self.free_bytes = self._reported_free_space()
        self._expected_signature = self._current_signature()

    @classmethod
    def open(
        cls,
        selected: str | Path,
        *,
        cache_bytes: int = DEFAULT_CACHE_BYTES,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        writable: bool = False,
    ) -> ReadOnlyImage:
        """Validate and open a DAT/DSC image, read-only unless explicitly writable."""

        inspect_pair(selected)
        pair = discover_pair(selected)
        reader: ImageReader | None = None
        mount: Mount | None = None
        try:
            geometry = geometry_from_dsc(pair.dsc_path.read_bytes())
            reader, closeables = _locked_reader(pair, writable=writable)
            mount = create_filesystem("adfs").open(reader, geometry)
            image = cls(
                pair=pair,
                reader=reader,
                mount=mount,
                cache_bytes=cache_bytes,
                max_nodes=max_nodes,
                max_depth=max_depth,
                writable=writable,
                closeables=closeables,
            )
            if writable:
                try:
                    from acornfs.recovery import RecoveryCheckpoint

                    image._checkpoint = RecoveryCheckpoint.create(pair)
                except Exception:
                    image.close(clean=False)
                    raise
            return image
        except AcornFSError:
            raise
        except Exception as exc:
            if mount is not None:
                cls._close_oaknut_mount(mount)
            if reader is not None:
                reader.close()
            for closeable in closeables if "closeables" in locals() else ():
                with suppress(Exception):
                    closeable.close()
            raise AcornFSError(f"The ADFS image could not be opened safely: {exc}") from exc

    def _index_tree(self) -> None:
        root_path = self._mount.path_root()
        root = self._mount.stat(root_path)
        self.nodes[ROOT_INODE] = ImageNode(
            inode=ROOT_INODE,
            parent_inode=ROOT_INODE,
            name=b"",
            acorn_path=root_path,
            is_dir=True,
            size=root.length,
            locked=False,
        )
        active_paths: set[str] = set()

        def visit(parent_inode: int, path: str, depth: int) -> None:
            if depth > self._max_depth:
                raise AcornFSError(f"The ADFS directory tree exceeds {self._max_depth} levels.")
            path_key = path.casefold()
            if path_key in active_paths:
                raise AcornFSError(f"The ADFS directory tree contains a cycle at {path}.")
            active_paths.add(path_key)
            child_inodes: list[int] = []
            names: dict[bytes, int] = {}
            try:
                entries = sorted(
                    self._mount.iter_entries(path), key=lambda entry: entry.name.casefold()
                )
                for entry in entries:
                    if len(self.nodes) >= self._max_nodes:
                        raise AcornFSError(
                            f"The ADFS image contains more than {self._max_nodes} entries."
                        )
                    encoded_name = _display_name(entry.name)
                    if encoded_name in names:
                        raise AcornFSError(f"Two entries in {path} map to the same Linux filename.")
                    inode = len(self.nodes) + 1
                    node = ImageNode(
                        inode=inode,
                        parent_inode=parent_inode,
                        name=encoded_name,
                        acorn_path=entry.path,
                        is_dir=entry.is_dir,
                        size=entry.length,
                        locked=bool(cast(Any, self._mount).acorn_meta(entry.path).access & 8),
                    )
                    self.nodes[inode] = node
                    child_inodes.append(inode)
                    names[encoded_name] = inode
                    if entry.is_dir:
                        visit(inode, entry.path, depth + 1)
                self.children[parent_inode] = tuple(child_inodes)
                self.children_by_name[parent_inode] = names
            finally:
                active_paths.remove(path_key)

        visit(ROOT_INODE, root_path, 0)

    def lookup(self, parent_inode: int, name: bytes) -> ImageNode | None:
        names = self.children_by_name.get(parent_inode, {})
        inode = names.get(name)
        if inode is None:
            try:
                wanted = name.decode("utf-8").casefold()
                inode = next(
                    (
                        child_inode
                        for displayed, child_inode in names.items()
                        if displayed.decode("utf-8").casefold() == wanted
                    ),
                    None,
                )
            except UnicodeDecodeError:
                inode = None
        return None if inode is None else self.nodes[inode]

    def read(self, inode: int, offset: int, size: int) -> bytes:
        node = self.nodes[inode]
        if node.is_dir:
            raise IsADirectoryError(node.acorn_path)
        data = self._cached_file(inode, node)
        return data[offset : offset + size]

    def replace_file(self, inode: int, data: bytes) -> None:
        """Replace one file and make the new data visible immediately."""

        with self._mutation():
            node = self.nodes[inode]
            if node.is_dir:
                raise IsADirectoryError(node.acorn_path)
            metadata_api = cast(Any, self._mount)
            metadata = metadata_api.acorn_meta(node.acorn_path)
            self._mount.write_bytes(node.acorn_path, data)
            metadata_api.set_acorn_meta(node.acorn_path, metadata)
            self.nodes[inode] = replace(node, size=len(data))
            old_data = self._cache.pop(inode, None)
            if old_data is not None:
                self._cache_size -= len(old_data)
            if len(data) <= self._cache_limit:
                self._cache[inode] = data
                self._cache_size += len(data)
            self._finish_mutation()

    def acorn_metadata(self, inode: int) -> AcornMeta:
        node = self.nodes[inode]
        if node.inode == ROOT_INODE:
            raise ValueError("the filesystem root has no file metadata")
        return cast(AcornMeta, cast(Any, self._mount).acorn_meta(node.acorn_path))

    def set_acorn_metadata(
        self,
        inode: int,
        *,
        load_address: int | None = None,
        exec_address: int | None = None,
        locked: bool | None = None,
        filetype: int | None = None,
    ) -> None:
        with self._mutation():
            node = self.nodes[inode]
            if node.inode == ROOT_INODE:
                raise ValueError("the filesystem root has no file metadata")
            api = cast(Any, self._mount)
            current = cast(AcornMeta, api.acorn_meta(node.acorn_path))
            access = int(current.access or 0)
            if locked is not None:
                access = (access | 8) if locked else (access & ~8)
            replacement = AcornMeta(
                load_address=current.load_address if load_address is None else load_address,
                exec_address=current.exec_address if exec_address is None else exec_address,
                access=access,
            )
            api.set_acorn_meta(node.acorn_path, replacement)
            if filetype is not None:
                api.set_filetype(node.acorn_path, filetype)
            self.nodes[inode] = replace(node, locked=bool(access & 8))
            self._finish_mutation()

    def filetype(self, inode: int) -> int | None:
        node = self.nodes[inode]
        if node.inode == ROOT_INODE:
            return None
        return cast(int | None, cast(Any, self._mount).filetype(node.acorn_path))

    @staticmethod
    def _new_name(name: bytes) -> str:
        try:
            displayed = name.decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise ValueError("ADFS filenames must use 7-bit ASCII") from exc
        decoded = "".join(
            "/"
            if character == "∕"
            else chr(ord(character) - 0x2400)
            if 0x2400 <= ord(character) <= 0x241F
            else chr(127)
            if character == "␡"
            else character
            for character in displayed
        )
        try:
            encoded = decoded.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("ADFS filenames must use 7-bit ASCII") from exc
        if not encoded or len(encoded) > 10:
            raise ValueError("ADFS filenames must contain between 1 and 10 bytes")
        if any(character in decoded for character in ".:\r"):
            raise ValueError("ADFS filenames cannot contain '.', ':' or carriage return")
        return decoded

    def _child_path(self, parent_inode: int, name: str) -> str:
        parent = self.nodes[parent_inode]
        if not parent.is_dir:
            raise NotADirectoryError(parent.acorn_path)
        return cast(str, self._mount.join(parent.acorn_path, name))

    def _add_node(self, parent_inode: int, name: str, *, is_dir: bool, size: int = 0) -> ImageNode:
        encoded = _display_name(name)
        inode = self._next_inode
        self._next_inode += 1
        node = ImageNode(
            inode=inode,
            parent_inode=parent_inode,
            name=encoded,
            acorn_path=self._child_path(parent_inode, name),
            is_dir=is_dir,
            size=size,
        )
        self.nodes[inode] = node
        self.children[parent_inode] = (*self.children.get(parent_inode, ()), inode)
        self.children_by_name.setdefault(parent_inode, {})[encoded] = inode
        if is_dir:
            self.children[inode] = ()
            self.children_by_name[inode] = {}
        return node

    def create_file(self, parent_inode: int, name: bytes) -> ImageNode:
        with self._mutation():
            decoded = self._new_name(name)
            path = self._child_path(parent_inode, decoded)
            self._mount.write_bytes(path, b"")
            node = self._add_node(parent_inode, decoded, is_dir=False)
            self._finish_mutation()
            return node

    def make_directory(self, parent_inode: int, name: bytes) -> ImageNode:
        with self._mutation():
            decoded = self._new_name(name)
            path = self._child_path(parent_inode, decoded)
            maker = cast(Any, self._mount).make_directory
            maker(path)
            node = self._add_node(parent_inode, decoded, is_dir=True)
            self._finish_mutation()
            return node

    def remove(self, parent_inode: int, name: bytes, *, directory: bool) -> None:
        with self._mutation():
            node = self.lookup(parent_inode, name)
            if node is None:
                raise FileNotFoundError(name)
            if node.is_dir != directory:
                if node.is_dir:
                    raise IsADirectoryError(node.acorn_path)
                raise NotADirectoryError(node.acorn_path)
            self._mount.remove(node.acorn_path)
            self._drop_node(node)
            self._finish_mutation()

    def _drop_node(self, node: ImageNode) -> None:
        self.children[node.parent_inode] = tuple(
            inode for inode in self.children[node.parent_inode] if inode != node.inode
        )
        self.children_by_name[node.parent_inode].pop(node.name, None)
        self.children.pop(node.inode, None)
        self.children_by_name.pop(node.inode, None)
        self.nodes.pop(node.inode)
        old_data = self._cache.pop(node.inode, None)
        if old_data is not None:
            self._cache_size -= len(old_data)

    def rename(
        self, old_parent: int, old_name: bytes, new_parent: int, new_name: bytes
    ) -> ImageNode:
        with self._mutation():
            node = self.lookup(old_parent, old_name)
            if node is None:
                raise FileNotFoundError(old_name)
            decoded = self._new_name(new_name)
            new_path = self._child_path(new_parent, decoded)
            old_path = node.acorn_path
            destination = self.lookup(new_parent, new_name)
            if destination is node:
                return node
            destination_data: bytes | None = None
            destination_metadata: Any = None
            if destination is not None:
                if destination.is_dir != node.is_dir:
                    if destination.is_dir:
                        raise IsADirectoryError(destination.acorn_path)
                    raise NotADirectoryError(destination.acorn_path)
                if not destination.is_dir:
                    destination_data = self._mount.read_bytes(destination.acorn_path)
                    destination_metadata = cast(Any, self._mount).acorn_meta(destination.acorn_path)
                self._mount.remove(destination.acorn_path)
            try:
                self._mount.rename(old_path, new_path)
            except Exception:
                if destination is not None:
                    try:
                        if destination.is_dir:
                            cast(Any, self._mount).make_directory(destination.acorn_path)
                        else:
                            self._mount.write_bytes(destination.acorn_path, destination_data or b"")
                            cast(Any, self._mount).set_acorn_meta(
                                destination.acorn_path, destination_metadata
                            )
                    except Exception:
                        self._failed = True
                raise
            if destination is not None:
                self._drop_node(destination)
            self.children[old_parent] = tuple(
                inode for inode in self.children[old_parent] if inode != node.inode
            )
            self.children_by_name[old_parent].pop(node.name, None)
            new_encoded = _display_name(decoded)
            self.children[new_parent] = (*self.children.get(new_parent, ()), node.inode)
            self.children_by_name.setdefault(new_parent, {})[new_encoded] = node.inode
            self.nodes[node.inode] = replace(
                node,
                parent_inode=new_parent,
                name=new_encoded,
                acorn_path=new_path,
            )
            prefix = f"{old_path}."
            for inode, descendant in tuple(self.nodes.items()):
                if descendant.acorn_path.startswith(prefix):
                    suffix = descendant.acorn_path[len(old_path) :]
                    self.nodes[inode] = replace(descendant, acorn_path=f"{new_path}{suffix}")
            self._finish_mutation()
            return self.nodes[node.inode]

    def _current_signature(self) -> tuple[int, int, int, int, int]:
        open_stat = os.fstat(self._closeables[1].fileno())
        path_stat = self.pair.dat_path.stat()
        return (
            path_stat.st_dev,
            path_stat.st_ino,
            open_stat.st_size,
            open_stat.st_mtime_ns,
            open_stat.st_ctime_ns,
        )

    def _prepare_mutation(self) -> None:
        if not self.writable:
            raise PermissionError("image is read-only")
        if self._failed:
            raise AcornFSError("The writable session has failed; unmount and recover the image.")
        if self._current_signature() != self._expected_signature:
            self._failed = True
            raise AcornFSError("The DAT image changed outside AcornFS; further writes are blocked.")

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        with self._mutation_lock:
            self._prepare_mutation()
            try:
                yield
            except (
                ADFSError,
                FileNotFoundError,
                IsADirectoryError,
                NotADirectoryError,
                PermissionError,
                ValueError,
            ):
                raise
            except Exception:
                self._failed = True
                raise

    def _finish_mutation(self) -> None:
        self.sync()
        self.free_bytes = self._reported_free_space()

    def sync(self) -> None:
        """Flush writable image changes through to stable storage."""

        if not self.writable:
            return
        mapping = self._closeables[0]
        mapping.flush()
        mapping_handle = self._closeables[1]
        os.fsync(mapping_handle.fileno())
        self._expected_signature = self._current_signature()

    def _cached_file(self, inode: int, node: ImageNode) -> bytes:
        cached = self._cache.pop(inode, None)
        if cached is not None:
            self._cache[inode] = cached
            return cached
        data = cast(bytes, self._mount.read_bytes(node.acorn_path))
        if len(data) <= self._cache_limit:
            while self._cache and self._cache_size + len(data) > self._cache_limit:
                _old_inode, old_data = self._cache.popitem(last=False)
                self._cache_size -= len(old_data)
            self._cache[inode] = data
            self._cache_size += len(data)
        return data

    def _reported_size(self, fallback: int) -> int:
        reporter = getattr(self._mount, "size_bytes", None)
        return int(reporter()) if callable(reporter) else fallback

    def _reported_free_space(self) -> int:
        reporter = getattr(self._mount, "free_bytes", None)
        return int(reporter()) if callable(reporter) else 0

    @staticmethod
    def _close_oaknut_mount(mount: Mount) -> None:
        adfs = getattr(mount, "_adfs", None)
        close = getattr(adfs, "close", None)
        if callable(close):
            close()

    def close(self, *, clean: bool = True) -> None:
        if self._closed:
            return
        close_error: Exception | None = None
        if self.writable:
            try:
                self.sync()
                validator = getattr(self._mount, "validate", None)
                problems = validator() if clean and callable(validator) else []
                if problems:
                    self._failed = True
                    close_error = AcornFSError(
                        f"Post-write ADFS validation found {len(problems)} problem(s); "
                        "the recovery checkpoint was retained."
                    )
            except Exception as exc:
                self._failed = True
                close_error = AcornFSError(f"Could not safely finalise the writable image: {exc}")
        self._cache.clear()
        self._cache_size = 0
        self._close_oaknut_mount(self._mount)
        self._reader.close()
        for closeable in self._closeables:
            with suppress(Exception):
                closeable.close()
        self._closeables = ()
        self._closed = True
        if self.writable and clean and not self._failed and self._checkpoint is not None:
            self._checkpoint.complete()
        if close_error is not None:
            raise close_error

    def __enter__(self) -> ReadOnlyImage:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        try:
            self.close(clean=exc_info[0] is None)
        except Exception:
            if exc_info[0] is None:
                raise
