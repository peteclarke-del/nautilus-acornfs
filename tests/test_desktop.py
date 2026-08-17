import time
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

import pytest

from acornfs.desktop import (
    _last_log_line,
    _notify,
    _run_with_progress,
    _run_with_reported_progress,
    _systemd_mount_command,
    background_mount,
    cleanup_stale_mountpoint,
    desktop_configure_mount_location,
    desktop_create,
    desktop_open,
    desktop_open_file_forge,
    desktop_recover,
    desktop_repair,
    desktop_unmount,
    desktop_validate,
    desktop_write_floppy,
    local_image_reference,
    mountpoint_for_image,
)
from acornfs.errors import AcornFSError, OperationCancelled
from acornfs.mounts import MountRecord
from acornfs.preferences import mount_location, preferences_path
from tests.image_fixture import create_beebscsi_image, reserve_adfs_tail


@pytest.fixture(autouse=True)
def run_progress_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "acornfs.desktop._run_with_progress",
        lambda _title, _message, operation: operation(lambda: False),
    )
    monkeypatch.setattr(
        "acornfs.desktop._run_with_reported_progress",
        lambda _title, _message, operation: operation(lambda _percent, _message: None),
    )


def test_mountpoint_is_stable_for_either_pair_member(tmp_path: Path, monkeypatch: object) -> None:
    mount_root = tmp_path / "mounts"
    monkeypatch.setenv("ACORNFS_MOUNT_ROOT", str(mount_root))  # type: ignore[attr-defined]
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    assert mountpoint_for_image(dat_path) == mountpoint_for_image(dsc_path)
    assert mountpoint_for_image(dat_path).parent == mount_root
    assert mountpoint_for_image(dat_path).name.startswith("scsi0-")


def test_changed_preference_reuses_an_existing_image_mount(
    tmp_path: Path, monkeypatch: object
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    old_mountpoint = tmp_path / "old-root" / "scsi0-existing"
    old_mountpoint.mkdir(parents=True)
    new_root = tmp_path / "new-root"
    monkeypatch.setenv("ACORNFS_MOUNT_ROOT", str(new_root))  # type: ignore[attr-defined]
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))  # type: ignore[attr-defined]
    (tmp_path / "runtime").mkdir()
    record = MountRecord(
        str(old_mountpoint),
        dat_path.name,
        "ro",
        image_path=str(dat_path),
        read_write=False,
    )

    with (
        patch("acornfs.desktop.cleanup_retained_state"),
        patch("acornfs.desktop.mount_for_image", return_value=record),
        patch("acornfs.desktop.mount_at", return_value=record),
        patch("acornfs.desktop.cleanup_stale_mountpoint") as cleanup,
        patch("acornfs.desktop.subprocess.Popen") as launch,
        patch("acornfs.desktop._open_folder") as open_folder,
        patch("acornfs.desktop._notify"),
    ):
        mounted = background_mount(dat_path)

    assert mounted == old_mountpoint
    assert not new_root.exists()
    cleanup.assert_not_called()
    launch.assert_not_called()
    open_folder.assert_called_once_with(old_mountpoint)


def test_desktop_uri_handler_accepts_only_local_image_paths() -> None:
    assert local_image_reference("file:///tmp/Acorn%20image/scsi0.dat") == Path(
        "/tmp/Acorn image/scsi0.dat"
    )
    assert local_image_reference("acornfs:///tmp/scsi0.dsc") == Path("/tmp/scsi0.dsc")
    assert local_image_reference("relative/scsi0.dat") == Path("relative/scsi0.dat")
    with pytest.raises(AcornFSError, match="only local"):
        local_image_reference("file://server/share/scsi0.dat")
    with pytest.raises(AcornFSError, match="Unsupported"):
        local_image_reference("https://example.test/scsi0.dat")
    with pytest.raises(AcornFSError, match="invalid path"):
        local_image_reference("file:///tmp/scsi%00.dat")
    with pytest.raises(AcornFSError, match="unambiguous"):
        local_image_reference("file:///tmp/scsi0.dat?version=2")


