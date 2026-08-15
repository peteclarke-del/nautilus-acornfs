from pathlib import Path

from acornfs.desktop import mountpoint_for_image
from tests.image_fixture import create_beebscsi_image


def test_mountpoint_is_stable_for_either_pair_member(tmp_path: Path, monkeypatch: object) -> None:
    mount_root = tmp_path / "mounts"
    monkeypatch.setenv("ACORNFS_MOUNT_ROOT", str(mount_root))  # type: ignore[attr-defined]
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    assert mountpoint_for_image(dat_path) == mountpoint_for_image(dsc_path)
    assert mountpoint_for_image(dat_path).parent == mount_root
    assert mountpoint_for_image(dat_path).name.startswith("scsi0-")
