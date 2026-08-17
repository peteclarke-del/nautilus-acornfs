import fcntl
import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from acornfs.core.beebscsi import discover_pair, open_locked_reader
from acornfs.core.image import ReadOnlyImage
from acornfs.errors import AcornFSError, PairDiscoveryError
from acornfs.recovery import RecoveryCheckpoint
from tests.image_fixture import create_beebscsi_image


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


def test_checkpoint_refuses_symlinked_identity_directory(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    pair = discover_pair(dat_path)
    identity = hashlib.sha256(str(pair.dat_path).encode("utf-8", "surrogateescape")).hexdigest()
    recovery_root = tmp_path / "state" / "acornfs" / "recovery"
    recovery_root.mkdir(parents=True)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    (recovery_root / identity).symlink_to(redirected, target_is_directory=True)

    with pytest.raises(AcornFSError, match="unsafe recovery state path"):
        RecoveryCheckpoint.create(pair)

    assert list(redirected.iterdir()) == []


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