def test_desktop_open_mounts_mime_references_read_only() -> None:
    with patch("acornfs.desktop.desktop_mount", return_value=0) as mount:
        assert desktop_open(["file:///tmp/scsi0.dat", "acornfs:///tmp/scsi1.dsc"]) == 0
    assert mount.call_args_list == [
        ((Path("/tmp/scsi0.dat"),), {"read_write": False}),
        ((Path("/tmp/scsi1.dsc"),), {"read_write": False}),
    ]


def test_desktop_open_notifies_for_refused_uri() -> None:
    with (
        patch("acornfs.desktop._notify") as notify,
        pytest.raises(AcornFSError, match="Unsupported"),
    ):
        desktop_open(["https://example.test/scsi0.dat"])
    notify.assert_called_once_with(
        "AcornFS open failed", "Unsupported image URI scheme: https", error=True
    )


def test_desktop_file_forge_handoff_reports_launcher_failure() -> None:
    with (
        patch("acornfs.desktop.open_in_file_forge", side_effect=AcornFSError("not installed")),
        patch("acornfs.desktop._show_desktop_message") as show,
        pytest.raises(AcornFSError, match="not installed"),
    ):
        desktop_open_file_forge("/image.dat")
    show.assert_called_once_with("Could not open Acorn File Forge", "not installed", error=True)


def test_desktop_floppy_write_selects_confirms_writes_and_reports_success() -> None:
    with (
        patch("acornfs.desktop.detected_command", return_value="/usr/bin/gw"),
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch(
            "acornfs.desktop.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="B\n"),
                SimpleNamespace(returncode=0),
            ],
        ),
        patch(
            "acornfs.desktop.write_floppy",
            return_value=SimpleNamespace(drive="B", verified=True),
        ) as write,
        patch("acornfs.desktop._show_desktop_message") as show,
    ):
        assert desktop_write_floppy("/images/private-name.ssd") == 0

    write.assert_called_once_with(Path("/images/private-name.ssd"), "B", progress=ANY)
    show.assert_called_once_with(
        "Physical floppy complete",
        "Greaseweazle wrote and verified private-name.ssd in drive B.",
    )


def test_desktop_floppy_write_confirmation_can_be_cancelled() -> None:
    with (
        patch("acornfs.desktop.detected_command", return_value="/usr/bin/gw"),
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch(
            "acornfs.desktop.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="A\n"),
                SimpleNamespace(returncode=1),
            ],
        ),
        patch("acornfs.desktop.write_floppy") as write,
    ):
        assert desktop_write_floppy("disc.ssd") == 0

    write.assert_not_called()


def test_desktop_create_collects_settings_and_reports_success(tmp_path: Path) -> None:
    form = SimpleNamespace(returncode=0, stdout="games\x1fGAMES\x1f2MB\n")
    completed = SimpleNamespace(returncode=0)
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", side_effect=[form, completed]) as run,
    ):
        assert desktop_create(tmp_path) == 0

    assert (tmp_path / "games.dat").is_file()
    assert (tmp_path / "games.dsc").is_file()
    form_arguments = run.call_args_list[0].args[0]
    assert form_arguments[:2] == ["/usr/bin/zenity", "--forms"]
    assert "--ok-label=Create" in form_arguments
    result_arguments = run.call_args_list[1].args[0]
    assert result_arguments[:2] == ["/usr/bin/zenity", "--info"]
    assert "games.dat" in next(item for item in result_arguments if item.startswith("--text="))


def test_desktop_create_cancellation_creates_nothing(tmp_path: Path) -> None:
    cancelled = SimpleNamespace(returncode=1, stdout="")
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", return_value=cancelled),
    ):
        assert desktop_create(tmp_path) == 0
    assert list(tmp_path.iterdir()) == []


