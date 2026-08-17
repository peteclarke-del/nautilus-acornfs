"""Persistent checkpoints for recoverable writable image sessions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import shutil
import stat
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from acornfs.core.beebscsi import BeebSCSIPair, discover_pair
from acornfs.errors import AcornFSError
from acornfs.i18n import _
from acornfs.operations import CancellationCheck, cancellation_point
from acornfs.safe_paths import ensure_private_directory

FICLONE = 0x40049409


def state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        return Path(configured).expanduser() / "acornfs" / "recovery"
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ".local" / "state" / "acornfs" / "recovery"


def _identity(pair: BeebSCSIPair) -> str:
    raw = str(pair.dat_path).encode("utf-8", "surrogateescape")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class RecoveryInfo:
    identity: str
    dat_path: str
    dsc_path: str
    created_at: str
    state: str
    reflinked: bool


def _manifest_path(pair: BeebSCSIPair) -> Path:
    return state_root() / _identity(pair) / "manifest.json"


def _ensure_checkpoint_directory(directory: Path) -> None:
    """Create AcornFS-owned state without following symlinked descendants."""

    root = state_root()
    ensure_private_directory(directory, anchor=root.parent.parent)


def _write_manifest(path: Path, info: RecoveryInfo) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(info), indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def pending_recovery(selected: str | Path) -> RecoveryInfo | None:
    pair = discover_pair(selected)
    path = _manifest_path(pair)
    if not path.exists():
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return RecoveryInfo(**payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AcornFSError(
            _("The recovery manifest is unreadable: {path}: {error}").format(path=path, error=exc)
        ) from exc


def _checkpoint_copy(
    source: Path,
    destination: Path,
    *,
    copied: Callable[[int], None] | None = None,
    source_handle: BinaryIO | None = None,
) -> bool:
    reflinked = False
    opened_here = source.open("rb") if source_handle is None else None
    handle = opened_here if opened_here is not None else source_handle
    if handle is None:
        raise AcornFSError(_("Could not open the checkpoint source."))
    try:
        metadata = os.fstat(handle.fileno())
        with destination.open("xb") as destination_handle:
            try:
                fcntl.ioctl(destination_handle.fileno(), FICLONE, handle.fileno())
                reflinked = True
            except OSError:
                handle.seek(0)
                while chunk := handle.read(8 * 1024 * 1024):
                    destination_handle.write(chunk)
                    if copied is not None:
                        copied(len(chunk))
            else:
                if copied is not None:
                    copied(metadata.st_size)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
    finally:
        if opened_here is not None:
            opened_here.close()
    os.chmod(destination, stat.S_IMODE(metadata.st_mode))
    os.utime(destination, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    return reflinked


@contextmanager
def _exclusive_pair_lock(pair: BeebSCSIPair) -> Any:
    handles = (pair.dat_path.open("r+b"), pair.dsc_path.open("r+b"))
    try:
        for handle in handles:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise AcornFSError(_("The image is mounted or open in another AcornFS process.")) from exc
    finally:
        for handle in handles:
            handle.close()


class RecoveryCheckpoint:
    """One active pre-write checkpoint for a DAT/DSC pair."""

    def __init__(self, pair: BeebSCSIPair, directory: Path, info: RecoveryInfo) -> None:
        self.pair = pair
        self.directory = directory
        self.info = info

    @classmethod
    def create(
        cls,
        pair: BeebSCSIPair,
        *,
        progress: Callable[[int, int], None] | None = None,
        source_handles: tuple[BinaryIO, BinaryIO] | None = None,
    ) -> RecoveryCheckpoint:
        directory = _manifest_path(pair).parent
        manifest = directory / "manifest.json"
        if manifest.exists():
            raise AcornFSError(
                _(
                    "An interrupted writable session needs recovery. Run "
                    "'acornfs recover {path}' before mounting read-write."
                ).format(path=pair.dat_path)
            )
        _ensure_checkpoint_directory(directory)
        # A crash after a clean session removes its manifest may leave harmless
        # orphan backup files. They are not authoritative and must not prevent
        # the next exclusive checkpoint from using create-only writes.
        for name in ("image.dat", "image.dsc"):
            (directory / name).unlink(missing_ok=True)
        _fsync_directory(directory)
        info = RecoveryInfo(
            identity=_identity(pair),
            dat_path=str(pair.dat_path),
            dsc_path=str(pair.dsc_path),
            created_at=datetime.now(UTC).isoformat(),
            state="creating",
            reflinked=False,
        )
        _write_manifest(manifest, info)
        try:
            if source_handles is None:
                total_bytes = pair.dat_path.stat().st_size + pair.dsc_path.stat().st_size
            else:
                total_bytes = sum(os.fstat(handle.fileno()).st_size for handle in source_handles)
            copied_bytes = 0
            if progress is not None:
                progress(0, total_bytes)

            def copied(length: int) -> None:
                nonlocal copied_bytes
                copied_bytes += length
                if progress is not None:
                    progress(copied_bytes, total_bytes)

            dat_reflinked = _checkpoint_copy(
                pair.dat_path,
                directory / "image.dat",
                copied=copied,
                source_handle=source_handles[0] if source_handles is not None else None,
            )
            dsc_reflinked = _checkpoint_copy(
                pair.dsc_path,
                directory / "image.dsc",
                copied=copied,
                source_handle=source_handles[1] if source_handles is not None else None,
            )
            info = RecoveryInfo(
                identity=info.identity,
                dat_path=info.dat_path,
                dsc_path=info.dsc_path,
                created_at=info.created_at,
                state="ready",
                reflinked=dat_reflinked and dsc_reflinked,
            )
            _write_manifest(manifest, info)
        except Exception as exc:
            cls(pair, directory, info).complete()
            raise AcornFSError(
                _("Could not create the writable recovery checkpoint: {error}").format(error=exc)
            ) from exc
        return cls(pair, directory, info)

    def complete(self) -> None:
        # The manifest is the authority for pending recovery. Remove and sync it
        # first so a crash cannot advertise already-deleted backup images.
        (self.directory / "manifest.json").unlink(missing_ok=True)
        if self.directory.exists():
            _fsync_directory(self.directory)
        for name in ("image.dat", "image.dsc"):
            (self.directory / name).unlink(missing_ok=True)
        if self.directory.exists():
            _fsync_directory(self.directory)
        try:
            self.directory.rmdir()
            self.directory.parent.rmdir()
        except OSError:
            pass


def _stage_restore(
    source: Path,
    destination: Path,
    *,
    cancelled: CancellationCheck | None,
) -> None:
    """Copy one replacement without touching its destination."""

    try:
        with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
            while chunk := source_handle.read(8 * 1024 * 1024):
                cancellation_point(cancelled)
                destination_handle.write(chunk)
            cancellation_point(cancelled)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        shutil.copystat(source, destination, follow_symlinks=False)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def recover_image(
    selected: str | Path,
    *,
    restore: bool = False,
    discard: bool = False,
    cancelled: CancellationCheck | None = None,
) -> str:
    cancellation_point(cancelled)
    pair = discover_pair(selected)
    info = pending_recovery(pair.dat_path)
    if info is None:
        return _("No recovery checkpoint is pending.")
    directory = _manifest_path(pair).parent
    if restore and discard:
        raise AcornFSError(_("Choose either --restore or --discard, not both."))
    if not restore and not discard:
        state = {
            "creating": _("being created"),
            "ready": _("ready"),
        }.get(info.state, info.state)
        return _(
            "Recovery checkpoint from {created_at} is {state}. Use --restore to restore it "
            "or --discard to accept the current image."
        ).format(
            created_at=info.created_at,
            state=state,
        )
    with _exclusive_pair_lock(pair):
        if restore:
            if info.state != "ready":
                raise AcornFSError(
                    _("The interrupted checkpoint was not completed and cannot be restored.")
                )
            replacements: list[tuple[Path, Path]] = []
            try:
                for backup_name, target in (
                    ("image.dat", pair.dat_path),
                    ("image.dsc", pair.dsc_path),
                ):
                    cancellation_point(cancelled)
                    backup = directory / backup_name
                    temporary = target.with_name(
                        f".{target.name}.acornfs-restore-{uuid.uuid4().hex}"
                    )
                    _stage_restore(backup, temporary, cancelled=cancelled)
                    replacements.append((temporary, target))
                # Cancellation is deliberately disabled across this short commit phase:
                # both staged files must replace the pair as one logical operation.
                cancellation_point(cancelled)
                for temporary, target in reversed(replacements):
                    os.replace(temporary, target)
                    _fsync_directory(target.parent)
            finally:
                for temporary, _target in replacements:
                    temporary.unlink(missing_ok=True)
            result = _("Recovery checkpoint restored.")
        else:
            result = _("Recovery checkpoint discarded.")
        RecoveryCheckpoint(pair, directory, info).complete()
    return result


__all__ = [
    "RecoveryCheckpoint",
    "RecoveryInfo",
    "pending_recovery",
    "recover_image",
    "state_root",
]
