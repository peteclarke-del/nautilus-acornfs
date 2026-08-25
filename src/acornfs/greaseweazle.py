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

from acornfs.core import ResolvedImage, resolve_image
from acornfs.core.hfe import is_hfe
from acornfs.errors import AcornFSError
from acornfs.i18n import _
from acornfs.privacy import safe_user_message

SUPPORTED_SUFFIXES = frozenset({".adf", ".adl", ".adm", ".ads", ".dsd", ".hfe", ".ssd"})
SUPPORTED_IMAGE_SIZES = frozenset(
    {
        100 * 1024,
        160 * 1024,
        200 * 1024,
        320 * 1024,
        400 * 1024,
        640 * 1024,
        800 * 1024,
        1600 * 1024,
    }
)
DRIVE_CHOICES = ("A", "B", "0", "1", "2", "3")
INFO_TIMEOUT = 4.0
DRIVE_PROBE_TIMEOUT = 5.0
WRITE_TIMEOUT = 30 * 60.0
SERIAL_DEVICE_DIRECTORY = Path("/dev/serial/by-id")
_TRACK = re.compile(r"^T(?P<cylinder>\d+)\.(?P<head>\d+):")
_GEOMETRY = re.compile(
    r"Writing c=(?P<first>\d+)-(?P<last>\d+):h=(?P<head_first>\d+)(?:-(?P<head_last>\d+))?"
)
_RPM = re.compile(r"\bRate:\s*\d+(?:\.\d+)?\s*rpm\b", re.IGNORECASE)


@dataclass(frozen=True)
class FloppyWriteResult:
    """Outcome reported by a successful Greaseweazle write."""

    drive: str
    verified: bool


def supports_physical_write(path: str | Path) -> bool:
    """Return whether Greaseweazle recognises the image filename."""

    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def _supported_image_size(path: str | Path) -> bool:
    try:
        return Path(path).stat().st_size in SUPPORTED_IMAGE_SIZES
    except OSError:
        return False


def _geometry_signature(image: ResolvedImage) -> tuple[str, int, int, int, int] | None:
    surfaces = image.geometry.surface_specs
    if not surfaces:
        return None
    first = surfaces[0]
    if any(
        surface.num_tracks != first.num_tracks
        or surface.sectors_per_track != first.sectors_per_track
        or surface.bytes_per_sector != first.bytes_per_sector
        for surface in surfaces[1:]
    ):
        return None
    filesystem = "dfs" if image.filesystem in {"acorn-dfs", "watford-dfs"} else image.filesystem
    return (
        filesystem,
        first.num_tracks,
        len(surfaces),
        first.sectors_per_track,
        first.bytes_per_sector,
    )


_GREASEWEAZLE_FORMATS = {
    ("adfs", 40, 1, 16, 256): "acorn.adfs.160",
    ("adfs", 80, 1, 16, 256): "acorn.adfs.320",
    ("adfs", 80, 2, 16, 256): "acorn.adfs.640",
    ("adfs", 80, 2, 5, 1024): "acorn.adfs.800",
    ("adfs", 80, 2, 10, 1024): "acorn.adfs.1600",
    ("dfs", 40, 1, 10, 256): "acorn.dfs.ss",
    ("dfs", 80, 1, 10, 256): "acorn.dfs.ss80",
    ("dfs", 40, 2, 10, 256): "acorn.dfs.ds",
    ("dfs", 80, 2, 10, 256): "acorn.dfs.ds80",
}


def greaseweazle_format(path: str | Path) -> str:
    """Resolve one Acorn floppy to an explicit Greaseweazle disk format."""

    selected = Path(path)
    try:
        image = resolve_image(selected)
    except AcornFSError as exc:
        raise AcornFSError(
            _("The selected file is not a supported writable Acorn floppy image: {error}").format(
                error=exc
            )
        ) from exc
    try:
        dfs_by_container = {
            (".ssd", 100 * 1024): "acorn.dfs.ss",
            (".ssd", 200 * 1024): "acorn.dfs.ss80",
            (".dsd", 200 * 1024): "acorn.dfs.ds",
            (".dsd", 400 * 1024): "acorn.dfs.ds80",
        }
        if image.filesystem in {"acorn-dfs", "watford-dfs"}:
            try:
                container_format = dfs_by_container.get(
                    (selected.suffix.casefold(), selected.stat().st_size)
                )
            except OSError:
                container_format = None
            if container_format is not None:
                return container_format
        signature = _geometry_signature(image)
        format_name = _GREASEWEAZLE_FORMATS.get(signature) if signature is not None else None
        if format_name is None:
            raise AcornFSError(
                _("The detected Acorn floppy geometry is not writable by this Greaseweazle setup.")
            )
        return format_name
    finally:
        image.close()