def test_desktop_mount_location_is_saved(tmp_path: Path) -> None:
    target = tmp_path / "mounts"
    entry = SimpleNamespace(returncode=0, stdout=f"{target}\n")
    completed = SimpleNamespace(returncode=0)
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", side_effect=[entry, completed]) as run,
    ):
        assert desktop_configure_mount_location() == 0

    assert mount_location().root == target
    entry_arguments = run.call_args_list[0].args[0]
    assert entry_arguments[:2] == ["/usr/bin/zenity", "--entry"]
    assert "--ok-label=Save" in entry_arguments
    result_arguments = run.call_args_list[1].args[0]
    assert result_arguments[:2] == ["/usr/bin/zenity", "--info"]
    assert str(target) in next(item for item in result_arguments if item.startswith("--text="))


def test_notification_and_log_details_redact_unrelated_paths(tmp_path: Path) -> None:
    log = tmp_path / "mount.log"
    log.write_text("failed at /home/alice/private/token\0\n", encoding="utf-8")
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/notify-send"),
        patch("acornfs.desktop.subprocess.run") as run,
    ):
        _notify("AcornFS failed", "failed at /home/alice/private/token\0", error=True)

    arguments = run.call_args.args[0]
    assert "/home/alice" not in arguments[-1]
    assert "\0" not in arguments[-1]
    assert "/home/alice" not in _last_log_line(log)


def test_desktop_mount_location_can_replace_corrupt_preference() -> None:
    preferences_path().parent.mkdir(parents=True)
    preferences_path().write_text("broken", encoding="utf-8")
    entry = SimpleNamespace(returncode=0, stdout="sidebar\n")
    completed = SimpleNamespace(returncode=0)

    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", side_effect=[entry, completed]) as run,
    ):
        assert desktop_configure_mount_location() == 0

    form_arguments = run.call_args_list[0].args[0]
    assert "saved preference is invalid" in next(
        item for item in form_arguments if item.startswith("--text=")
    )
    assert mount_location().mode == "sidebar"


def test_desktop_recovery_requires_explicit_dialog_choice() -> None:
    choice = SimpleNamespace(
        returncode=0,
        stdout="Restore image to the pre-mount checkpoint\n",
    )
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", return_value=choice) as run,
        patch(
            "acornfs.desktop.recover_image", return_value="Recovery checkpoint restored."
        ) as recover,
        patch("acornfs.desktop._notify"),
    ):
        assert desktop_recover("/image.dat") == 0
    recover.assert_called_once_with("/image.dat", restore=True, cancelled=ANY)
    assert run.call_args_list[1].args[0][:2] == ["/usr/bin/zenity", "--info"]


def test_desktop_recovery_failure_is_shown_explicitly() -> None:
    choice = SimpleNamespace(
        returncode=0,
        stdout="Restore image to the pre-mount checkpoint\n",
    )
    shown = SimpleNamespace(returncode=0)
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", side_effect=[choice, shown]) as run,
        patch(
            "acornfs.desktop.recover_image",
            side_effect=AcornFSError("The image is still mounted."),
        ),
        pytest.raises(AcornFSError, match="still mounted"),
    ):
        desktop_recover("/image.dat")
    error_arguments = run.call_args_list[1].args[0]
    assert error_arguments[:2] == ["/usr/bin/zenity", "--error"]
    assert "still mounted" in next(item for item in error_arguments if item.startswith("--text="))


def test_desktop_repair_requires_typed_filename_and_repairs_safe_tail(
    tmp_path: Path, monkeypatch: object
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    capacity = dat_path.stat().st_size
    reserve_adfs_tail(dat_path, 128)
    with dat_path.open("r+b") as handle:
        handle.truncate(capacity - 128 * 256)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # type: ignore[attr-defined]
    confirmation = SimpleNamespace(returncode=0, stdout=f"{dat_path.name}\n")

    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", return_value=confirmation) as run,
        patch("acornfs.desktop._notify") as notify,
    ):
        assert desktop_repair(dat_path) == 0

    assert run.call_args_list[0].args[0][:2] == ["/usr/bin/zenity", "--entry"]
    assert run.call_args_list[1].args[0][:2] == ["/usr/bin/zenity", "--info"]
    assert dat_path.stat().st_size == capacity
    notify.assert_not_called()


