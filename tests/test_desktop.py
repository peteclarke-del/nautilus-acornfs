from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from acornfs.desktop import desktop_recover, mountpoint_for_image
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
