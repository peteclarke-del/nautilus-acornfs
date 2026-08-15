from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from acornfs.desktop import (
    _systemd_mount_command,
    cleanup_stale_mountpoint,
    desktop_recover,
    desktop_validate,
    mountpoint_for_image,
)
from tests.image_fixture import create_beebscsi_image


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
    assert "geometry.dat_short" in run.call_args.kwargs["input"]
    assert "Run 'acornfs validate'" not in run.call_args.kwargs["input"]
    notify.assert_not_called()
