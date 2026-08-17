import fcntl
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from acornfs.core.beebscsi import discover_pair, open_locked_reader
from acornfs.core.image import ReadOnlyImage
from acornfs.errors import AcornFSError, PairDiscoveryError
from acornfs.recovery import RecoveryCheckpoint
from tests.image_fixture import create_beebscsi_image, create_dfs_floppy


def _close_reader(reader: object, closeables: tuple[object, ...]) -> None:
    reader.close()  # type: ignore[attr-defined]
    for closeable in closeables:
        closeable.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("member", ["dat", "dsc"])
def test_writable_open_refuses_hard_linked_pair_member(tmp_path: Path, member: str) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    target = dat_path if member == "dat" else dsc_path
    os.link(target, tmp_path / f"linked.{member}")

    with pytest.raises(PairDiscoveryError, match="hard links"):
        ReadOnlyImage.open(dat_path, writable=True)

    with ReadOnlyImage.open(dat_path) as image:
        assert not image.writable


def test_open_rejects_path_replaced_after_lock(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    pair = discover_pair(dat_path)
    replacement = tmp_path / "replacement.dat"
    replacement.write_bytes(dat_path.read_bytes())
    real_flock = fcntl.flock
    calls = 0

    def replace_between_lock_and_verification(handle: object, operation: int) -> None:
        nonlocal calls
        real_flock(handle, operation)  # type: ignore[arg-type]
        calls += 1
        if calls == 2:
            os.replace(replacement, dat_path)

    with (
        patch(
            "acornfs.core.beebscsi.fcntl.flock",
            side_effect=replace_between_lock_and_verification,
        ),
        pytest.raises(PairDiscoveryError, match="changed while AcornFS was opening"),
    ):
        open_locked_reader(pair, writable=False)

    reader, closeables, _descriptor = open_locked_reader(discover_pair(dat_path), writable=False)
    _close_reader(reader, closeables)


def test_locked_mapping_does_not_extend_pair_lock_lifetime(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)

    image = ReadOnlyImage.open(dat_path)
    image.close()

    with ReadOnlyImage.open(dat_path, writable=True) as writable:
        assert writable.writable


def test_writable_open_refuses_hard_linked_standalone_image(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path)
    os.link(image_path, tmp_path / "linked.ssd")

    with pytest.raises(AcornFSError, match="hard links"):
        ReadOnlyImage.open(image_path, writable=True)

    with ReadOnlyImage.open(image_path) as image:
        assert not image.writable


def test_standalone_lock_blocks_other_acornfs_opens(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path)
    script = """
import sys
from acornfs.core.image import ReadOnlyImage
from acornfs.errors import AcornFSError

try:
    ReadOnlyImage.open(sys.argv[1])
except AcornFSError as error:
    print(error)
    raise SystemExit(23)
raise SystemExit(0)
"""
    with ReadOnlyImage.open(image_path, writable=True):
        result = subprocess.run(
            [sys.executable, "-c", script, str(image_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 23
    assert "another AcornFS process" in result.stdout


def test_checkpoint_refuses_symlinked_identity_directory(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    pair = discover_pair(dat_path)
    identity = hashlib.sha256(str(pair.dat_path).encode("utf-8", "surrogateescape")).hexdigest()
    recovery_root = tmp_path / "state" / "acornfs" / "recovery"
    recovery_root.mkdir(parents=True)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    (recovery_root / identity).symlink_to(redirected, target_is_directory=True)

    with pytest.raises(AcornFSError, match="unsafe private AcornFS directory"):
        RecoveryCheckpoint.create(pair)

    assert list(redirected.iterdir()) == []


def test_mount_registry_refuses_symlinked_private_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acornfs.mounts import register_mount

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    (runtime / "acornfs").symlink_to(redirected, target_is_directory=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()

    with pytest.raises(AcornFSError, match="unsafe private AcornFS directory"):
        register_mount(dat_path, mountpoint, read_write=False)

    assert list(redirected.iterdir()) == []


def test_preferences_refuse_symlinked_private_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acornfs.preferences import set_mount_location

    config = tmp_path / "config"
    config.mkdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    (config / "acornfs").symlink_to(redirected, target_is_directory=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))

    with pytest.raises(AcornFSError, match="unsafe private AcornFS directory"):
        set_mount_location("sidebar")

    assert list(redirected.iterdir()) == []


def test_custom_mount_root_refuses_symbolic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acornfs.preferences import ensure_mount_root

    redirected = tmp_path / "redirected"
    redirected.mkdir()
    mount_root = tmp_path / "mounts"
    mount_root.symlink_to(redirected, target_is_directory=True)
    monkeypatch.setenv("ACORNFS_MOUNT_ROOT", str(mount_root))

    with pytest.raises(AcornFSError, match="symbolic link"):
        ensure_mount_root()

    assert list(redirected.iterdir()) == []


def test_private_directory_creation_rejects_component_swap(
    tmp_path: Path,
) -> None:
    from acornfs.safe_paths import ensure_private_directory

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    real_mkdir = os.mkdir

    def swap_after_create(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)
        if path == "private" and dir_fd is not None:
            os.rename("private", "moved", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.symlink(redirected, "private", target_is_directory=True, dir_fd=dir_fd)

    with (
        patch("acornfs.safe_paths.os.mkdir", side_effect=swap_after_create),
        pytest.raises(AcornFSError, match="unsafe private AcornFS directory"),
    ):
        ensure_private_directory(anchor / "private" / "child", anchor=anchor)

    assert list(redirected.iterdir()) == []


def test_repair_audit_refuses_symlinked_private_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acornfs.core.repair import _write_audit

    state = tmp_path / "state"
    audit_parent = state / "acornfs"
    audit_parent.mkdir(parents=True)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    (audit_parent / "repair-audits").symlink_to(redirected, target_is_directory=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    with pytest.raises(AcornFSError, match="unsafe private AcornFS directory"):
        _write_audit(audit_parent / "repair-audits" / "audit.json", {"status": "test"})

    assert list(redirected.iterdir()) == []


def test_repair_audit_does_not_follow_predictable_temporary_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acornfs.core.repair import _write_audit

    state = tmp_path / "state"
    audit = state / "acornfs" / "repair-audits" / "audit.json"
    redirected = tmp_path / "redirected"
    redirected.write_text("unchanged", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    audit.parent.mkdir(parents=True)
    temporary = audit.with_name(f".{audit.name}.{'a' * 32}.tmp")
    temporary.symlink_to(redirected)

    with (
        patch("acornfs.core.repair.uuid.uuid4") as uuid4,
        pytest.raises(FileExistsError),
    ):
        uuid4.return_value.hex = "a" * 32
        _write_audit(audit, {"status": "test"})

    assert redirected.read_text(encoding="utf-8") == "unchanged"
    assert temporary.is_symlink()
    assert not audit.exists()


def test_desktop_child_environment_does_not_inherit_unrelated_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acornfs.desktop import _desktop_environment

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("UNRELATED_API_TOKEN", "secret")

    environment = _desktop_environment()

    assert environment["PATH"] == "/usr/bin"
    assert environment["DBUS_SESSION_BUS_ADDRESS"].startswith("unix:")
    assert environment["ACORNFS_DESKTOP_MOUNT"] == "1"
    assert "UNRELATED_API_TOKEN" not in environment


def test_unknown_user_image_reference_is_rejected_safely() -> None:
    from acornfs.desktop import local_image_reference

    with pytest.raises(AcornFSError, match="unknown user account"):
        local_image_reference("~acornfs-user-that-does-not-exist/image.dat")


def test_malformed_image_uri_is_rejected_safely() -> None:
    from acornfs.desktop import local_image_reference

    with pytest.raises(AcornFSError, match="URI is malformed"):
        local_image_reference("file://[invalid/image.dat")


@pytest.mark.parametrize("reference", ["image\0.dat", "file:///tmp/image%00.dat"])
def test_nul_image_reference_is_rejected_safely(reference: str) -> None:
    from acornfs.desktop import local_image_reference

    with pytest.raises(AcornFSError, match="invalid path.*NUL"):
        local_image_reference(reference)


def test_user_visible_untrusted_detail_is_bounded_and_redacted() -> None:
    from acornfs.privacy import MAX_USER_MESSAGE_CHARS, safe_user_message

    rendered = safe_user_message("failed at /home/alice/Private Images/image.dat\0: " + "x" * 2000)

    assert "/home/alice" not in rendered
    assert "Private Images/image.dat" not in rendered
    assert "\0" not in rendered
    assert len(rendered) == MAX_USER_MESSAGE_CHARS
