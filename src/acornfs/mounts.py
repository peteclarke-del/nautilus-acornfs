"""Kernel-backed discovery and private lifecycle records for AcornFS mounts."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from acornfs.core import BeebSCSIPair, discover_pair
from acornfs.errors import AcornFSError

REGISTRY_VERSION = 1


@dataclass(frozen=True, slots=True)
class MountRecord:
    mountpoint: str
    source: str
    options: str
    image_path: str | None = None
    descriptor_path: str | None = None
    image_device: int | None = None
    image_inode: int | None = None
    descriptor_device: int | None = None
    descriptor_inode: int | None = None
    pid: int | None = None
    read_write: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def runtime_root() -> Path:
    configured = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(configured) if configured else Path("/run/user") / str(os.getuid())
    if not root.is_dir():
        raise AcornFSError("The session runtime directory is unavailable.")
    return root / "acornfs"


def _registry_root(*, create: bool = False) -> Path:
    root = runtime_root() / "mounts"
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


def _record_path(mountpoint: str | Path, *, create: bool = False) -> Path:
    target = str(Path(mountpoint).expanduser().resolve())
    digest = hashlib.sha256(target.encode("utf-8", "surrogateescape")).hexdigest()
    return _registry_root(create=create) / f"{digest}.json"


def _pair_identity(pair: BeebSCSIPair) -> tuple[int, int, int, int]:
    try:
        dat_stat = pair.dat_path.stat()
        dsc_stat = pair.dsc_path.stat()
    except OSError as exc:
        raise AcornFSError(f"Could not identify the BeebSCSI pair: {exc}") from exc
    return dat_stat.st_dev, dat_stat.st_ino, dsc_stat.st_dev, dsc_stat.st_ino


def register_mount(
    image_path: str | Path, mountpoint: str | Path, *, read_write: bool
) -> MountRecord:
    """Atomically record the pair identity held by a newly active mount."""

    pair = discover_pair(image_path)
    dat_device, dat_inode, dsc_device, dsc_inode = _pair_identity(pair)
    record = MountRecord(
        mountpoint=str(Path(mountpoint).expanduser().resolve()),
        source=pair.dat_path.name,
        options="",
        image_path=str(pair.dat_path),
        descriptor_path=str(pair.dsc_path),
        image_device=dat_device,
        image_inode=dat_inode,
        descriptor_device=dsc_device,
        descriptor_inode=dsc_inode,
        pid=os.getpid(),
        read_write=read_write,
    )
    path = _record_path(record.mountpoint, create=True)
    payload = {"version": REGISTRY_VERSION, **record.as_dict()}
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AcornFSError(f"Could not record the active AcornFS mount: {exc}") from exc
    return record


def unregister_mount(mountpoint: str | Path) -> None:
    """Remove the lifecycle record for a mount that has fully shut down."""

    with suppress(OSError):
        _record_path(mountpoint).unlink(missing_ok=True)


def _decode_mount_field(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n")


def parse_mountinfo(text: str) -> list[MountRecord]:
    mounts: list[MountRecord] = []
    for line in text.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            filesystem = fields[separator + 1]
            source = fields[separator + 2]
            mountpoint = fields[4]
            options = fields[5]
        except (IndexError, ValueError):
            continue
        if filesystem != "fuse.acornfs":
            continue
        mounts.append(
            MountRecord(
                mountpoint=_decode_mount_field(mountpoint),
                source=_decode_mount_field(source),
                options=options,
            )
        )
    return mounts


def _kernel_mounts() -> list[MountRecord]:
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise AcornFSError(f"Cannot read mount status: {exc}") from exc
    return parse_mountinfo(mountinfo)


def _registrations() -> dict[str, tuple[Path, MountRecord]]:
    try:
        root = _registry_root()
    except AcornFSError:
        return {}
    if not root.is_dir():
        return {}
    registrations: dict[str, tuple[Path, MountRecord]] = {}
    for path in root.glob("*.json"):
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            if payload.pop("version") != REGISTRY_VERSION:
                continue
            record = MountRecord(**payload)
            registrations[record.mountpoint] = (path, record)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return registrations


def _process_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def active_mounts() -> list[MountRecord]:
    """Return kernel-confirmed mounts enriched with exact private identity records."""

    kernel = _kernel_mounts()
    registrations = _registrations()
    enriched: list[MountRecord] = []
    for record in kernel:
        registration = registrations.get(record.mountpoint)
        if registration is None:
            enriched.append(record)
            continue
        private = registration[1]
        if not _process_alive(private.pid) or private.source != record.source:
            enriched.append(record)
            continue
        enriched.append(
            replace(
                record,
                image_path=private.image_path,
                descriptor_path=private.descriptor_path,
                image_device=private.image_device,
                image_inode=private.image_inode,
                descriptor_device=private.descriptor_device,
                descriptor_inode=private.descriptor_inode,
                pid=private.pid,
                read_write=private.read_write,
            )
        )
    for path, stale_record in registrations.values():
        if not _process_alive(stale_record.pid):
            with suppress(OSError):
                path.unlink(missing_ok=True)
    return enriched


def mount_at(mountpoint: str | Path) -> MountRecord | None:
    target = str(Path(mountpoint).expanduser().resolve())
    return next((record for record in active_mounts() if record.mountpoint == target), None)


def registered_mount_at(mountpoint: str | Path) -> MountRecord | None:
    """Return a live daemon record, including its post-detach validation window."""

    target = str(Path(mountpoint).expanduser().resolve())
    registration = _registrations().get(target)
    if registration is None:
        return None
    path, record = registration
    if _process_alive(record.pid):
        return record
    with suppress(OSError):
        path.unlink(missing_ok=True)
    return None


def mount_for_image(image_path: str | Path) -> MountRecord | None:
    """Resolve a pair only when both canonical paths and live inodes still match."""

    pair = discover_pair(image_path)
    dat_device, dat_inode, dsc_device, dsc_inode = _pair_identity(pair)
    for record in active_mounts():
        if (
            record.image_path == str(pair.dat_path)
            and record.descriptor_path == str(pair.dsc_path)
            and record.image_device == dat_device
            and record.image_inode == dat_inode
            and record.descriptor_device == dsc_device
            and record.descriptor_inode == dsc_inode
        ):
            return record
    return None


def is_mounted(mountpoint: str | Path) -> bool:
    return mount_at(mountpoint) is not None


def wait_for_mount_shutdown(mountpoint: str | Path, *, timeout: float = 35.0) -> bool:
    """Wait until a detached daemon finishes close-time flush and validation."""

    deadline = time.monotonic() + timeout
    while registered_mount_at(mountpoint) is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    return registered_mount_at(mountpoint) is None


__all__ = [
    "MountRecord",
    "active_mounts",
    "is_mounted",
    "mount_at",
    "mount_for_image",
    "parse_mountinfo",
    "register_mount",
    "registered_mount_at",
    "runtime_root",
    "unregister_mount",
    "wait_for_mount_shutdown",
]
