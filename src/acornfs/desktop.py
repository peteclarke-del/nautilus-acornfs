"""Non-blocking desktop helpers for Nautilus actions."""

from __future__ import annotations

import errno
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

from acornfs.core import discover_pair, validate_image
from acornfs.errors import AcornFSError
from acornfs.mounts import is_mounted
from acornfs.recovery import recover_image

MOUNT_TIMEOUT = 15.0
WRITABLE_MOUNT_TIMEOUT = 300.0


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


def _unit_for_mountpoint(mountpoint: Path) -> str:
    digest = hashlib.sha256(str(mountpoint).encode("utf-8", "surrogateescape")).hexdigest()[:16]
    return f"acornfs-mount-{digest}.service"


def _systemd_user_available() -> bool:
    if os.environ.get("ACORNFS_NO_SYSTEMD") == "1":
        return False
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _systemd_mount_command(unit: str, command: list[str]) -> list[str]:
    result = [
        "systemd-run",
        "--user",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        "--service-type=exec",
        "--property=KillMode=mixed",
        "--property=KillSignal=SIGINT",
        "--property=TimeoutStopSec=30s",
        "--setenv=ACORNFS_DESKTOP_MOUNT=1",
    ]
    for name in (
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "WAYLAND_DISPLAY",
    ):
        value = os.environ.get(name)
        if value is not None:
            result.append(f"--setenv={name}={value}")
    return [*result, "--", *command]


def _unit_active(unit: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _unit_log_line(unit: str) -> str:
    result = subprocess.run(
        ["journalctl", "--user", "--unit", unit, "--lines=1", "--no-pager", "--output=cat"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


def _stop_unit(unit: str) -> None:
    subprocess.run(
        ["systemctl", "--user", "stop", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
    timeout: float | None = None,
    read_write: bool = False,
) -> Path:
    """Start a detached foreground mount process and wait until it is ready."""

    pair = discover_pair(image_path)
    if timeout is None:
        timeout = WRITABLE_MOUNT_TIMEOUT if read_write else MOUNT_TIMEOUT
    mountpoint = mountpoint_for_image(pair.dat_path)
    root = runtime_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    mountpoint.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mountpoint.mkdir(mode=0o700, exist_ok=True)
    lock_path = root / f".{mountpoint.name}.lock"
    log_path = root / f"{mountpoint.name}.log"

    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cleanup_stale_mountpoint(mountpoint)
        if not is_mounted(mountpoint):
            command = [sys.executable, "-m", "acornfs.cli", "mount"]
            if read_write:
                command.append("--read-write")
            command.extend((str(pair.dat_path), str(mountpoint)))
            process: subprocess.Popen[bytes] | None = None
            unit: str | None = None
            if _systemd_user_available():
                unit = _unit_for_mountpoint(mountpoint)
                launch = subprocess.run(
                    _systemd_mount_command(unit, command),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if launch.returncode:
                    detail = launch.stderr.strip() or "systemd-run failed"
                    raise AcornFSError(f"Could not start the AcornFS user service: {detail}")
            else:
                with log_path.open("ab") as log:
                    process = subprocess.Popen(
                        command,
                        env={**os.environ, "ACORNFS_DESKTOP_MOUNT": "1"},
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if is_mounted(mountpoint):
                    break
                if process is not None and process.poll() is not None:
                    detail = _last_log_line(log_path)
                    raise AcornFSError(
                        detail or f"Mount process exited with status {process.returncode}."
                    )
                if unit is not None and not _unit_active(unit):
                    detail = _unit_log_line(unit)
                    raise AcornFSError(detail or "The AcornFS user service exited before mounting.")
                time.sleep(0.1)
            else:
                if process is not None:
                    process.terminate()
                if unit is not None:
                    _stop_unit(unit)
                raise AcornFSError(f"Timed out mounting {pair.dat_path.name}.")

    if open_folder:
        _open_folder(mountpoint)
    if notify:
        mode = "read-write" if read_write else "read-only"
        _notify(
            "AcornFS image mounted",
            f"{pair.dat_path.name} is available in Files ({mode}).",
        )
    return mountpoint


def _last_log_line(log_path: Path) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return lines[-1] if lines else ""


def desktop_mount(image_path: str | Path, *, read_write: bool = False) -> int:
    try:
        background_mount(image_path, read_write=read_write)
    except AcornFSError as exc:
        _notify("AcornFS mount failed", str(exc), error=True)
        raise
    return 0


def desktop_unmount(mountpoint: str | Path) -> int:
    target = Path(mountpoint).expanduser().resolve()
    if not is_mounted(target):
        with suppress(OSError):
            target.rmdir()
        _notify("AcornFS image unmounted", f"{target.name} was already detached.")
        return 0
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
    _notify("AcornFS image detached", f"{target.name} is completing final validation.")
    return 0


def cleanup_stale_mountpoint(mountpoint: str | Path) -> bool:
    """Detach a dead FUSE endpoint, leaving healthy mounts untouched."""

    target = Path(mountpoint).expanduser().resolve()
    if not is_mounted(target):
        return False
    try:
        os.listdir(target)
    except OSError as exc:
        if exc.errno not in {errno.ENOTCONN, errno.EIO, errno.ESTALE}:
            raise AcornFSError(f"Could not inspect mounted image {target}: {exc}") from exc
        result = subprocess.run(
            ["fusermount3", "-u", "-z", str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = result.stderr.strip() or "fusermount3 failed"
            raise AcornFSError(f"Could not clean up stale mount {target}: {detail}") from exc
        return True
    return False


def desktop_validate(image_path: str | Path) -> int:
    """Validate an image structure read-only and report the result on the desktop."""

    try:
        problems = validate_image(image_path)
    except AcornFSError as exc:
        _notify("AcornFS validation failed", str(exc), error=True)
        raise
    name = discover_pair(image_path).dat_path.name
    if problems:
        _notify(
            "AcornFS validation found problems",
            f"{name}: {len(problems)} problem(s). Run 'acornfs validate' for details.",
            error=True,
        )
        return 1
    _notify("AcornFS validation passed", f"{name} has no reported ADFS problems.")
    return 0


def desktop_recover(image_path: str | Path) -> int:
    """Ask the user how to resolve one interrupted writable session."""

    dialog = shutil.which("zenity")
    if dialog is None:
        raise AcornFSError(
            "Recovery needs a choice. Run 'acornfs recover IMAGE --restore' or '--discard'."
        )
    result = subprocess.run(
        [
            dialog,
            "--list",
            "--radiolist",
            "--title=Resolve interrupted AcornFS write",
            "--text=Choose how to resolve the retained pre-write checkpoint.",
            "--column=Selected",
            "--column=Action",
            "TRUE",
            "Restore image to the pre-mount checkpoint",
            "FALSE",
            "Keep the current image and discard the checkpoint",
            "--width=620",
            "--height=280",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    choice = result.stdout.strip()
    if choice == "Restore image to the pre-mount checkpoint":
        message = recover_image(image_path, restore=True)
    elif choice == "Keep the current image and discard the checkpoint":
        message = recover_image(image_path, discard=True)
    else:
        return 0
    _notify("AcornFS recovery complete", message)
    return 0


def notify_mount_failure(message: str) -> None:
    """Surface a detached mount or final-validation failure to the desktop."""

    _notify("AcornFS mount failed", message, error=True)


__all__ = [
    "background_mount",
    "cleanup_stale_mountpoint",
    "desktop_mount",
    "desktop_recover",
    "desktop_unmount",
    "desktop_validate",
    "mountpoint_for_image",
    "mount_root",
    "notify_mount_failure",
    "runtime_root",
]
