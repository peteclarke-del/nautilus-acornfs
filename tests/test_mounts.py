from acornfs.mounts import parse_mountinfo


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
