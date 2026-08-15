"""Cached, read-only view of an Acorn filesystem image."""

from __future__ import annotations

import mmap
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from oaknut.filesystem import create_filesystem, geometry_from_dsc
from oaknut.filesystem.capabilities import Mount
from oaknut.filesystem.reader import ImageReader

from acornfs.core.beebscsi import BeebSCSIPair, discover_pair, inspect_pair
from acornfs.errors import AcornFSError

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


def _private_reader(path: Path) -> ImageReader:
    """Map an image copy-on-write: demand-paged, mutable to Oaknut, safe on disk."""

    handle = path.open("rb")
    try:
        mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_COPY)
    except Exception:
        handle.close()
        raise
    return ImageReader(
        mapping,
        suffix=path.suffix,
        writable=True,
        _closeables=(mapping, handle),
    )


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
    ) -> None:
        self.pair = pair
        self._reader = reader
        self._mount = mount
        self._cache_limit = cache_bytes
        self._max_nodes = max_nodes
        self._max_depth = max_depth
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_size = 0
        self._closed = False
        self.timestamp_ns = pair.dat_path.stat().st_mtime_ns
        self.nodes: dict[int, ImageNode] = {}
        self.children: dict[int, tuple[int, ...]] = {}
        self.children_by_name: dict[int, dict[bytes, int]] = {}
        self._index_tree()
        self.total_bytes = self._reported_size(pair.dat_path.stat().st_size)
        self.free_bytes = self._reported_free_space()

    @classmethod
    def open(
        cls,
        selected: str | Path,
        *,
        cache_bytes: int = DEFAULT_CACHE_BYTES,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> ReadOnlyImage:
        """Validate and open a DAT/DSC image without granting write access."""

        inspect_pair(selected)
        pair = discover_pair(selected)
        reader: ImageReader | None = None
        mount: Mount | None = None
        try:
            geometry = geometry_from_dsc(pair.dsc_path.read_bytes())
            reader = _private_reader(pair.dat_path)
            mount = create_filesystem("adfs").open(reader, geometry)
            return cls(
                pair=pair,
                reader=reader,
                mount=mount,
                cache_bytes=cache_bytes,
                max_nodes=max_nodes,
                max_depth=max_depth,
            )
        except AcornFSError:
            raise
        except Exception as exc:
            if mount is not None:
                cls._close_oaknut_mount(mount)
            if reader is not None:
                reader.close()
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
        inode = self.children_by_name.get(parent_inode, {}).get(name)
        return None if inode is None else self.nodes[inode]

    def read(self, inode: int, offset: int, size: int) -> bytes:
        node = self.nodes[inode]
        if node.is_dir:
            raise IsADirectoryError(node.acorn_path)
        data = self._cached_file(inode, node)
        return data[offset : offset + size]

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

    def close(self) -> None:
        if self._closed:
            return
        self._cache.clear()
        self._cache_size = 0
        self._close_oaknut_mount(self._mount)
        self._reader.close()
        self._closed = True

    def __enter__(self) -> ReadOnlyImage:
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()
