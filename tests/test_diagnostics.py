from unittest.mock import patch

from acornfs.diagnostics import diagnostic_report
from acornfs.mounts import MountRecord


def test_diagnostics_omit_absolute_image_and_mount_paths() -> None:
    record = MountRecord(
        mountpoint="/home/alice/AcornFS Mounts/private-disc-123",
        source="private.dat",
        options="rw,nosuid,nodev",
        image_path="/home/alice/secret/client/private.dat",
        image_device=42,
        image_inode=99,
        pid=1234,
        read_write=True,
    )
    with patch("acornfs.diagnostics.active_mounts", return_value=[record]):
        report = diagnostic_report()

    rendered = repr(report)
    assert "/home/alice" not in rendered
    assert "secret/client" not in rendered
    assert report["mounts"][0]["image_name"] == "private.dat"
    assert report["mounts"][0]["image_identity"]
