from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from acornfs.core import ReadOnlyImage, validate_image_report
from acornfs.core.image import ROOT_INODE
from acornfs.desktop import _systemd_user_available, background_mount, desktop_unmount
from acornfs.fuse_adapter.availability import live_fuse_available
from acornfs.mounts import active_mounts, is_mounted, mount_for_image, runtime_root
from acornfs.recovery import pending_recovery, recover_image
from tests.image_fixture import create_adfs_floppy, create_beebscsi_image, create_dfs_floppy

FUSE_REQUESTED = os.environ.get("ACORNFS_RUN_LIVE_FUSE") == "1"
FUSE_AVAILABLE = FUSE_REQUESTED and live_fuse_available()
SYSTEMD_AVAILABLE = FUSE_AVAILABLE and _systemd_user_available()
FUSE_SKIP_REASON = (
    "a usable /dev/fuse device and fusermount3 are required"
    if FUSE_REQUESTED
    else "set ACORNFS_RUN_LIVE_FUSE=1 on a host permitted to mount FUSE filesystems"
)


@pytest.mark.skipif(not FUSE_AVAILABLE, reason=FUSE_SKIP_REASON)
def test_live_read_only_adfs_floppy_mount(tmp_path: Path) -> None:
    """Traverse a standalone ADFS floppy through the real kernel FUSE path."""

    image_path = create_adfs_floppy(tmp_path)
    original = image_path.read_bytes()
    mountpoint = tmp_path / "floppy-mount"
    mountpoint.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-m", "acornfs.cli", "mount", str(image_path), str(mountpoint)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while mount_for_image(image_path) is None:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                pytest.fail(f"FUSE floppy mount exited early: {stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("FUSE floppy mount did not become ready within 15 seconds")
            time.sleep(0.05)

        assert (mountpoint / "HELLO").read_bytes() == b"Hello from floppy\r"
        assert (mountpoint / "DOCS" / "GUIDE").read_bytes() == b"Floppy guide\r"
        with pytest.raises(OSError) as denied:
            (mountpoint / "NEWFILE").write_bytes(b"must not be written")
        assert denied.value.errno in {errno.EACCES, errno.EROFS}

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

    assert image_path.read_bytes() == original


@pytest.mark.skipif(not FUSE_AVAILABLE, reason=FUSE_SKIP_REASON)
def test_live_read_only_dsd_mount_exposes_both_drives(tmp_path: Path) -> None:
    """Traverse both sides of a DSD through the real kernel FUSE path."""

    image_path = create_dfs_floppy(tmp_path, double_sided=True)
    original = image_path.read_bytes()
    mountpoint = tmp_path / "dfs-mount"
    mountpoint.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-m", "acornfs.cli", "mount", str(image_path), str(mountpoint)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while mount_for_image(image_path) is None:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                pytest.fail(f"FUSE DFS mount exited early: {stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("FUSE DFS mount did not become ready within 15 seconds")
            time.sleep(0.05)

        assert (mountpoint / "0" / "$" / "HELLO").read_bytes() == b"Hello from DFS drive 0\r"
        assert (mountpoint / "2" / "$" / "OTHER").read_bytes() == b"Hello from DFS drive 2\r"
        with pytest.raises(OSError) as denied:
            (mountpoint / "0" / "$" / "NEW").write_bytes(b"must not be written")
        assert denied.value.errno in {errno.EACCES, errno.EROFS}

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

    assert image_path.read_bytes() == original


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
                registry = runtime_root() / "mounts"
                records = {
                    path.name: path.read_text(encoding="utf-8", errors="replace")
                    for path in registry.glob("*.json")
                }
                pytest.fail(
                    "FUSE mount did not become ready within 15 seconds; "
                    f"kernel records={active_mounts()!r}; private records={records!r}"
                )
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


@pytest.mark.skipif(
    not SYSTEMD_AVAILABLE,
    reason="a usable FUSE device and systemd user manager are required",
)
def test_live_systemd_writable_mount_is_recognised_and_finalised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the exact transient-service path used by Nautilus."""

    image_dir = tmp_path / "image"
    image_dir.mkdir()
    dat_path, _dsc_path = create_beebscsi_image(image_dir)
    mount_root = tmp_path / "mounts"
    monkeypatch.setenv("ACORNFS_MOUNT_ROOT", str(mount_root))
    monkeypatch.setattr("acornfs.desktop._notify", lambda *_args, **_kwargs: None)
    mountpoint = background_mount(
        dat_path,
        open_folder=False,
        notify=False,
        timeout=20,
        read_write=True,
    )
    try:
        record = mount_for_image(dat_path)
        assert record is not None
        assert record.read_write is True
        (mountpoint / "SYSTEMD").write_bytes(b"managed writable mount")
        assert desktop_unmount(mountpoint) == 0
    finally:
        if is_mounted(mountpoint):
            subprocess.run(
                ["fusermount3", "-uz", str(mountpoint)],
                check=False,
                capture_output=True,
                timeout=15,
            )

    assert validate_image_report(dat_path).findings == ()
    assert pending_recovery(dat_path) is None


@pytest.mark.skipif(
    not FUSE_AVAILABLE,
    reason=FUSE_SKIP_REASON,
)
def test_live_forced_daemon_termination_restores_checkpoint(tmp_path: Path) -> None:
    """Prove a killed writable daemon leaves a restorable pre-mount checkpoint."""

    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    original = dat_path.read_bytes()
    mountpoint = tmp_path / "crash-mount"
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
        (mountpoint / "CRASHED").write_bytes(b"must be rolled back")
        process.kill()
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        if is_mounted(mountpoint):
            subprocess.run(
                ["fusermount3", "-uz", str(mountpoint)],
                check=False,
                capture_output=True,
                timeout=15,
            )

    assert pending_recovery(dat_path) is not None
    assert "restored" in recover_image(dat_path, restore=True)
    assert dat_path.read_bytes() == original
    assert validate_image_report(dat_path).findings == ()
    assert pending_recovery(dat_path) is None


@pytest.mark.skipif(
    not FUSE_AVAILABLE,
    reason=FUSE_SKIP_REASON,
)
def test_live_sigint_flushes_a_dirty_open_handle(tmp_path: Path) -> None:
    """Model graceful systemd logout while an application still has dirty data open."""

    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    mountpoint = tmp_path / "dirty-shutdown-mount"
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
    descriptor: int | None = None
    try:
        deadline = time.monotonic() + 15
        while mount_for_image(dat_path) is None:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                pytest.fail(f"FUSE mount exited early: {stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("FUSE mount did not become ready within 15 seconds")
            time.sleep(0.05)
        descriptor = os.open(mountpoint / "README", os.O_WRONLY | os.O_TRUNC)
        os.write(descriptor, b"flushed during graceful shutdown")
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=15) == 0
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                if exc.errno not in {errno.ENOTCONN, errno.EIO}:
                    raise
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        if is_mounted(mountpoint):
            subprocess.run(
                ["fusermount3", "-uz", str(mountpoint)],
                check=False,
                capture_output=True,
                timeout=15,
            )

    assert pending_recovery(dat_path) is None
    assert validate_image_report(dat_path).findings == ()
    with ReadOnlyImage.open(dat_path) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert image.read(readme.inode, 0, 1024) == b"flushed during graceful shutdown"