def _environment() -> dict[str, str]:
    return {
        name: value
        for name in ("HOME", "LANG", "LC_ALL", "PATH")
        if (value := os.environ.get(name)) is not None
    }


def _command_responds(command: str) -> bool:
    """Probe one resolved command without exposing subprocess details to callers."""

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
        return False
    return probe.returncode == 0


def detected_command(path: str | Path) -> str | None:
    """Return a usable gw command only when a device responds to ``gw info``."""

    if not supports_physical_write(path):
        return None
    command = shutil.which("gw")
    if command is None:
        return None
    return command if _command_responds(command) else None


def _device_available() -> bool:
    """Return whether udev exposes an accessible Greaseweazle serial device."""

    try:
        devices = tuple(SERIAL_DEVICE_DIRECTORY.iterdir())
    except OSError:
        return False
    for device in devices:
        if "greaseweazle" not in device.name.casefold():
            continue
        try:
            target = device.resolve(strict=True)
        except OSError:
            continue
        if os.access(target, os.R_OK | os.W_OK):
            return True
    return False


def physical_write_available(path: str | Path) -> bool:
    """Return immediate executable and udev availability for a desktop menu."""

    if not supports_physical_write(path):
        return False
    selected = Path(path)
    if selected.suffix.casefold() == ".hfe":
        if not is_hfe(selected):
            return False
    elif not _supported_image_size(selected):
        return False
    return shutil.which("gw") is not None and _device_available()


def _reset_after_probe_timeout(command: str) -> None:
    """Best-effort controller reset to deselect drives and stop their motors."""

    with suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            [command, "reset"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_environment(),
            timeout=INFO_TIMEOUT,
        )


def _drive_has_index(command: str, drive: str) -> bool:
    """Probe one drive for an indexed disk without modifying its contents."""

    try:
        probe = subprocess.run(
            [command, "rpm", f"--drive={drive}", "--nr=1"],
            check=False,
            capture_output=True,
            text=True,
            env=_environment(),
            timeout=DRIVE_PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        _reset_after_probe_timeout(command)
        return False
    except OSError:
        return False
    output = f"{probe.stdout}\n{probe.stderr}"
    return probe.returncode == 0 and _RPM.search(output) is not None


def detected_drives(
    path: str | Path,
    *,
    command: str | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[str, ...]:
    """Return drives that report index pulses from an inserted floppy.

    PC and Shugart identifiers describe alternative bus configurations. Probe
    the Shugart group only when no PC-bus drive responds, avoiding duplicate or
    misleading choices for normal single-bus installations.
    """

    executable = command or detected_command(path)
    if executable is None:
        return ()
    report = progress or (lambda _percent, _detail: None)
    groups = (("A", "B"), ("0", "1", "2", "3"))
    completed = 0
    for drives in groups:
        found: list[str] = []
        for drive in drives:
            report(
                5 + completed * 90 // len(DRIVE_CHOICES),
                _("Checking physical drive {drive}…").format(drive=drive),
            )
            if _drive_has_index(executable, drive):
                found.append(drive)
            completed += 1
        if found:
            report(100, _("Physical drives detected."))
            return tuple(found)
    report(100, _("No physical drive with an indexed floppy was detected."))
    return ()


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
    native_hfe = image.suffix.casefold() == ".hfe"
    if native_hfe and not is_hfe(image):
        raise AcornFSError(_("The selected file is not an HFE v1 or HFEv3 floppy image."))
    format_name = None if native_hfe else greaseweazle_format(image)
    command = detected_command(image)
    if command is None:
        raise AcornFSError(
            _("No responsive Greaseweazle device was detected; reconnect it and try again.")
        )

    report(1, _("Preparing a stable image snapshot…"))
    with tempfile.TemporaryDirectory(prefix="acornfs-gw-") as temporary:
        snapshot = Path(temporary) / f"image{image.suffix.casefold()}"
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
            arguments = [command, "write", f"--drive={selected_drive}"]
            if format_name is not None:
                arguments.append(f"--format={format_name}")
            arguments.append(str(snapshot))
            process = subprocess.Popen(
                arguments,
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
    "DRIVE_PROBE_TIMEOUT",
    "FloppyWriteResult",
    "SUPPORTED_SUFFIXES",
    "WRITE_TIMEOUT",
    "detected_command",
    "detected_drives",
    "greaseweazle_format",
    "physical_write_available",
    "supports_physical_write",
    "write_floppy",
]
