from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from acornfs.core import validate_image_report
from acornfs.fuse_adapter.availability import live_fuse_available
from acornfs.mounts import mount_for_image
from acornfs.recovery import pending_recovery
from tests.image_fixture import create_beebscsi_image

FUSE_REQUESTED = os.environ.get("ACORNFS_RUN_LIVE_FUSE") == "1"
FUSE_AVAILABLE = FUSE_REQUESTED and live_fuse_available()
FUSE_SKIP_REASON = (
    "a usable /dev/fuse device and fusermount3 are required"
    if FUSE_REQUESTED
    else "set ACORNFS_RUN_LIVE_FUSE=1 on a host permitted to mount FUSE filesystems"
)


@pytest.mark.skipif(
    not FUSE_AVAILABLE,
    reason=FUSE_SKIP_REASON,
)
def test_live_writable_fuse_lifecycle(tmp_path: Path) -> None:
    """Exercise normal file-manager operations through a real kernel mount."""

    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    mountpoint = tmp_path / "mount"
    mountpoint.mkdir()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "acornfs.cli",
            "mount",
            "--read-write",
            str(dat_path),
            str(mountpoint),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while mount_for_image(dat_path) is None:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                pytest.fail(f"FUSE mount exited early: {stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("FUSE mount did not become ready within 15 seconds")
            time.sleep(0.05)

        assert (mountpoint / "README").read_bytes() == b"Hello from AcornFS\r"
        (mountpoint / "NEWDIR").mkdir()
        new_file = mountpoint / "NEWFILE"
        new_file.write_bytes(b"written through the kernel FUSE mount")
        moved = mountpoint / "NEWDIR" / "MOVED"
        new_file.rename(moved)
        with moved.open("r+b") as handle:
            handle.truncate(1024)
            handle.flush()
            os.fsync(handle.fileno())
        os.setxattr(moved, b"user.acorn.filetype", b"FFD")
        assert os.getxattr(moved, b"user.acorn.filetype") == b"FFD"

        top = mountpoint / "TOP"
        top.mkdir()
        (top / "CHILD").mkdir()
        renamed = mountpoint / "DOCS" / "RENAMED"
        top.rename(renamed)
        assert (renamed / "CHILD").is_dir()

        unmount = subprocess.run(
            ["fusermount3", "-u", str(mountpoint)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert unmount.returncode == 0, unmount.stderr
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            subprocess.run(
                ["fusermount3", "-uz", str(mountpoint)],
                check=False,
                capture_output=True,
                timeout=15,
            )
            process.terminate()
            process.wait(timeout=15)

    assert validate_image_report(dat_path).findings == ()
    assert pending_recovery(dat_path) is None
