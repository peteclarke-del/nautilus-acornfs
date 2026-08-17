"""Optional, shell-free Greaseweazle physical-floppy integration."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from acornfs.errors import AcornFSError
from acornfs.i18n import _
from acornfs.privacy import safe_user_message

SUPPORTED_SUFFIXES = frozenset({".adf", ".adl", ".adm", ".ads", ".dsd", ".ssd"})
DRIVE_CHOICES = ("A", "B", "0", "1", "2", "3")
INFO_TIMEOUT = 4.0
WRITE_TIMEOUT = 30 * 60.0
_TRACK = re.compile(r"^T(?P<cylinder>\d+)\.(?P<head>\d+):")
_GEOMETRY = re.compile(
    r"Writing c=(?P<first>\d+)-(?P<last>\d+):h=(?P<head_first>\d+)(?:-(?P<head_last>\d+))?"
)


@dataclass(frozen=True)
class FloppyWriteResult:
    """Outcome reported by a successful Greaseweazle write."""

    drive: str
    verified: bool


def supports_physical_write(path: str | Path) -> bool:
    """Return whether Greaseweazle recognises the image filename."""

    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def _environment() -> dict[str, str]:
    return {
        name: value
        for name in ("HOME", "LANG", "LC_ALL", "PATH")
        if (value := os.environ.get(name)) is not None
    }


def detected_command(path: str | Path) -> str | None:
    """Return a usable gw command only when a device responds to ``gw info``."""

    if not supports_physical_write(path):
        return None
    command = shutil.which("gw")
    if command is None:
        return None
    try:
        probe = subprocess.run(
            [command, "info"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_environment(),
            timeout=INFO_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return command if probe.returncode == 0 else None


def physical_write_available(path: str | Path) -> bool:
    """Return whether the image can currently be offered for physical writing."""

    return detected_command(path) is not None


def _track_total(line: str) -> int | None:
    match = _GEOMETRY.search(line)
    if match is None:
        return None
    cylinders = int(match["last"]) - int(match["first"]) + 1
    last_head = int(match["head_last"] or match["head_first"])
    heads = last_head - int(match["head_first"]) + 1
    return cylinders * heads


def _snapshot(image: Path, destination: Path) -> None:
    before = image.stat()
    if not image.is_file():
        raise AcornFSError(_("The selected floppy image is not a regular file."))
    with image.open("rb") as source, destination.open("xb") as target:
        shutil.copyfileobj(source, target)
        target.flush()
        os.fsync(target.fileno())
    after = image.stat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or destination.stat().st_size != after.st_size:
        raise AcornFSError(
            _("The floppy image changed while it was being prepared; no physical write started.")
        )


def write_floppy(
    image_path: str | Path,
    drive: str,
    *,
    progress: Callable[[int, str], None] | None = None,
) -> FloppyWriteResult:
    """Write one stable image snapshot and retain Greaseweazle verification defaults."""

    report = progress or (lambda _percent, _detail: None)
    selected_drive = drive.upper() if drive.lower() in {"a", "b"} else drive
    if selected_drive not in DRIVE_CHOICES:
        raise AcornFSError(_("The selected Greaseweazle drive is invalid."))
    try:
        image = Path(image_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise AcornFSError(
            _("Could not open the floppy image: {detail}").format(detail=safe_user_message(exc))
        ) from exc
    if not supports_physical_write(image):
        raise AcornFSError(_("Greaseweazle does not support this floppy-image filename."))
    command = detected_command(image)
    if command is None:
        raise AcornFSError(
            _("No responsive Greaseweazle device was detected; reconnect it and try again.")
        )

    report(1, _("Preparing a stable image snapshot…"))
    with tempfile.TemporaryDirectory(prefix="acornfs-gw-") as temporary:
        snapshot = Path(temporary) / f"image{image.suffix.lower()}"
        try:
            _snapshot(image, snapshot)
        except OSError as exc:
            raise AcornFSError(
                _("Could not prepare the floppy image: {detail}").format(
                    detail=safe_user_message(exc)
                )
            ) from exc
        report(5, _("Starting Greaseweazle…"))
        try:
            process = subprocess.Popen(
                [command, "write", f"--drive={selected_drive}", str(snapshot)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=_environment(),
            )
        except OSError as exc:
            raise AcornFSError(
                _("Could not start Greaseweazle: {detail}").format(detail=safe_user_message(exc))
            ) from exc

        timed_out = threading.Event()

        def stop_timed_out_write() -> None:
            timed_out.set()
            with suppress(OSError):
                process.terminate()

        watchdog = threading.Timer(WRITE_TIMEOUT, stop_timed_out_write)
        watchdog.daemon = True
        watchdog.start()
        recent: deque[str] = deque(maxlen=8)
        tracks: set[tuple[int, int]] = set()
        total: int | None = None
        verified = False
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                recent.append(line)
                total = _track_total(line) or total
                track = _TRACK.match(line)
                if track is not None:
                    tracks.add((int(track["cylinder"]), int(track["head"])))
                    percent = 5 + min(90, int(90 * len(tracks) / total)) if total else 5
                    report(
                        percent,
                        _("Writing and verifying track {track}").format(
                            track=f"{track['cylinder']}.{track['head']}"
                        ),
                    )
                if "Verify Failure" in line:
                    report(max(5, min(95, 5 + len(tracks))), _("Retrying track verification…"))
                if "All tracks verified" in line:
                    verified = True
            returncode = process.wait()
        except BaseException:
            with suppress(OSError):
                process.terminate()
            with suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=5)
            raise
        finally:
            watchdog.cancel()
        if timed_out.is_set():
            raise AcornFSError(
                _(
                    "Greaseweazle exceeded the physical-write time limit. "
                    "The physical floppy may be incomplete; do not rely on its contents."
                )
            )
        if returncode != 0:
            detail = safe_user_message(recent[-1] if recent else _("unknown error"))
            raise AcornFSError(
                _(
                    "Greaseweazle could not complete the write: {detail}. "
                    "The physical floppy may be incomplete; do not rely on its contents."
                ).format(detail=detail)
            )
        if not verified:
            raise AcornFSError(
                _(
                    "Greaseweazle completed the write but did not confirm verification. "
                    "The physical floppy may be incomplete; do not rely on its contents."
                )
            )
        report(100, _("All tracks written and verified."))
        return FloppyWriteResult(drive=selected_drive, verified=True)


__all__ = [
    "DRIVE_CHOICES",
    "FloppyWriteResult",
    "SUPPORTED_SUFFIXES",
    "WRITE_TIMEOUT",
    "detected_command",
    "physical_write_available",
    "supports_physical_write",
    "write_floppy",
]