def test_dead_fuse_endpoint_is_detached_before_mounting(tmp_path: Path) -> None:
    target = tmp_path / "stale"
    target.mkdir()
    detached = SimpleNamespace(returncode=0, stderr="")
    with (
        patch("acornfs.desktop.is_mounted", return_value=True),
        patch("acornfs.desktop.os.listdir", side_effect=OSError(107, "not connected")),
        patch("acornfs.desktop.subprocess.run", return_value=detached) as run,
    ):
        assert cleanup_stale_mountpoint(target)
    run.assert_called_once_with(
        ["fusermount3", "-u", "-z", str(target)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_systemd_mount_uses_graceful_sigint_and_collection() -> None:
    command = _systemd_mount_command("acornfs-test.service", ["python", "-m", "acornfs.cli"])
    assert command[:5] == [
        "systemd-run",
        "--user",
        "--quiet",
        "--collect",
        "--unit=acornfs-test.service",
    ]
    assert "--property=KillSignal=SIGINT" in command
    assert "--property=TimeoutStopSec=30s" in command
    assert command[-3:] == ["python", "-m", "acornfs.cli"]


def test_desktop_validation_reports_clean_image(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with patch("acornfs.desktop._notify") as notify:
        assert desktop_validate(dat_path) == 0
    notify.assert_called_once_with(
        "AcornFS validation passed",
        "scsi0.dat has no reported ADFS problems.",
    )


def test_desktop_validation_reports_safe_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.setattr(
        "acornfs.desktop._run_with_progress",
        lambda *_args: (_ for _ in ()).throw(OperationCancelled("cancelled")),
    )
    with patch("acornfs.desktop._notify") as notify:
        assert desktop_validate(dat_path) == 0
    notify.assert_called_once_with("AcornFS validation cancelled", "The image was not modified.")


def test_progress_dialog_requests_cooperative_cancellation() -> None:
    class ClosedProgress:
        stdin = None

        @staticmethod
        def poll() -> int:
            return 1

    def operation(cancelled: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 1
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.001)
        if not cancelled():
            raise AssertionError("progress cancellation was not propagated")
        raise OperationCancelled("cancelled safely")

    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.Popen", return_value=ClosedProgress()) as popen,
        pytest.raises(OperationCancelled, match="cancelled safely"),
    ):
        _run_with_progress("Title", "Working…", operation)

    arguments = popen.call_args.args[0]
    assert arguments[:3] == ["/usr/bin/zenity", "--progress", "--pulsate"]
    assert "--cancel-label=Cancel safely" in arguments


def test_repair_progress_dialog_receives_determinate_updates() -> None:
    class RecordingInput(StringIO):
        def close(self) -> None:
            pass

    class ProgressProcess:
        def __init__(self) -> None:
            self.stdin = RecordingInput()
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: int) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = -15

    process = ProgressProcess()

    def operation(progress: Callable[[int, str], None]) -> str:
        progress(10, "Planning repair")
        progress(55, "Copying checkpoint")
        progress(100, "Repair verified")
        return "done"

    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.Popen", return_value=process) as popen,
    ):
        assert _run_with_reported_progress("Repair", "Starting", operation) == "done"

    arguments = popen.call_args.args[0]
    assert arguments[:2] == ["/usr/bin/zenity", "--progress"]
    assert "--percentage=0" in arguments
    assert "--no-cancel" in arguments
    assert "--pulsate" not in arguments
    assert process.stdin.getvalue() == (
        "#Planning repair\n10\n#Copying checkpoint\n55\n#Repair verified\n100\n"
    )


