from pathlib import Path
from unittest.mock import patch

from acornfs.mounts import (
    MountRecord,
    active_mounts,
    mount_for_image,
    parse_mountinfo,
    register_mount,
    registered_mount_at,
    runtime_root,
    unregister_mount,
    wait_for_mount_shutdown,
)
from tests.image_fixture import create_beebscsi_image


def test_parses_only_acornfs_mounts_and_unescapes_paths() -> None:
    text = "\n".join(
        [
            "31 20 0:29 / /tmp/Acorn\\040Discs ro,nosuid - fuse.acornfs scsi0.dat ro",
            "32 20 8:1 / /home rw,relatime - ext4 /dev/sda1 rw",
        ]
    )
    mounts = parse_mountinfo(text)
    assert len(mounts) == 1
    assert mounts[0].mountpoint == "/tmp/Acorn Discs"
    assert mounts[0].source == "scsi0.dat"
    assert mounts[0].options == "ro,nosuid"


def test_registry_enriches_only_kernel_confirmed_mounts(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # type: ignore[attr-defined]
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()
    registered = register_mount(dat_path, mountpoint, read_write=True)
    registry_files = list((runtime_root() / "mounts").glob("*.json"))
    assert len(registry_files) == 1
    assert registry_files[0].stat().st_mode & 0o777 == 0o600
    kernel = MountRecord(str(mountpoint), dat_path.name, "rw,nosuid")

    with patch("acornfs.mounts._kernel_mounts", return_value=[kernel]):
        records = active_mounts()
        resolved = mount_for_image(dat_path)

    assert records == [
        MountRecord(
            mountpoint=str(mountpoint),
            source=dat_path.name,
            options="rw,nosuid",
            image_path=registered.image_path,
            descriptor_path=registered.descriptor_path,
            image_device=registered.image_device,
            image_inode=registered.image_inode,
            descriptor_device=registered.descriptor_device,
            descriptor_inode=registered.descriptor_inode,
            pid=registered.pid,
            read_write=True,
        )
    ]
    assert resolved == records[0]
    assert registered_mount_at(mountpoint) == registered
    unregister_mount(mountpoint)
    assert registered_mount_at(mountpoint) is None


def test_replaced_pair_does_not_match_active_image_identity(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # type: ignore[attr-defined]
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()
    registered = register_mount(dat_path, mountpoint, read_write=False)
    kernel = MountRecord(str(mountpoint), dat_path.name, "ro")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(dat_path.read_bytes())
    replacement.replace(dat_path)

    with patch("acornfs.mounts._kernel_mounts", return_value=[kernel]):
        assert mount_for_image(dat_path) is None
        assert active_mounts()[0].image_inode == registered.image_inode


def test_wait_for_shutdown_covers_post_detach_finalisation() -> None:
    record = MountRecord("/mount", "disc.dat", "rw", pid=123, read_write=True)
    with (
        patch("acornfs.mounts.registered_mount_at", side_effect=[record, None, None]),
        patch("acornfs.mounts.time.monotonic", side_effect=[0.0, 0.1]),
        patch("acornfs.mounts.time.sleep") as sleep,
    ):
        assert wait_for_mount_shutdown("/mount")
    sleep.assert_called_once_with(0.05)


def test_dead_registration_is_pruned(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # type: ignore[attr-defined]
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()
    register_mount(dat_path, mountpoint, read_write=False)
    with (
        patch("acornfs.mounts._kernel_mounts", return_value=[]),
        patch("acornfs.mounts._process_alive", return_value=False),
    ):
        assert active_mounts() == []
    assert list((runtime_root() / "mounts").glob("*.json")) == []
