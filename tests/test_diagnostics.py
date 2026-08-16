from unittest.mock import patch

from acornfs.diagnostics import diagnostic_report
from acornfs.errors import AcornFSError
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
    assert report["mount_location"] == {"mode": "sidebar", "source": "default"}


def test_diagnostics_report_invalid_preferences_without_exposing_details() -> None:
    with (
        patch("acornfs.diagnostics.active_mounts", return_value=[]),
        patch(
            "acornfs.diagnostics.mount_location",
            side_effect=AcornFSError("/home/alice/private/preferences.json is corrupt"),
        ),
    ):
        report = diagnostic_report()

    assert report["mount_location"] == {
        "mode": "invalid",
        "source": "preferences-error",
    }
    assert "/home/alice" not in repr(report)