def test_desktop_validation_shows_finite_problem_report(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with dat_path.open("r+b") as handle:
        handle.truncate(512)
    dialog_result = SimpleNamespace(returncode=0)
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", return_value=dialog_result) as run,
        patch("acornfs.desktop._notify") as notify,
    ):
        assert desktop_validate(dat_path) == 1

    arguments = run.call_args.args[0]
    assert arguments[:2] == ["/usr/bin/zenity", "--text-info"]
    assert "--ok-label=Close" in arguments
    assert "--no-cancel" in arguments
    height = int(next(item.split("=", 1)[1] for item in arguments if item.startswith("--height=")))
    assert height < 400
    assert "geometry.dat_short" in run.call_args.kwargs["input"]
    assert "Run 'acornfs validate'" not in run.call_args.kwargs["input"]
    notify.assert_not_called()


def test_validation_dialog_offers_repair_for_safe_tail(tmp_path: Path, monkeypatch: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    capacity = dat_path.stat().st_size
    reserve_adfs_tail(dat_path, 128)
    with dat_path.open("r+b") as handle:
        handle.truncate(capacity - 128 * 256)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # type: ignore[attr-defined]
    report_choice = SimpleNamespace(returncode=0)
    confirmation = SimpleNamespace(returncode=0, stdout=f"{dat_path.name}\n")
    completed = SimpleNamespace(returncode=0)

    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch(
            "acornfs.desktop.subprocess.run",
            side_effect=[report_choice, confirmation, completed],
        ) as run,
        patch("acornfs.desktop._notify") as notify,
    ):
        assert desktop_validate(dat_path) == 0

    report_arguments = run.call_args_list[0].args[0]
    assert report_arguments[:2] == ["/usr/bin/zenity", "--text-info"]
    assert "--ok-label=Repair…" in report_arguments
    assert "--cancel-label=Cancel" in report_arguments
    assert "--no-cancel" not in report_arguments
    assert run.call_args_list[1].args[0][:2] == ["/usr/bin/zenity", "--entry"]
    assert run.call_args_list[2].args[0][:2] == ["/usr/bin/zenity", "--info"]
    assert dat_path.stat().st_size == capacity
    notify.assert_not_called()


def test_writable_desktop_unmount_waits_for_safe_finalisation(tmp_path: Path) -> None:
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()
    record = MountRecord(
        mountpoint=str(mountpoint),
        source="scsi0.dat",
        options="rw",
        image_path=str(tmp_path / "scsi0.dat"),
        read_write=True,
    )
    result = SimpleNamespace(returncode=0, stderr="")
    with (
        patch("acornfs.desktop.mount_at", return_value=record),
        patch("acornfs.desktop.wait_for_mount_shutdown", return_value=True),
        patch("acornfs.desktop.pending_recovery", return_value=None),
        patch("acornfs.desktop.subprocess.run", return_value=result) as run,
        patch("acornfs.desktop._notify") as notify,
    ):
        assert desktop_unmount(mountpoint) == 0

    assert run.call_args.args[0] == ["fusermount3", "-u", str(mountpoint)]
    notify.assert_called_once_with(
        "AcornFS image unmounted", f"{mountpoint.name} was flushed and validated safely."
    )


def test_read_only_desktop_unmount_can_detach_lazily(tmp_path: Path) -> None:
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()
    record = MountRecord(str(mountpoint), "scsi0.dat", "ro", read_write=False)
    result = SimpleNamespace(returncode=0, stderr="")
    with (
        patch("acornfs.desktop.mount_at", return_value=record),
        patch("acornfs.desktop.subprocess.run", return_value=result) as run,
        patch("acornfs.desktop._notify"),
    ):
        assert desktop_unmount(mountpoint) == 0
    assert run.call_args.args[0] == ["fusermount3", "-u", "-z", str(mountpoint)]
