import subprocess
import sys
from pathlib import Path

import pytest

from acornfs.core import discover_pair
from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.errors import OperationCancelled
from acornfs.recovery import RecoveryCheckpoint, pending_recovery, recover_image
from tests.image_fixture import create_adfs_new_map_pair, create_beebscsi_image, create_dfs_floppy


def test_recovery_module_imports_cleanly_in_fresh_process() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from acornfs.recovery import pending_recovery"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_clean_writable_session_removes_checkpoint(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True):
        assert pending_recovery(dat_path) is not None
    assert pending_recovery(dat_path) is None


def test_clean_checkpoint_removes_manifest_before_backup_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    removed: list[str] = []
    original_unlink = Path.unlink

    def recording_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name in {"manifest.json", "image.dat", "image.dsc"}:
            removed.append(path.name)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", recording_unlink)
    image = ReadOnlyImage.open(dat_path, writable=True)
    removed.clear()
    image.close()
    assert removed == ["manifest.json", "image.dat", "image.dsc"]


def test_checkpoint_creation_cleans_orphan_backups_without_a_manifest(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    pair = discover_pair(dat_path)
    interrupted_cleanup = RecoveryCheckpoint.create(pair)
    (interrupted_cleanup.directory / "manifest.json").unlink()

    replacement = RecoveryCheckpoint.create(pair)
    assert (replacement.directory / "manifest.json").is_file()
    replacement.complete()


def test_checkpoint_creation_reports_copied_bytes(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    pair = discover_pair(dat_path)
    updates: list[tuple[int, int]] = []

    checkpoint = RecoveryCheckpoint.create(
        pair, progress=lambda copied, total: updates.append((copied, total))
    )
    total = dat_path.stat().st_size + dsc_path.stat().st_size
    assert updates
    assert updates[-1] == (total, total)
    assert all(
        previous[0] <= current[0] for previous, current in zip(updates, updates[1:], strict=False)
    )
    checkpoint.complete()


def test_interrupted_session_can_restore_checkpoint(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    readme = image.lookup(ROOT_INODE, b"README")
    assert readme is not None
    image.replace_file(readme.inode, b"Uncommitted change\r")
    image.close(clean=False)

    info = pending_recovery(dat_path)
    assert info is not None
    assert "Use --restore" in recover_image(dat_path)
    assert recover_image(dat_path, restore=True) == "Recovery checkpoint restored."
    assert pending_recovery(dat_path) is None

    with ReadOnlyImage.open(dat_path) as restored:
        readme = restored.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert restored.read(readme.inode, 0, 1024) == b"Hello from AcornFS\r"


def test_interrupted_standalone_session_can_restore_checkpoint(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path)
    image = ReadOnlyImage.open(image_path, writable=True)
    default = image.lookup(ROOT_INODE, b"$")
    assert default is not None
    hello = image.lookup(default.inode, b"HELLO")
    assert hello is not None
    image.replace_file(hello.inode, b"Uncommitted DFS change\r")
    image.close(clean=False)

    info = pending_recovery(image_path)
    assert info is not None
    assert info.dsc_path is None
    assert recover_image(image_path, restore=True) == "Recovery checkpoint restored."
    assert pending_recovery(image_path) is None

    with ReadOnlyImage.open(image_path) as restored:
        default = restored.lookup(ROOT_INODE, b"$")
        assert default is not None
        hello = restored.lookup(default.inode, b"HELLO")
        assert hello is not None
        assert restored.read(hello.inode, 0, 1024) == b"Hello from DFS drive 0\r"


def test_interrupted_new_map_pair_restores_dat_without_changing_descriptor(
    tmp_path: Path,
) -> None:
    dat_path, dsc_path = create_adfs_new_map_pair(tmp_path)
    descriptor = dsc_path.read_bytes()
    image = ReadOnlyImage.open(dsc_path, writable=True)
    hello = image.lookup(ROOT_INODE, b"HELLO")
    assert hello is not None
    image.replace_file(hello.inode, b"Uncommitted New Map change\r")
    image.close(clean=False)

    info = pending_recovery(dsc_path)
    assert info is not None
    assert info.dsc_path is None
    assert recover_image(dsc_path, restore=True) == "Recovery checkpoint restored."
    assert dsc_path.read_bytes() == descriptor

    with ReadOnlyImage.open(dat_path) as restored:
        hello = restored.lookup(ROOT_INODE, b"HELLO")
        assert hello is not None
        assert restored.read(hello.inode, 0, 1024) == b"Hello from FileCore\r"


def test_crashed_writer_leaves_a_restorable_checkpoint(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    script = """
import os
import sys
from acornfs.core.image import ROOT_INODE, ReadOnlyImage

image = ReadOnlyImage.open(sys.argv[1], writable=True)
readme = image.lookup(ROOT_INODE, b"README")
assert readme is not None
image.replace_file(readme.inode, b"change before simulated crash")
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", script, str(dat_path)], check=False)
    assert result.returncode == 17
    assert pending_recovery(dat_path) is not None
    assert recover_image(dat_path, restore=True) == "Recovery checkpoint restored."

    with ReadOnlyImage.open(dat_path) as restored:
        readme = restored.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert restored.read(readme.inode, 0, readme.size) == b"Hello from AcornFS\r"


def test_cancelled_restore_keeps_current_pair_and_checkpoint(tmp_path: Path) -> None:
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    readme = image.lookup(ROOT_INODE, b"README")
    assert readme is not None
    image.replace_file(readme.inode, b"Current image must remain in place")
    image.close(clean=False)
    current_dat = dat_path.read_bytes()
    current_dsc = dsc_path.read_bytes()
    calls = 0

    def cancel_during_staging() -> bool:
        nonlocal calls
        calls += 1
        return calls == 4

    with pytest.raises(OperationCancelled, match="cancelled safely"):
        recover_image(dat_path, restore=True, cancelled=cancel_during_staging)

    assert dat_path.read_bytes() == current_dat
    assert dsc_path.read_bytes() == current_dsc
    assert pending_recovery(dat_path) is not None
    assert not list(tmp_path.glob(".*.acornfs-restore-*"))


def test_recovery_results_are_translatable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    monkeypatch.setattr("acornfs.recovery._", lambda message: f"translated: {message}")

    assert recover_image(dat_path) == "translated: No recovery checkpoint is pending."
