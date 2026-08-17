"""Cached view of an Acorn filesystem image, read-only unless explicitly writable."""

from __future__ import annotations

import gc
import os
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, TypeVar, cast

from oaknut.adfs.exceptions import ADFSDiscFullError, ADFSError
from oaknut.file import Access, AcornMeta
from oaknut.filesystem import (
    AcornMetadata,
    Filetyped,
    FreeSpace,
    HierarchicalDirectories,
    Sized,
    create_filesystem,
    geometry_from_dsc,
)
from oaknut.filesystem.capabilities import Mount
from oaknut.filesystem.reader import ImageReader

from acornfs.core.beebscsi import (
    BeebSCSIGeometry,
    open_locked_reader,
    parse_descriptor,
)
from acornfs.core.dfs import DoubleSidedDFSMount
from acornfs.core.formats import ResolvedImage, resolve_image
from acornfs.core.mmb import MMBMount
from acornfs.core.storage import open_locked_image
from acornfs.core.transaction import FileTransaction, SectorTransaction
from acornfs.errors import AcornFSError, FilenameTooLongError
from acornfs.i18n import _, ngettext

if TYPE_CHECKING:
    from acornfs.core.validation import IntegrityReport
    from acornfs.operations import OperationBudget
    from acornfs.recovery import RecoveryCheckpoint, SingleImageCheckpoint

ROOT_INODE = 1
DEFAULT_CACHE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_NODES = 100_000
DEFAULT_MAX_DEPTH = 256
ADFS_SECTOR_BYTES = 256
T = TypeVar("T")


class _MutationRolledBack(Exception):
    """Carry the original failure through the mutation guard after rollback."""

    def __init__(self, original: Exception) -> None:
        super().__init__(str(original))
        self.original = original


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
    run_only: bool = False


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


