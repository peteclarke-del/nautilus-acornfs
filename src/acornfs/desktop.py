"""Non-blocking desktop helpers for Nautilus actions."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import TypeVar
from urllib.parse import unquote, urlparse

from acornfs.core import (
    BeebSCSIPair,
    IntegrityReport,
    RepairPlan,
    apply_repairs,
    create_beebscsi_image,
    discover_pair,
    plan_repairs,
    plan_repairs_from_report,
    resolve_image,
    validate_image_report,
)
from acornfs.errors import AcornFSError, OperationCancelled
from acornfs.file_forge import open_in_file_forge
from acornfs.i18n import _
from acornfs.mounts import (
    is_mounted,
    mount_at,
    mount_for_image,
    runtime_root,
    wait_for_mount_shutdown,
)
from acornfs.preferences import mount_location, mount_root, set_mount_location
from acornfs.recovery import pending_recovery, recover_image
from acornfs.retention import cleanup_retained_state

MOUNT_TIMEOUT = 15.0
WRITABLE_MOUNT_TIMEOUT = 300.0
_T = TypeVar("_T")


def mountpoint_for_image(image_path: str | Path) -> Path:
    image = resolve_image(image_path)
    identity = str(image.primary_path).encode("utf-8", "surrogateescape")
    digest = hashlib.sha256(identity).hexdigest()[:10]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", image.primary_path.stem).strip(".-") or "image"
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


def _dialog_report(report: IntegrityReport) -> str:
    """Wrap a validation report for a compact, readable desktop dialog."""

    wrapped: list[str] = []
    for line in report.format_text().splitlines():
        wrapped.extend(
            textwrap.wrap(
                line,
                width=88,
                subsequent_indent="  " if line.startswith("- ") else "",
                replace_whitespace=False,
            )
            or [""]
        )
    return "\n".join(wrapped)


def _show_desktop_message(title: str, message: str, *, error: bool = False) -> None:
    """Show a finite one-button result dialog, falling back to a notification."""

    dialog = shutil.which("zenity")
    if dialog is None:
        _notify(title, message, error=error)
        return
    subprocess.run(
        [
            dialog,
            "--error" if error else "--info",
            f"--title={title}",
            f"--text={message}",
            f"--ok-label={_('Close')}",
            "--width=560",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_with_progress(
    title: str,
    message: str,
    operation: Callable[[Callable[[], bool]], _T],
) -> _T:
    """Run work beside a cancellable pulse dialog using cooperative boundaries."""

    dialog = shutil.which("zenity")
    if dialog is None:
        return operation(lambda: False)
    cancelled = threading.Event()
    try:
        progress = subprocess.Popen(
            [
                dialog,
                "--progress",
                "--pulsate",
                "--auto-close",
                f"--title={title}",
                f"--text={message}",
                f"--cancel-label={_('Cancel safely')}",
                "--width=520",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return operation(lambda: False)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="acornfs-operation") as executor:
        future = executor.submit(operation, cancelled.is_set)
        while not future.done():
            if progress.poll() is not None:
                cancelled.set()
                break
            time.sleep(0.05)
        try:
            return future.result()
        finally:
            if progress.poll() is None:
                if progress.stdin is not None:
                    with suppress(BrokenPipeError, OSError):
                        progress.stdin.write("100\n")
                        progress.stdin.close()
                with suppress(subprocess.TimeoutExpired):
                    progress.wait(timeout=2)
                if progress.poll() is None:
                    progress.terminate()


def _run_with_reported_progress(
    title: str,
    message: str,
    operation: Callable[[Callable[[int, str], None]], _T],
) -> _T:
    """Run non-cancellable transactional work with determinate progress updates."""

    dialog = shutil.which("zenity")
    if dialog is None:
        return operation(lambda _percent, _message: None)
    try:
        progress = subprocess.Popen(
            [
                dialog,
                "--progress",
                "--auto-close",
                "--no-cancel",
                "--percentage=0",
                f"--title={title}",
                f"--text={message}",
                "--width=560",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return operation(lambda _percent, _message: None)

    def update(percent: int, detail: str) -> None:
        if progress.poll() is not None or progress.stdin is None:
            return
        with suppress(BrokenPipeError, OSError):
            progress.stdin.write(f"#{detail}\n{max(0, min(100, percent))}\n")
            progress.stdin.flush()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="acornfs-repair") as executor:
        future = executor.submit(operation, update)
        while not future.done():
            time.sleep(0.05)
        try:
            return future.result()
        finally:
            if progress.poll() is None:
                if progress.stdin is not None:
                    with suppress(BrokenPipeError, OSError):
                        progress.stdin.close()
                with suppress(subprocess.TimeoutExpired):
                    progress.wait(timeout=2)
                if progress.poll() is None:
                    progress.terminate()


def _show_validation_report(
    name: str, report: IntegrityReport, *, offer_repair: bool = False
) -> bool | None:
    """Show complete findings and return whether the user selected repair."""

    dialog = shutil.which("zenity")
    if dialog is None:
        return None
    content = _dialog_report(report)
    line_count = len(content.splitlines())
    height = min(480, max(240, 145 + line_count * 22))
    arguments = [
        dialog,
        "--text-info",
        f"--title={_('AcornFS validation — {name}').format(name=name)}",
        "--width=680",
        f"--height={height}",
        f"--ok-label={_('Repair…') if offer_repair else _('Close')}",
    ]
    if offer_repair:
        arguments.append(f"--cancel-label={_('Cancel')}")
    else:
        arguments.append("--no-cancel")
    result = subprocess.run(
        arguments,
        input=content,
        text=True,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return offer_repair and result.returncode == 0


def background_mount(
    image_path: str | Path,
    *,
    open_folder: bool = True,
    notify: bool = True,
    timeout: float | None = None,
    read_write: bool = False,
) -> Path:
    """Start a detached foreground mount process and wait until it is ready."""

    cleanup_retained_state()
    image = resolve_image(image_path)
    if read_write and not image.capabilities.mount_read_write:
        raise AcornFSError(_("Read-write mounting is not supported for this image format."))
    if timeout is None:
        timeout = WRITABLE_MOUNT_TIMEOUT if read_write else MOUNT_TIMEOUT
    mountpoint = mountpoint_for_image(image.primary_path)
    root = runtime_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / f".{mountpoint.name}.lock"
    log_path = root / f"{mountpoint.name}.log"

    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        mounted_image = mount_for_image(image.primary_path)
        if mounted_image is not None:
            mountpoint = Path(mounted_image.mountpoint)
        else:
            mountpoint.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mountpoint.mkdir(mode=0o700, exist_ok=True)
            cleanup_stale_mountpoint(mountpoint)
        existing = mount_at(mountpoint)
        if existing is not None and mounted_image is None:
            raise AcornFSError(
                _(
                    "The image's mount location is occupied by a different file identity. "
                    "Unmount that location before mounting the replacement image."
                )
            )
        if existing is None:
            command = [sys.executable, "-m", "acornfs.cli", "mount"]
            if read_write:
                command.append("--read-write")
            command.extend((str(image.primary_path), str(mountpoint)))
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
                    detail = launch.stderr.strip() or _("systemd-run failed")
                    raise AcornFSError(
                        _("Could not start the AcornFS user service: {detail}").format(
                            detail=detail
                        )
                    )
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
                if mount_for_image(image.primary_path) is not None:
                    break
                if process is not None and process.poll() is not None:
                    detail = _last_log_line(log_path)
                    raise AcornFSError(
                        detail
                        or _("Mount process exited with status {status}.").format(
                            status=process.returncode
                        )
                    )
                if unit is not None and not _unit_active(unit):
                    detail = _unit_log_line(unit)
                    raise AcornFSError(
                        detail or _("The AcornFS user service exited before mounting.")
                    )
                time.sleep(0.1)
            else:
                if process is not None:
                    process.terminate()
                if unit is not None:
                    _stop_unit(unit)
                raise AcornFSError(
                    _("Timed out mounting {image}.").format(image=image.primary_path.name)
                )

    if open_folder:
        _open_folder(mountpoint)
    if notify:
        mode = _("read-write") if read_write else _("read-only")
        _notify(
            _("AcornFS image mounted"),
            _("{image} is available in Files ({mode}).").format(
                image=image.primary_path.name, mode=mode
            ),
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
        _notify(_("AcornFS mount failed"), str(exc), error=True)
        raise
    return 0


def local_image_reference(reference: str | Path) -> Path:
    """Convert a desktop path, file URI or AcornFS URI to one local image member."""

    if isinstance(reference, Path):
        return reference.expanduser()
    parsed = urlparse(reference)
    if not parsed.scheme:
        return Path(reference).expanduser()
    if parsed.scheme not in {"file", "acornfs"}:
        raise AcornFSError(_("Unsupported image URI scheme: {scheme}").format(scheme=parsed.scheme))
    if parsed.netloc not in {"", "localhost"}:
        raise AcornFSError(_("AcornFS can open only local image URIs."))
    if parsed.params or parsed.query or parsed.fragment or not parsed.path:
        raise AcornFSError(_("The image URI must contain one unambiguous local path."))
    path = unquote(parsed.path)
    if "\0" in path:
        raise AcornFSError(_("The image URI contains an invalid path."))
    return Path(path)


def desktop_open(image_references: list[str]) -> int:
    """Open local desktop/MIME references as safe read-only mounts."""

    for reference in image_references:
        try:
            image_path = local_image_reference(reference)
        except AcornFSError as exc:
            _notify(_("AcornFS open failed"), str(exc), error=True)
            raise
        desktop_mount(image_path, read_write=False)
    return 0


def desktop_open_file_forge(image_path: str | Path) -> int:
    """Hand a local DAT/DSC pair to an installed File Forge desktop launcher."""

    try:
        open_in_file_forge(image_path)
    except AcornFSError as exc:
        _show_desktop_message(_("Could not open Acorn File Forge"), str(exc), error=True)
        raise
    return 0


def desktop_create(directory: str | Path) -> int:
    """Collect image settings and create a validated DAT/DSC pair in a folder."""

    destination = Path(directory).expanduser().resolve()
    dialog = shutil.which("zenity")
    if dialog is None:
        raise AcornFSError(
            _(
                "Run 'acornfs create-beebscsi {destination}' to create an image without Zenity."
            ).format(destination=destination)
        )
    separator = "\x1f"
    choice = subprocess.run(
        [
            dialog,
            "--forms",
            f"--title={_('Create BeebSCSI image')}",
            f"--text={_('Create an empty, validated ADFS hard-disc pair in:')}\n{destination}",
            f"--add-entry={_('Base filename (default: scsi0)')}",
            f"--add-entry={_('ADFS title (default: BLANK)')}",
            f"--add-entry={_('Capacity (default: 20MB)')}",
            f"--separator={separator}",
            f"--ok-label={_('Create')}",
            f"--cancel-label={_('Cancel')}",
            "--width=600",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if choice.returncode != 0:
        return 0
    values = choice.stdout.rstrip("\n").split(separator)
    if len(values) != 3:
        raise AcornFSError(_("The image settings dialog returned an invalid response."))
    name, title, capacity = values
    try:
        created = _run_with_reported_progress(
            _("Creating BeebSCSI image"),
            _("Checking image settings…"),
            lambda progress: create_beebscsi_image(
                destination,
                name=name,
                title=title,
                capacity=capacity,
                progress=progress,
            ),
        )
    except AcornFSError as exc:
        _show_desktop_message(_("AcornFS image creation failed"), str(exc), error=True)
        raise
    _show_desktop_message(
        _("BeebSCSI image created"),
        _(
            "Created and verified {dat} and {dsc}.\n\n"
            "Right-click either file to open or validate it."
        ).format(dat=created.pair.dat_path.name, dsc=created.pair.dsc_path.name),
    )
    return 0


def desktop_configure_mount_location() -> int:
    """Collect and persist the mount location used for future desktop mounts."""

    try:
        current = mount_location()
        displayed = current.mode if current.mode != "custom" else str(current.root)
        preference_note = ""
    except AcornFSError:
        displayed = "sidebar"
        preference_note = _("\nThe saved preference is invalid; saving will replace it.")
    dialog = shutil.which("zenity")
    if dialog is None:
        raise AcornFSError(_("Run 'acornfs config-mount-location' to configure mount locations."))
    prompt = (
        _("Enter sidebar, runtime, or an absolute directory path.\n")
        + _("The setting applies to future mounts; existing mounts are unchanged.")
        + preference_note
    )
    choice = subprocess.run(
        [
            dialog,
            "--entry",
            f"--title={_('AcornFS mount location')}",
            f"--text={prompt}",
            f"--entry-text={displayed}",
            f"--ok-label={_('Save')}",
            f"--cancel-label={_('Cancel')}",
            "--width=620",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if choice.returncode != 0:
        return 0
    try:
        saved = set_mount_location(choice.stdout.rstrip("\n"))
    except AcornFSError as exc:
        _show_desktop_message(_("AcornFS mount location was not changed"), str(exc), error=True)
        raise
    effective = mount_location()
    override = (
        _("\n\nACORNFS_MOUNT_ROOT still overrides this preference for the current environment.")
        if effective.source == "environment"
        else ""
    )
    _show_desktop_message(
        _("AcornFS mount location saved"),
        _("Saved mount location:\n{root}\n\nMode: {mode}{override}").format(
            root=saved.root, mode=saved.mode, override=override
        ),
    )
    return 0


def desktop_unmount(mountpoint: str | Path) -> int:
    target = Path(mountpoint).expanduser().resolve()
    record = mount_at(target)
    if record is None:
        with suppress(OSError):
            target.rmdir()
        _notify(
            _("AcornFS image unmounted"),
            _("{mountpoint} was already detached.").format(mountpoint=target.name),
        )
        return 0
    if record.read_write is None:
        detail = _(
            "This mount has no lifecycle identity record, so AcornFS cannot prove its write "
            "mode or final flush state. Unmount it from a terminal before remounting it."
        )
        _notify(_("AcornFS unmount refused"), detail, error=True)
        raise AcornFSError(detail)
    command = ["fusermount3", "-u"]
    if record.read_write is False:
        command.append("-z")
    command.append(str(target))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or _("fusermount3 failed")
        _notify(_("AcornFS unmount failed"), detail, error=True)
        raise AcornFSError(
            _("Could not unmount {mountpoint}: {detail}").format(mountpoint=target, detail=detail)
        )
    if record.read_write:
        if not wait_for_mount_shutdown(target):
            detail = _(
                "The image detached but its writable daemon has not confirmed a safe flush. "
                "Do not reuse the image until the daemon has exited."
            )
            _notify(_("AcornFS unmount not confirmed"), detail, error=True)
            raise AcornFSError(detail)
        if record.image_path is not None and pending_recovery(record.image_path) is not None:
            detail = _(
                "The image detached but final validation did not complete safely; "
                "resolve its recovery checkpoint before mounting it read-write again."
            )
            _notify(_("AcornFS final validation failed"), detail, error=True)
            raise AcornFSError(detail)
    with suppress(OSError):
        target.rmdir()
    with suppress(OSError):
        target.parent.rmdir()
    if record.read_write:
        _notify(
            _("AcornFS image unmounted"),
            _("{mountpoint} was flushed and validated safely.").format(mountpoint=target.name),
        )
    else:
        _notify(
            _("AcornFS image detached"),
            _("{mountpoint} was detached read-only.").format(mountpoint=target.name),
        )
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
            raise AcornFSError(
                _("Could not inspect mounted image {mountpoint}: {error}").format(
                    mountpoint=target, error=exc
                )
            ) from exc
        result = subprocess.run(
            ["fusermount3", "-u", "-z", str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = result.stderr.strip() or _("fusermount3 failed")
            raise AcornFSError(
                _("Could not clean up stale mount {mountpoint}: {detail}").format(
                    mountpoint=target, detail=detail
                )
            ) from exc
        return True
    return False


def desktop_validate(image_path: str | Path) -> int:
    """Validate an image structure read-only and report the result on the desktop."""

    try:
        report = _run_with_progress(
            _("Validating AcornFS image"),
            _("Checking geometry, directories and allocation…"),
            lambda cancelled: validate_image_report(image_path, cancelled=cancelled),
        )
    except OperationCancelled:
        _notify(_("AcornFS validation cancelled"), _("The image was not modified."))
        return 0
    except AcornFSError as exc:
        _notify(_("AcornFS validation failed"), str(exc), error=True)
        raise
    pair = discover_pair(image_path)
    name = pair.dat_path.name
    if report.fatal_findings or report.warning_findings:
        plan = plan_repairs_from_report(report)
        choice = _show_validation_report(name, report, offer_repair=plan.application_supported)
        if choice is True:
            return _confirm_and_apply_repair(pair, plan)
        if choice is None:
            first = (*report.fatal_findings, *report.warning_findings)[0]
            remaining = len(report.findings) - 1
            suffix = _(" (+{count} more)").format(count=remaining) if remaining else ""
            _notify(
                _("AcornFS validation found problems"),
                _("{image}: [{severity}] {code}: {message}{suffix}").format(
                    image=name,
                    severity=first.severity_label,
                    code=first.code,
                    message=first.message,
                    suffix=suffix,
                ),
                error=True,
            )
        return 1
    _notify(
        _("AcornFS validation passed"),
        _("{image} has no reported ADFS problems.").format(image=name),
    )
    return 0


def _confirm_and_apply_repair(pair: BeebSCSIPair, plan: RepairPlan) -> int:
    """Show the shared typed-confirmation dialog and apply a previously reviewed plan."""

    dialog = shutil.which("zenity")
    if dialog is None:
        raise AcornFSError(
            _("Run 'acornfs repair {image} --confirm {name}' to apply the eligible repair.").format(
                image=pair.dat_path, name=pair.dat_path.name
            )
        )
    actions = "\n".join(f"• {action.title}" for action in plan.actions)
    result = subprocess.run(
        [
            dialog,
            "--entry",
            f"--title={_('Repair AcornFS image')}",
            f"--text={_('Eligible low-risk repair(s):')}\n{actions}\n\n"
            f"{_('A recovery checkpoint and audit will be created.')}\n"
            f"{_('Type {name} to confirm:').format(name=pair.dat_path.name)}",
            "--width=620",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    confirmation = result.stdout.rstrip("\n")
    try:
        repair = _run_with_reported_progress(
            _("Repairing AcornFS image"),
            _("Preparing the repair…"),
            lambda progress: apply_repairs(
                pair.dat_path,
                confirmation=confirmation,
                progress=progress,
            ),
        )
    except AcornFSError as exc:
        _show_desktop_message(_("AcornFS repair failed"), str(exc), error=True)
        raise
    _show_desktop_message(
        _("AcornFS repair completed"),
        _("{image} was repaired and fully verified.\n\nAudit report:\n{audit}").format(
            image=pair.dat_path.name, audit=repair.audit_path
        ),
    )
    return 0


def desktop_repair(image_path: str | Path) -> int:
    """Review an eligible repair and require the exact DAT filename before applying it."""

    pair = discover_pair(image_path)
    plan = plan_repairs(pair.dat_path)
    if plan.clean:
        _notify(
            _("AcornFS repair"),
            _("{image} needs no repair.").format(image=pair.dat_path.name),
        )
        return 0
    if not plan.application_supported:
        if _show_validation_report(pair.dat_path.name, plan.report) is None:
            _notify(
                _("AcornFS automatic repair refused"),
                _("This image has no complete low-risk automatic repair plan."),
                error=True,
            )
        return 1
    return _confirm_and_apply_repair(pair, plan)


def desktop_recover(image_path: str | Path) -> int:
    """Ask the user how to resolve one interrupted writable session."""

    dialog = shutil.which("zenity")
    if dialog is None:
        raise AcornFSError(
            _("Recovery needs a choice. Run 'acornfs recover IMAGE --restore' or '--discard'.")
        )
    restore_choice = _("Restore image to the pre-mount checkpoint")
    discard_choice = _("Keep the current image and discard the checkpoint")
    result = subprocess.run(
        [
            dialog,
            "--list",
            "--radiolist",
            f"--title={_('Resolve interrupted AcornFS read-write mount')}",
            f"--text={_('Choose how to resolve the retained pre-mount checkpoint.')}",
            f"--column={_('Selected')}",
            f"--column={_('Action')}",
            "TRUE",
            restore_choice,
            "FALSE",
            discard_choice,
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
    try:
        if choice == restore_choice:
            message = _run_with_progress(
                _("Restoring AcornFS image"),
                _("Staging the checkpoint safely before replacing the image pair…"),
                lambda cancelled: recover_image(image_path, restore=True, cancelled=cancelled),
            )
        elif choice == discard_choice:
            message = recover_image(image_path, discard=True)
        else:
            return 0
    except OperationCancelled:
        _show_desktop_message(
            _("AcornFS recovery cancelled"),
            _(
                "Recovery stopped before the commit boundary. The image was not replaced and "
                "the checkpoint is still available."
            ),
        )
        return 0
    except AcornFSError as exc:
        _show_desktop_message(_("AcornFS recovery failed"), str(exc), error=True)
        raise
    _show_desktop_message(_("AcornFS recovery complete"), message)
    return 0


def notify_mount_failure(message: str) -> None:
    """Surface a detached mount or final-validation failure to the desktop."""

    _notify(_("AcornFS mount failed"), message, error=True)


__all__ = [
    "background_mount",
    "cleanup_stale_mountpoint",
    "desktop_configure_mount_location",
    "desktop_create",
    "desktop_mount",
    "desktop_open",
    "desktop_open_file_forge",
    "desktop_repair",
    "desktop_recover",
    "desktop_unmount",
    "desktop_validate",
    "mountpoint_for_image",
    "mount_root",
    "local_image_reference",
    "notify_mount_failure",
    "runtime_root",
]
