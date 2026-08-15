"""Non-blocking desktop helpers for Nautilus actions."""

from __future__ import annotations

import fcntl
import hashlib
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from acornfs.core import discover_pair
from acornfs.errors import AcornFSError
from acornfs.mounts import is_mounted

MOUNT_TIMEOUT = 15.0


def runtime_root() -> Path:
    configured = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(configured) if configured else Path("/run/user") / str(os.getuid())
    if not root.is_dir():
        raise AcornFSError("The desktop session runtime directory is unavailable.")
    return root / "acornfs"


def mount_root() -> Path:
    """Return a non-hidden home path which GLib exposes as a user FUSE mount."""

    configured = os.environ.get("ACORNFS_MOUNT_ROOT")
    if configured:
        return Path(configured).expanduser()
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / "AcornFS Mounts"


def mountpoint_for_image(image_path: str | Path) -> Path:
    pair = discover_pair(image_path)
    identity = str(pair.dat_path).encode("utf-8", "surrogateescape")
    digest = hashlib.sha256(identity).hexdigest()[:10]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", pair.dat_path.stem).strip(".-") or "image"
    return mount_root() / f"{stem}-{digest}"


def _notify(summary: str, body: str, *, error: bool = False) -> None:
    command = shutil.which("notify-send")
    if command is None:
        return
    urgency = "critical" if error else "normal"
    subprocess.run(
        [command, f"--urgency={urgency}", summary, body],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _open_folder(path: Path) -> None:
    subprocess.Popen(
        ["gio", "open", path.as_uri()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def background_mount(
    image_path: str | Path,
    *,
    open_folder: bool = True,
    notify: bool = True,
    timeout: float = MOUNT_TIMEOUT,
) -> Path:
    """Start a detached foreground mount process and wait until it is ready."""

    pair = discover_pair(image_path)
    mountpoint = mountpoint_for_image(pair.dat_path)
    root = runtime_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    mountpoint.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mountpoint.mkdir(mode=0o700, exist_ok=True)
    lock_path = root / f".{mountpoint.name}.lock"
    log_path = root / f"{mountpoint.name}.log"

    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not is_mounted(mountpoint):
            with log_path.open("ab") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "acornfs.cli",
                        "mount",
                        str(pair.dat_path),
                        str(mountpoint),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if is_mounted(mountpoint):
                    break
                return_code = process.poll()
                if return_code is not None:
                    detail = _last_log_line(log_path)
                    raise AcornFSError(detail or f"Mount process exited with status {return_code}.")
                time.sleep(0.1)
            else:
                process.terminate()
                raise AcornFSError(f"Timed out mounting {pair.dat_path.name}.")

    if open_folder:
        _open_folder(mountpoint)
    if notify:
        _notify("AcornFS image mounted", f"{pair.dat_path.name} is available in Files.")
    return mountpoint


def _last_log_line(log_path: Path) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return lines[-1] if lines else ""


def desktop_mount(image_path: str | Path) -> int:
    try:
        background_mount(image_path)
    except AcornFSError as exc:
        _notify("AcornFS mount failed", str(exc), error=True)
        raise
    return 0


def desktop_unmount(mountpoint: str | Path) -> int:
    target = Path(mountpoint).expanduser().resolve()
    result = subprocess.run(
        ["fusermount3", "-u", "-z", str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or "fusermount3 failed"
        _notify("AcornFS unmount failed", detail, error=True)
        raise AcornFSError(f"Could not unmount {target}: {detail}")
    with suppress(OSError):
        target.rmdir()
    with suppress(OSError):
        target.parent.rmdir()
    _notify("AcornFS image unmounted", target.name)
    return 0


__all__ = [
    "background_mount",
    "desktop_mount",
    "desktop_unmount",
    "mountpoint_for_image",
    "mount_root",
    "runtime_root",
]
