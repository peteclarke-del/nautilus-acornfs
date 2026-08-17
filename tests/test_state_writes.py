import errno
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from acornfs.core import discover_pair
from acornfs.core.repair import _write_audit
from acornfs.errors import AcornFSError
from acornfs.mounts import register_mount, runtime_root
from acornfs.preferences import mount_location, preferences_path, set_mount_location
from acornfs.recovery import RecoveryCheckpoint, _checkpoint_copy, pending_recovery
from acornfs.safe_paths import atomic_write_private_text
from tests.image_fixture import create_beebscsi_image


def test_private_atomic_write_retries_interruption_and_short_writes(tmp_path: Path) -> None:
    anchor = tmp_path / "state"
    target = anchor / "acornfs" / "record.json"
    real_write = os.write
    calls = 0

    def interrupted_short_write(descriptor: int, content: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError(errno.EINTR, "interrupted")
        return real_write(descriptor, content[:3])

    with patch("acornfs.safe_paths.os.write", side_effect=interrupted_short_write):
        atomic_write_private_text(target, '{"complete": true}\n', anchor=anchor)

    assert json.loads(target.read_text(encoding="utf-8")) == {"complete": True}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert calls > 2
    assert not list(target.parent.glob(".*.tmp"))


@pytest.mark.parametrize("failure_point", ["write", "replace"])
def test_private_atomic_write_failure_preserves_last_good_file(
    tmp_path: Path, failure_point: str
) -> None:
    anchor = tmp_path / "state"
    target = anchor / "acornfs" / "record.json"
    atomic_write_private_text(target, "old state\n", anchor=anchor)
    error = OSError(errno.ENOSPC, "disk full")
    patched = (
        patch("acornfs.safe_paths.os.write", side_effect=error)
        if failure_point == "write"
        else patch("acornfs.safe_paths.os.replace", side_effect=error)
    )

    with patched, pytest.raises(OSError) as raised:
        atomic_write_private_text(target, "new state\n", anchor=anchor)

    assert raised.value.errno == errno.ENOSPC
    assert target.read_text(encoding="utf-8") == "old state\n"
    assert not list(target.parent.glob(".*.tmp"))


def test_disk_full_preference_update_preserves_previous_choice(tmp_path: Path) -> None:
    set_mount_location("sidebar")
    before = preferences_path().read_bytes()

    with (
        patch(
            "acornfs.safe_paths.os.write",
            side_effect=OSError(errno.ENOSPC, "disk full"),
        ),
        pytest.raises(AcornFSError, match="Could not save"),
    ):
        set_mount_location(str(tmp_path / "new-mounts"))

    assert preferences_path().read_bytes() == before
    assert mount_location().mode == "sidebar"
    assert not list(preferences_path().parent.glob(".*.tmp"))


def test_low_memory_preference_update_preserves_previous_choice() -> None:
    set_mount_location("sidebar")
    before = preferences_path().read_bytes()

    with (
        patch("acornfs.preferences.json.dumps", side_effect=MemoryError("memory exhausted")),
        pytest.raises(AcornFSError, match="Could not save"),
    ):
        set_mount_location("runtime")

    assert preferences_path().read_bytes() == before
    assert mount_location().mode == "sidebar"


def test_disk_full_mount_registration_leaves_no_partial_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    mountpoint = tmp_path / "mounted"
    mountpoint.mkdir()

    with (
        patch(
            "acornfs.safe_paths.os.write",
            side_effect=OSError(errno.ENOSPC, "disk full"),
        ),
        pytest.raises(AcornFSError, match="Could not record"),
    ):
        register_mount(dat_path, mountpoint, read_write=False)

    assert not list((runtime_root() / "mounts").glob("*.json"))
    assert not list((runtime_root() / "mounts").glob(".*.tmp"))


def test_disk_full_audit_update_preserves_last_durable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    audit = state / "acornfs" / "repair-audits" / "audit.json"
    _write_audit(audit, {"status": "planned"})
    before = audit.read_bytes()

    with (
        patch(
            "acornfs.safe_paths.os.write",
            side_effect=OSError(errno.ENOSPC, "disk full"),
        ),
        pytest.raises(OSError) as raised,
    ):
        _write_audit(audit, {"status": "completed"})

    assert raised.value.errno == errno.ENOSPC
    assert audit.read_bytes() == before
    assert not list(audit.parent.glob(".*.tmp"))


def test_low_memory_audit_update_preserves_last_durable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    audit = state / "acornfs" / "repair-audits" / "audit.json"
    _write_audit(audit, {"status": "planned"})
    before = audit.read_bytes()

    with (
        patch("acornfs.core.repair.json.dumps", side_effect=MemoryError("memory exhausted")),
        pytest.raises(MemoryError, match="memory exhausted"),
    ):
        _write_audit(audit, {"status": "completed"})

    assert audit.read_bytes() == before
    assert not list(audit.parent.glob(".*.tmp"))


@pytest.mark.parametrize(
    "failure",
    [OSError(errno.ENOSPC, "disk full"), MemoryError("memory exhausted")],
)
def test_partial_checkpoint_write_is_removed_without_touching_image(
    tmp_path: Path, failure: Exception
) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    pair = discover_pair(dat_path)
    dat_before = dat_path.read_bytes()
    dsc_before = dsc_path.read_bytes()

    def fail_after_partial_copy(
        _source: Path,
        destination: Path,
        **_kwargs: object,
    ) -> bool:
        destination.write_bytes(b"partial checkpoint")
        raise failure

    with (
        patch("acornfs.recovery._checkpoint_copy", side_effect=fail_after_partial_copy),
        pytest.raises(AcornFSError, match="Could not create.*(disk full|memory exhausted)"),
    ):
        RecoveryCheckpoint.create(pair)

    assert dat_path.read_bytes() == dat_before
    assert dsc_path.read_bytes() == dsc_before
    assert pending_recovery(dat_path) is None
    recovery = tmp_path / "state" / "acornfs" / "recovery"
    if recovery.exists():
        assert not list(recovery.rglob("manifest.json"))
        assert not list(recovery.rglob("image.*"))


def test_checkpoint_copy_removes_its_partial_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.dat"
    destination = tmp_path / "checkpoint.dat"
    source.write_bytes(b"source remains intact")
    real_open = Path.open

    class DiskFullWriter:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> "DiskFullWriter":
            return self

        def __exit__(self, *_args: object) -> None:
            self.handle.close()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined,no-any-return]

        def write(self, content: bytes) -> int:
            self.handle.write(content[:4])  # type: ignore[attr-defined]
            self.handle.flush()  # type: ignore[attr-defined]
            raise OSError(errno.ENOSPC, "disk full")

        def flush(self) -> None:
            self.handle.flush()  # type: ignore[attr-defined]

    def open_with_disk_full(path: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        handle = real_open(path, mode, *args, **kwargs)
        return DiskFullWriter(handle) if path == destination and mode == "xb" else handle

    with (
        patch("acornfs.recovery.fcntl.ioctl", side_effect=OSError("no reflink")),
        patch("pathlib.Path.open", autospec=True, side_effect=open_with_disk_full),
        pytest.raises(OSError) as raised,
    ):
        _checkpoint_copy(source, destination)

    assert raised.value.errno == errno.ENOSPC
    assert source.read_bytes() == b"source remains intact"
    assert not destination.exists()


def test_checkpoint_copy_falls_back_cleanly_without_reflink_support(tmp_path: Path) -> None:
    source = tmp_path / "source.dat"
    destination = tmp_path / "checkpoint.dat"
    source.write_bytes(b"ordinary copied checkpoint")

    with patch("acornfs.recovery.fcntl.ioctl", side_effect=OSError("no reflink")):
        reflinked = _checkpoint_copy(source, destination)

    assert not reflinked
    assert destination.read_bytes() == source.read_bytes()