class ReadOnlyImage:
    """An eagerly indexed ADFS tree backed by one long-lived Oaknut mount."""

    def __init__(
        self,
        *,
        source: ResolvedImage,
        reader: ImageReader,
        mount: Mount,
        cache_bytes: int = DEFAULT_CACHE_BYTES,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        writable: bool = False,
        closeables: tuple[Any, ...] = (),
        checkpoint: RecoveryCheckpoint | SingleImageCheckpoint | None = None,
        descriptor_geometry: BeebSCSIGeometry | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.source = source
        self.pair = source.pair
        self._reader = reader
        self._mount = mount
        self.has_acorn_metadata = isinstance(mount, AcornMetadata)
        self.has_filetypes = isinstance(mount, Filetyped)
        self._cache_limit = cache_bytes
        self._max_nodes = max_nodes
        self.max_nodes = max_nodes
        self._max_depth = max_depth
        self.writable = writable
        self._closeables = closeables
        self._checkpoint = checkpoint
        self._descriptor_geometry = descriptor_geometry
        self._fault_injector = fault_injector
        self._mutation_lock = RLock()
        self._failed = False
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_size = 0
        self._closed = False
        self.timestamp_ns = source.primary_path.stat().st_mtime_ns
        self.nodes: dict[int, ImageNode] = {}
        self.children: dict[int, tuple[int, ...]] = {}
        self.children_by_name: dict[int, dict[bytes, int]] = {}
        self._index_tree()
        self._next_inode = max(self.nodes) + 1
        self.total_bytes = self._reported_size(source.primary_path.stat().st_size)
        self.free_bytes = self._reported_free_space()
        self._expected_signature = self._current_signature() if writable else None

    @classmethod
    def open(
        cls,
        selected: str | Path,
        *,
        cache_bytes: int = DEFAULT_CACHE_BYTES,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        writable: bool = False,
        repair_mode: bool = False,
        fault_injector: Callable[[str], None] | None = None,
        checkpoint_progress: Callable[[int, int], None] | None = None,
        operation_budget: OperationBudget | None = None,
    ) -> ReadOnlyImage:
        """Detect and open a supported image, read-only unless explicitly writable."""

        source = resolve_image(selected)
        if writable and not source.capabilities.mount_read_write:
            raise AcornFSError(_("Read-write mounting is not supported for this image format."))
        pair = source.pair
        reader: ImageReader | None = None
        mount: Mount | None = None
        try:
            descriptor_geometry: BeebSCSIGeometry | None = None
            closeables: tuple[Any, ...] = ()
            if pair is not None:
                reader, closeables, descriptor = open_locked_reader(pair, writable=writable)
                descriptor_geometry = parse_descriptor(descriptor)
                geometry = geometry_from_dsc(descriptor)
            else:
                reader, closeables = open_locked_image(
                    source.primary_path,
                    writable=writable,
                    companion=source.companion_path,
                )
                geometry = source.geometry
            if source.mmb_layout is not None:
                mount = cast(Mount, MMBMount(reader, source.mmb_layout))
            else:
                filesystem = create_filesystem(source.filesystem)
                mount = filesystem.open(reader, geometry)
                if source.kind == "dfs-floppy" and len(geometry.surface_specs) > 1:
                    side_two = filesystem.open(reader, geometry, surface=1)
                    mount = cast(Mount, DoubleSidedDFSMount(mount, side_two))
            if writable:
                cls._install_oaknut_write_compatibility(mount)
                from acornfs.core.validation import require_safe_for_write, validate_open_mount

                if pair is not None and descriptor_geometry is not None:
                    report = validate_open_mount(
                        pair, mount, descriptor_geometry, budget=operation_budget
                    )
                    require_safe_for_write(report)
                    if not repair_mode and any(
                        finding.code == "geometry.dat_missing_reserved_tail"
                        for finding in report.findings
                    ):
                        raise AcornFSError(
                            _(
                                "Writable mount refused: the DAT omits a repairable reserved "
                                "tail. Run the low-risk repair first."
                            )
                        )
                else:
                    cls._require_generic_integrity(mount)
            image = cls(
                source=source,
                reader=reader,
                mount=mount,
                cache_bytes=cache_bytes,
                max_nodes=max_nodes,
                max_depth=max_depth,
                writable=writable,
                closeables=closeables,
                descriptor_geometry=descriptor_geometry,
                fault_injector=fault_injector,
            )
            # Ownership has transferred to the image. This also prevents the
            # outer exception handler from closing the same resources twice if
            # checkpoint creation fails and image.close() handles them.
            reader = None
            mount = None
            closeables = ()
            if writable:
                try:
                    from acornfs.recovery import RecoveryCheckpoint, SingleImageCheckpoint

                    if pair is not None:
                        image._checkpoint = RecoveryCheckpoint.create(
                            pair,
                            progress=checkpoint_progress,
                            source_handles=(image._closeables[1], image._closeables[3]),
                        )
                    else:
                        image._checkpoint = SingleImageCheckpoint.create(
                            source.primary_path,
                            progress=checkpoint_progress,
                            source_handle=image._closeables[1],
                        )
                    image._prepare_mutation()
                except Exception:
                    image.close(clean=False)
                    raise
            return image
        except Exception as exc:
            if mount is not None:
                cls._close_oaknut_mount(mount)
            if reader is not None:
                reader.close()
            for closeable in closeables if "closeables" in locals() else ():
                with suppress(Exception):
                    closeable.close()
            if isinstance(exc, AcornFSError):
                raise
            raise AcornFSError(
                _("The Acorn image could not be opened safely: {error}").format(error=exc)
            ) from exc

    @staticmethod
    def _require_generic_integrity(mount: Mount) -> None:
        validator = getattr(mount, "validate", None)
        if not callable(validator):
            raise AcornFSError(_("This image format has no structural validator."))
        problems = tuple(validator())
        if problems:
            raise AcornFSError(
                _("Writable mount refused because validation found: {error}").format(
                    error=problems[0]
                )
            )

    @staticmethod
    def _install_oaknut_write_compatibility(mount: Mount) -> None:
        """Bridge pinned Oaknut write edge cases until its public API covers them."""

        adfs = getattr(mount, "_adfs", None)
        if adfs is None or not getattr(adfs, "is_new_map", False):
            return
        allocate = adfs._allocate_file_space
        release = adfs._release_object

        def allocate_file_space(parent: Any, parent_address: int, data: bytes) -> int:
            if not data:
                return 0
            return cast(int, allocate(parent, parent_address, data))

        def release_object(parent: Any, parent_address: int, entry: Any) -> None:
            if int(entry.length) == 0:
                return
            release(parent, parent_address, entry)

        adfs._allocate_file_space = allocate_file_space
        adfs._release_object = release_object

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
                raise AcornFSError(
                    _("The Acorn filesystem tree exceeds {maximum} levels.").format(
                        maximum=self._max_depth
                    )
                )
            path_key = path if self.source.case_sensitive_names else path.casefold()
            if path_key in active_paths:
                raise AcornFSError(
                    _("The Acorn filesystem tree contains a cycle at {path}.").format(path=path)
                )
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
                            _("The Acorn image contains more than {maximum} entries.").format(
                                maximum=self._max_nodes
                            )
                        )
                    encoded_name = _display_name(entry.name)
                    if encoded_name in names:
                        raise AcornFSError(
                            _("Two entries in {path} map to the same Linux filename.").format(
                                path=path
                            )
                        )
                    inode = len(self.nodes) + 1
                    metadata = (
                        cast(AcornMetadata, self._mount).acorn_meta(entry.path)
                        if self.has_acorn_metadata
                        else None
                    )
                    node = ImageNode(
                        inode=inode,
                        parent_inode=parent_inode,
                        name=encoded_name,
                        acorn_path=entry.path,
                        is_dir=entry.is_dir,
                        size=entry.length,
                        locked=bool(metadata and metadata.access & int(Access.L)),
                        run_only=bool(metadata and metadata.access & int(Access.X)),
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
        if inode is None and not self.source.case_sensitive_names:
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

    def node_at_path(self, path: str) -> ImageNode:
        """Resolve one indexed Acorn path using the source filesystem's name rules."""

        wanted = path if self.source.case_sensitive_names else path.casefold()
        for node in self.nodes.values():
            candidate = (
                node.acorn_path if self.source.case_sensitive_names else node.acorn_path.casefold()
            )
            if candidate == wanted:
                return node
        raise FileNotFoundError(path)

    def read(self, inode: int, offset: int, size: int) -> bytes:
        node = self.nodes[inode]
        if node.is_dir:
            raise IsADirectoryError(node.acorn_path)
        if node.size > self._cache_limit:
            return self._read_range(node, offset, size)
        data = self._cached_file(inode, node)
        return data[offset : offset + size]

    def uses_ranged_reads(self, inode: int) -> bool:
        """Return whether this file bypasses the bounded whole-file cache."""

        node = self.nodes[inode]
        return not node.is_dir and node.size > self._cache_limit

    def _read_range(self, node: ImageNode, offset: int, size: int) -> bytes:
        """Read only the sectors needed for a range of an uncached large file."""

        if offset < 0 or size <= 0 or offset >= node.size:
            return b""
        end = min(node.size, offset + size)
        adfs = getattr(self._mount, "_adfs", None)
        if adfs is None or getattr(adfs, "is_new_map", False):
            return bytes(self._mount.read_bytes(node.acorn_path))[offset:end]
        _parent, entry = adfs.path(node.acorn_path)._resolve()
        first_sector = offset // ADFS_SECTOR_BYTES
        final_sector = (end + ADFS_SECTOR_BYTES - 1) // ADFS_SECTOR_BYTES
        data = adfs._disc.sector_range(
            int(entry.start_sector) + first_sector, final_sector - first_sector
        )
        start_in_sector = offset % ADFS_SECTOR_BYTES
        return bytes(data[start_in_sector : start_in_sector + end - offset])

    def replace_file(self, inode: int, data: bytes) -> None:
        """Replace one file and make the new data visible immediately."""

        node = self.nodes[inode]
        if node.is_dir:
            raise IsADirectoryError(node.acorn_path)
        metadata_api = cast(Any, self._mount)
        metadata: AcornMeta | None = None

        def prepare(transaction: SectorTransaction) -> None:
            nonlocal metadata
            self._preflight_file_size(node, len(data))
            metadata = cast(AcornMeta, metadata_api.acorn_meta(node.acorn_path))
            transaction.capture_parent(node.acorn_path)
            transaction.capture_file(node.acorn_path)

        def mutate() -> None:
            self._mount.write_bytes(node.acorn_path, data)
            if metadata is None:
                raise AcornFSError(_("File metadata was not captured before replacement."))
            metadata_api.set_acorn_meta(node.acorn_path, metadata)

        def commit() -> None:
            self.nodes[inode] = replace(node, size=len(data))
            self._cache_file(inode, data)

        self._run_atomic("replace", prepare=prepare, mutate=mutate, commit=commit)

    def import_file(
        self, parent_inode: int, name: bytes, data: bytes, metadata: AcornMeta
    ) -> ImageNode:
        """Create one file and its Acorn metadata as a single mutation."""

        decoded = self._new_name(name)
        if self.lookup(parent_inode, name) is not None:
            raise FileExistsError(decoded)
        for label, value in (
            ("load address", metadata.load_address),
            ("execution address", metadata.exec_address),
        ):
            if value is not None and not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{label} must fit in 32 bits")
        path = self._child_path(parent_inode, decoded)
        access = int(metadata.access or 0)
        if not 0 <= access <= 0xFF:
            raise ValueError(_("access metadata must fit in 8 bits"))
        replacement = AcornMeta(
            load_address=int(metadata.load_address or 0),
            exec_address=int(metadata.exec_address or 0),
            access=access,
        )
        metadata_api = cast(Any, self._mount)

        def mutate() -> None:
            self._mount.write_bytes(path, data)
            metadata_api.set_acorn_meta(path, replacement)

        def commit() -> ImageNode:
            node = self._add_node(parent_inode, decoded, is_dir=False, size=len(data))
            if access & 8:
                node = replace(node, locked=True)
                self.nodes[node.inode] = node
            self._cache_file(node.inode, data)
            return node

        return self._run_atomic(
            "import",
            prepare=lambda transaction: transaction.capture_parent(path),
            mutate=mutate,
            commit=commit,
        )

    def preflight_file_size(self, inode: int, new_size: int) -> None:
        """Reject a requested file size before a FUSE buffer is expanded."""

        if new_size < 0:
            raise ValueError(_("file size cannot be negative"))
        node = self.nodes[inode]
        if node.is_dir:
            raise IsADirectoryError(node.acorn_path)
        self._preflight_file_size(node, new_size)

    def _preflight_file_size(self, node: ImageNode, new_size: int) -> None:
        """Reject an overwrite before Oaknut frees data it cannot reallocate."""

        required = (new_size + ADFS_SECTOR_BYTES - 1) // ADFS_SECTOR_BYTES
        if required == 0:
            return
        adfs = getattr(self._mount, "_adfs", None)
        if adfs is None or getattr(adfs, "_fsm", None) is None:
            available_for_path = getattr(self._mount, "available_bytes", None)
            free_bytes = (
                int(available_for_path(node.acorn_path))
                if callable(available_for_path)
                else self.free_bytes
            )
            available = free_bytes + node.size
            if new_size > available:
                raise ADFSDiscFullError(
                    f"Image has {available} bytes available; {new_size} bytes were requested"
                )
            return
        try:
            _parent, entry = adfs.path(node.acorn_path)._resolve()
            extents = [
                (start // ADFS_SECTOR_BYTES, length // ADFS_SECTOR_BYTES)
                for start, length in adfs._fsm.free_space_entries()
            ]
            old_sectors = (node.size + ADFS_SECTOR_BYTES - 1) // ADFS_SECTOR_BYTES
            if old_sectors:
                extents.append((int(entry.start_sector), old_sectors))
        except Exception as exc:
            self._failed = True
            raise AcornFSError(
                _("Could not preflight the ADFS allocation safely: {error}").format(error=exc)
            ) from exc

        largest = 0
        current_start = -1
        current_end = -1
        for start, length in sorted(extents):
            end = start + length
            if current_start < 0 or start > current_end:
                if current_start >= 0:
                    largest = max(largest, current_end - current_start)
                current_start, current_end = start, end
            else:
                current_end = max(current_end, end)
        if current_start >= 0:
            largest = max(largest, current_end - current_start)
        if required > largest:
            raise ADFSDiscFullError(
                f"No contiguous extent can hold {required} sectors; largest available is {largest}"
            )

    def acorn_metadata(self, inode: int) -> AcornMeta:
        node = self.nodes[inode]
        if node.inode == ROOT_INODE:
            raise ValueError(_("the filesystem root has no file metadata"))
        if not self.has_acorn_metadata:
            raise ValueError(_("this filesystem does not provide Acorn metadata"))
        return cast(AcornMeta, cast(AcornMetadata, self._mount).acorn_meta(node.acorn_path))

    def set_acorn_metadata(
        self,
        inode: int,
        *,
        load_address: int | None = None,
        exec_address: int | None = None,
        locked: bool | None = None,
        filetype: int | None = None,
    ) -> None:
        node = self.nodes[inode]
        if node.inode == ROOT_INODE:
            raise ValueError(_("the filesystem root has no file metadata"))
        api = cast(Any, self._mount)
        access = 0
        replacement: AcornMeta | None = None

        def prepare(transaction: SectorTransaction) -> None:
            nonlocal access, replacement
            current = cast(AcornMeta, api.acorn_meta(node.acorn_path))
            access = int(current.access or 0)
            if locked is not None:
                access = (access | 8) if locked else (access & ~8)
            replacement = AcornMeta(
                load_address=current.load_address if load_address is None else load_address,
                exec_address=current.exec_address if exec_address is None else exec_address,
                access=access,
            )
            transaction.capture_parent(node.acorn_path)

        def mutate() -> None:
            if replacement is None:
                raise AcornFSError(_("File metadata was not captured before mutation."))
            api.set_acorn_meta(node.acorn_path, replacement)
            if filetype is not None:
                api.set_filetype(node.acorn_path, filetype)

        self._run_atomic(
            "metadata",
            prepare=prepare,
            mutate=mutate,
            commit=lambda: self.nodes.__setitem__(inode, replace(node, locked=bool(access & 8))),
        )

    def filetype(self, inode: int) -> int | None:
        node = self.nodes[inode]
        if node.inode == ROOT_INODE:
            return None
        if not self.has_filetypes:
            return None
        return cast(int | None, cast(Filetyped, self._mount).filetype(node.acorn_path))

    def _new_name(self, name: bytes) -> str:
        try:
            displayed = name.decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise ValueError(_("Acorn filenames must use 7-bit ASCII")) from exc
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
            raise ValueError(_("Acorn filenames must use 7-bit ASCII")) from exc
        if self.source.filesystem in {"acorn-dfs", "watford-dfs"}:
            maximum = 7
        else:
            adfs = getattr(self._mount, "_adfs", None)
            directory_format = type(adfs._dir_format).__name__ if adfs is not None else ""
            maximum = 255 if directory_format == "BigDirectoryFormat" else 10
        if not encoded:
            raise ValueError(
                _("Acorn filenames must contain between 1 and {maximum} bytes").format(
                    maximum=maximum
                )
            )
        if len(encoded) > maximum:
            raise FilenameTooLongError(
                _("Acorn filenames must contain between 1 and {maximum} bytes").format(
                    maximum=maximum
                )
            )
        if any(character in decoded for character in "\0.:\r"):
            raise ValueError(_("The filename contains a character unavailable in this format"))
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
        decoded = self._new_name(name)
        if self.lookup(parent_inode, name) is not None:
            raise FileExistsError(decoded)
        path = self._child_path(parent_inode, decoded)
        return self._run_atomic(
            "create",
            prepare=lambda transaction: transaction.capture_parent(path),
            mutate=lambda: self._mount.write_bytes(path, b""),
            commit=lambda: self._add_node(parent_inode, decoded, is_dir=False),
        )

    def make_directory(self, parent_inode: int, name: bytes) -> ImageNode:
        decoded = self._new_name(name)
        if self.lookup(parent_inode, name) is not None:
            raise FileExistsError(decoded)
        path = self._child_path(parent_inode, decoded)
        if not isinstance(self._mount, HierarchicalDirectories):
            raise PermissionError(_("This filesystem does not store directories."))
        maker = cast(HierarchicalDirectories, self._mount).make_directory
        return self._run_atomic(
            "mkdir",
            prepare=lambda transaction: transaction.capture_parent(path),
            mutate=lambda: maker(path),
            commit=lambda: self._add_node(parent_inode, decoded, is_dir=True),
        )

    def remove(self, parent_inode: int, name: bytes, *, directory: bool) -> None:
        node = self.lookup(parent_inode, name)
        if node is None:
            raise FileNotFoundError(name)
        if node.is_dir != directory:
            if node.is_dir:
                raise IsADirectoryError(node.acorn_path)
            raise NotADirectoryError(node.acorn_path)
        if directory and not isinstance(self._mount, HierarchicalDirectories):
            raise PermissionError(_("This filesystem does not store directories."))
        self._run_atomic(
            "rmdir" if directory else "unlink",
            prepare=lambda transaction: transaction.capture_parent(node.acorn_path),
            mutate=lambda: self._mount.remove(node.acorn_path),
            commit=lambda: self._drop_node(node),
        )

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
        node = self.lookup(old_parent, old_name)
        if node is None:
            raise FileNotFoundError(old_name)
        if node.locked:
            raise PermissionError(_("{path} is locked").format(path=node.acorn_path))
        if node.is_dir and not isinstance(self._mount, HierarchicalDirectories):
            raise PermissionError(_("This filesystem does not store directories."))
        if node.is_dir and self._is_descendant_or_self(new_parent, node.inode):
            raise ValueError(_("a directory cannot be moved inside itself"))
        decoded = self._new_name(new_name)
        new_path = self._child_path(new_parent, decoded)
        old_path = node.acorn_path
        destination = self.lookup(new_parent, new_name)
        if destination is node:
            return node
        if destination is not None:
            if destination.locked:
                raise PermissionError(_("{path} is locked").format(path=destination.acorn_path))
            if destination.is_dir != node.is_dir:
                if destination.is_dir:
                    raise IsADirectoryError(destination.acorn_path)
                raise NotADirectoryError(destination.acorn_path)

        destination_parent_sector = 0

        def prepare(transaction: SectorTransaction) -> None:
            nonlocal destination_parent_sector
            transaction.capture_parent(old_path)
            destination_parent_sector = transaction.capture_parent(new_path)
            if node.is_dir and self._uses_old_adfs_transactions:
                transaction.capture_directory(old_path)

        def mutate() -> None:
            if destination is not None:
                self._mount.remove(destination.acorn_path)
                self._fault("rename.destination_removed")
            self._mount.rename(old_path, new_path)
            if node.is_dir and self._uses_old_adfs_transactions:
                self._set_directory_identity(
                    new_path,
                    name=decoded,
                    parent_sector=destination_parent_sector if old_parent != new_parent else None,
                )

        def commit() -> ImageNode:
            return self._commit_rename(
                node,
                destination,
                old_parent=old_parent,
                new_parent=new_parent,
                decoded=decoded,
                old_path=old_path,
                new_path=new_path,
            )

        return self._run_atomic("rename", prepare=prepare, mutate=mutate, commit=commit)

    def _commit_rename(
        self,
        node: ImageNode,
        destination: ImageNode | None,
        *,
        old_parent: int,
        new_parent: int,
        decoded: str,
        old_path: str,
        new_path: str,
    ) -> ImageNode:
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
        return self.nodes[node.inode]

    def _set_directory_identity(self, path: str, *, name: str, parent_sector: int | None) -> None:
        adfs = cast(Any, self._mount)._adfs
        _parent, entry = adfs.path(path)._resolve()
        sector = int(entry.start_sector)
        directory = adfs._read_directory_at(sector)
        replacement = replace(directory, name=name)
        if parent_sector is not None:
            replacement = replace(replacement, parent_address=parent_sector)
        adfs._write_directory_at(replacement, sector)

    def apply_catalogue_repair(self, action: str, paths: tuple[str, ...]) -> None:
        """Apply one narrowly-scoped catalogue repair as a single transaction."""

        if action not in {"normalise_directory_lengths", "clear_empty_file_extents"}:
            raise ValueError(_("unsupported catalogue repair: {action}").format(action=action))
        if not paths:
            raise ValueError(_("a catalogue repair must name at least one path"))
        adfs = cast(Any, self._mount)._adfs

        def prepare(transaction: SectorTransaction) -> None:
            for path in paths:
                transaction.capture_parent(path)

        def mutate() -> None:
            grouped: dict[int, tuple[Any, set[str]]] = {}
            for path in paths:
                directory, sector = adfs._resolve_parent(path.split("."))
                current, names = grouped.setdefault(int(sector), (directory, set()))
                if current != directory:
                    raise AcornFSError(_("A repair resolved one parent directory inconsistently."))
                names.add(path.rsplit(".", 1)[-1].casefold())

            for sector, (directory, names) in grouped.items():
                repaired = []
                matched: set[str] = set()
                for entry in directory.entries:
                    key = entry.name.casefold()
                    if key not in names:
                        repaired.append(entry)
                        continue
                    matched.add(key)
                    if action == "normalise_directory_lengths":
                        if not entry.is_directory:
                            raise AcornFSError(
                                _("Repair target is no longer a directory: {name}").format(
                                    name=entry.name
                                )
                            )
                        repaired.append(replace(entry, length=int(adfs._dir_format.size_in_bytes)))
                    else:
                        if entry.is_directory or int(entry.length) != 0:
                            raise AcornFSError(
                                _("Repair target is no longer an empty file: {name}").format(
                                    name=entry.name
                                )
                            )
                        repaired.append(replace(entry, indirect_disc_address=0))
                if matched != names:
                    missing = ", ".join(sorted(names - matched))
                    raise AcornFSError(
                        _("Repair target was not found in its parent: {missing}").format(
                            missing=missing
                        )
                    )
                replacement = replace(
                    directory,
                    entries=tuple(repaired),
                    sequence_number=(int(directory.sequence_number) + 1) & 0xFF,
                )
                adfs._write_directory_at(replacement, sector)

        def commit() -> None:
            if action == "normalise_directory_lengths":
                wanted = set(paths)
                expected = int(adfs._dir_format.size_in_bytes)
                for inode, node in tuple(self.nodes.items()):
                    if node.acorn_path in wanted:
                        self.nodes[inode] = replace(node, size=expected)

        self._run_atomic(action, prepare=prepare, mutate=mutate, commit=commit)

    def pad_reserved_tail(self) -> None:
        """Restore a DSC-declared tail only when it starts at the ADFS boundary."""

        with self._mutation():
            mapping_handle = self._closeables[1]
            original_size = os.fstat(mapping_handle.fileno()).st_size
            if self._descriptor_geometry is None:
                raise AcornFSError(_("Reserved-tail repair requires BeebSCSI geometry."))
            expected_size = self._descriptor_geometry.capacity
            adfs_size = int(cast(Any, self._mount)._adfs._fsm.total_sectors) * ADFS_SECTOR_BYTES
            if original_size != adfs_size or original_size >= expected_size:
                raise AcornFSError(
                    _(
                        "Reserved-tail padding is allowed only when the DAT ends exactly at "
                        "the validated ADFS boundary below DSC capacity."
                    )
                )
            try:
                os.ftruncate(mapping_handle.fileno(), expected_size)
                self._fault("pad_reserved_tail.after")
                os.fsync(mapping_handle.fileno())
                self._expected_signature = self._current_signature()
            except Exception as exc:
                try:
                    os.ftruncate(mapping_handle.fileno(), original_size)
                    os.fsync(mapping_handle.fileno())
                    self._expected_signature = self._current_signature()
                except Exception as rollback_exc:
                    self._failed = True
                    raise AcornFSError(
                        _(
                            "Reserved-tail padding failed and its size rollback could not be "
                            "verified; restore the recovery checkpoint."
                        )
                    ) from rollback_exc
                raise _MutationRolledBack(exc) from exc

    def _is_descendant_or_self(self, inode: int, ancestor_inode: int) -> bool:
        cursor = inode
        while True:
            if cursor == ancestor_inode:
                return True
            node = self.nodes[cursor]
            if node.inode == ROOT_INODE or node.parent_inode == node.inode:
                return False
            cursor = node.parent_inode

    def _current_signature(self) -> tuple[int, ...]:
        signatures: list[int] = []
        members = [(self.source.primary_path, self._closeables[1])]
        if self.source.companion_path is not None:
            members.append((self.source.companion_path, self._closeables[3]))
        for path, handle in members:
            open_stat = os.fstat(handle.fileno())
            path_stat = path.stat(follow_symlinks=False)
            signatures.extend(
                (
                    path_stat.st_dev,
                    path_stat.st_ino,
                    open_stat.st_size,
                    open_stat.st_mtime_ns,
                    open_stat.st_ctime_ns,
                )
            )
        return tuple(signatures)

    def _prepare_mutation(self) -> None:
        if not self.writable:
            raise PermissionError(_("image is read-only"))
        if self._failed:
            raise AcornFSError(_("The writable session has failed; unmount and recover the image."))
        if self._expected_signature is None:
            raise AcornFSError(_("This image has no writable identity signature."))
        if self._current_signature() != self._expected_signature:
            self._failed = True
            raise AcornFSError(_("The image changed outside AcornFS; further writes are blocked."))

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)

    @property
    def _uses_old_adfs_transactions(self) -> bool:
        adfs = getattr(self._mount, "_adfs", None)
        return adfs is not None and not adfs.is_new_map

    def _run_atomic(
        self,
        operation: str,
        *,
        prepare: Callable[[Any], object],
        mutate: Callable[[], object],
        commit: Callable[[], T],
    ) -> T:
        """Run one mutation with a durable before-image and verified rollback."""

        with self._mutation():
            transaction: SectorTransaction | FileTransaction
            if self._uses_old_adfs_transactions:
                transaction = SectorTransaction(self._mount, self._closeables[0])
            else:
                if self._checkpoint is None:
                    raise AcornFSError(_("The writable recovery checkpoint is unavailable."))
                transaction = FileTransaction(
                    self.source.primary_path,
                    self._closeables[0],
                    self._closeables[1],
                    backup_directory=self._checkpoint.directory,
                )
            try:
                prepare(transaction)
                self._fault(f"{operation}.before")
                mutate()
                transaction.advance_disc_id()
                self._fault(f"{operation}.after")
                self._finish_mutation()
                transaction.complete()
            except Exception as exc:
                try:
                    transaction.restore()
                    self._finish_rollback()
                    transaction.complete()
                except Exception as rollback_exc:
                    self._failed = True
                    raise AcornFSError(
                        _(
                            "{operation} failed and its sector rollback could not be verified; "
                            "unmount and restore the recovery checkpoint."
                        ).format(operation=operation)
                    ) from rollback_exc
                raise _MutationRolledBack(exc) from exc
            return commit()

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        with self._mutation_lock:
            self._prepare_mutation()
            try:
                yield
            except _MutationRolledBack as exc:
                raise exc.original.with_traceback(exc.original.__traceback__) from exc
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

    def _finish_rollback(self) -> None:
        if self.pair is not None:
            report = self.integrity_report()
            if report.fatal_findings:
                count = len(report.fatal_findings)
                problem = ngettext("fatal ADFS problem", "fatal ADFS problems", count)
                raise AcornFSError(
                    _("Rollback validation found {count} {problem}; recovery is required.").format(
                        count=count, problem=problem
                    )
                )
        else:
            self._require_generic_integrity(self._mount)
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
        self._cache_file(inode, data)
        return data

    def _cache_file(self, inode: int, data: bytes) -> None:
        old_data = self._cache.pop(inode, None)
        if old_data is not None:
            self._cache_size -= len(old_data)
        if len(data) > self._cache_limit:
            return
        while self._cache and self._cache_size + len(data) > self._cache_limit:
            _old_inode, evicted = self._cache.popitem(last=False)
            self._cache_size -= len(evicted)
        self._cache[inode] = data
        self._cache_size += len(data)

    def _reported_size(self, fallback: int) -> int:
        return (
            int(cast(Sized, self._mount).size_bytes())
            if isinstance(self._mount, Sized)
            else fallback
        )

    def _reported_free_space(self) -> int:
        return (
            int(cast(FreeSpace, self._mount).free_bytes())
            if isinstance(self._mount, FreeSpace)
            else 0
        )

    def integrity_report(self, *, budget: OperationBudget | None = None) -> IntegrityReport:
        """Return a full integrity report for the currently locked image."""

        from acornfs.core.validation import validate_open_mount

        if self.pair is None or self._descriptor_geometry is None:
            raise AcornFSError(_("Full validation is not available for this image format."))
        return validate_open_mount(self.pair, self._mount, self._descriptor_geometry, budget=budget)

    @staticmethod
    def _close_oaknut_mount(mount: Mount) -> None:
        close = getattr(mount, "close", None)
        if callable(close):
            close()
            return
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
                if clean:
                    if self.pair is not None:
                        report = self.integrity_report()
                        fatal = report.fatal_findings
                        if fatal:
                            count = len(fatal)
                            problem = ngettext("fatal problem", "fatal problems", count)
                            raise AcornFSError(
                                _(
                                    "Post-write validation found {count} {problem}; "
                                    "the recovery checkpoint was retained."
                                ).format(count=count, problem=problem)
                            )
                    else:
                        self._require_generic_integrity(self._mount)
            except Exception as exc:
                self._failed = True
                close_error = AcornFSError(
                    _("Could not safely finalise the writable image: {error}").format(error=exc)
                )
        self._cache.clear()
        self._cache_size = 0
        mount = self._mount
        self._close_oaknut_mount(mount)
        self._mount = cast(Mount, None)
        del mount
        gc.collect()
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


def validate_image(selected: str | Path) -> tuple[str, ...]:
    """Compatibility wrapper returning human-readable integrity findings."""

    from acornfs.core.validation import validate_image_report

    report = validate_image_report(selected)
    return tuple(
        f"{finding.severity.value}: {finding.code}: {finding.message}"
        for finding in report.findings
    )
