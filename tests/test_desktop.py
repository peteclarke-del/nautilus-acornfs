from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from acornfs.desktop import (
    _systemd_mount_command,
    cleanup_stale_mountpoint,
    desktop_recover,
    desktop_repair,
    desktop_unmount,
    desktop_validate,
    mountpoint_for_image,
)
from acornfs.mounts import MountRecord
from tests.image_fixture import create_beebscsi_image, reserve_adfs_tail


def test_mountpoint_is_stable_for_either_pair_member(tmp_path: Path, monkeypatch: object) -> None:
    mount_root = tmp_path / "mounts"
    monkeypatch.setenv("ACORNFS_MOUNT_ROOT", str(mount_root))  # type: ignore[attr-defined]
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    assert mountpoint_for_image(dat_path) == mountpoint_for_image(dsc_path)
    assert mountpoint_for_image(dat_path).parent == mount_root
    assert mountpoint_for_image(dat_path).name.startswith("scsi0-")


def test_desktop_recovery_requires_explicit_dialog_choice() -> None:
    choice = SimpleNamespace(
        returncode=0,
        stdout="Restore image to the pre-mount checkpoint\n",
    )
    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", return_value=choice),
        patch(
            "acornfs.desktop.recover_image", return_value="Recovery checkpoint restored."
        ) as recover,
        patch("acornfs.desktop._notify"),
    ):
        assert desktop_recover("/image.dat") == 0
    recover.assert_called_once_with("/image.dat", restore=True)


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

    assert run.call_args.args[0][:2] == ["/usr/bin/zenity", "--entry"]
    assert dat_path.stat().st_size == capacity
    assert notify.call_args.args[0] == "AcornFS repair completed"


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

    with (
        patch("acornfs.desktop.shutil.which", return_value="/usr/bin/zenity"),
        patch("acornfs.desktop.subprocess.run", side_effect=[report_choice, confirmation]) as run,
        patch("acornfs.desktop._notify") as notify,
    ):
        assert desktop_validate(dat_path) == 0

    report_arguments = run.call_args_list[0].args[0]
    assert report_arguments[:2] == ["/usr/bin/zenity", "--text-info"]
    assert "--ok-label=Repair…" in report_arguments
    assert "--cancel-label=Cancel" in report_arguments
    assert "--no-cancel" not in report_arguments
    assert run.call_args_list[1].args[0][:2] == ["/usr/bin/zenity", "--entry"]
    assert dat_path.stat().st_size == capacity
    assert notify.call_args.args[0] == "AcornFS repair completed"


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
