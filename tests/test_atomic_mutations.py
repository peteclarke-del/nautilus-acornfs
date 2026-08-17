from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.errors import AcornFSError
from acornfs.recovery import pending_recovery, recover_image
from tests.image_fixture import create_beebscsi_image, create_dfs_floppy


class FaultController:
    def __init__(self) -> None:
        self._stage: str | None = None

    def arm(self, stage: str) -> None:
        self._stage = stage

    def __call__(self, stage: str) -> None:
        if stage == self._stage:
            self._stage = None
            raise RuntimeError(f"injected fault at {stage}")


def test_create_mkdir_unlink_and_rmdir_faults_are_atomic(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    faults = FaultController()
    with ReadOnlyImage.open(dat_path, writable=True, fault_injector=faults) as image:
        faults.arm("create.after")
        with pytest.raises(RuntimeError, match="create.after"):
            image.create_file(ROOT_INODE, b"FAILED")
        assert image.lookup(ROOT_INODE, b"FAILED") is None

        faults.arm("mkdir.after")
        with pytest.raises(RuntimeError, match="mkdir.after"):
            image.make_directory(ROOT_INODE, b"FAILDIR")
        assert image.lookup(ROOT_INODE, b"FAILDIR") is None

        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        original = image.read(readme.inode, 0, readme.size)
        faults.arm("unlink.after")
        with pytest.raises(RuntimeError, match="unlink.after"):
            image.remove(ROOT_INODE, b"README", directory=False)
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        assert image.read(readme.inode, 0, readme.size) == original

        faults.arm("rmdir.after")
        with pytest.raises(RuntimeError, match="rmdir.after"):
            image.remove(ROOT_INODE, b"EMPTY", directory=True)
        assert image.lookup(ROOT_INODE, b"EMPTY") is not None
        assert image.integrity_report().safe_for_write


def test_content_and_metadata_faults_restore_the_before_image(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    faults = FaultController()
    with ReadOnlyImage.open(dat_path, writable=True, fault_injector=faults) as image:
        readme = image.lookup(ROOT_INODE, b"README")
        assert readme is not None
        original_data = image.read(readme.inode, 0, readme.size)
        original_metadata = image.acorn_metadata(readme.inode)

        faults.arm("replace.after")
        with pytest.raises(RuntimeError, match="replace.after"):
            image.replace_file(readme.inode, b"replacement that must disappear")
        assert image.read(readme.inode, 0, readme.size) == original_data

        faults.arm("metadata.after")
        with pytest.raises(RuntimeError, match="metadata.after"):
            image.set_acorn_metadata(readme.inode, load_address=0x12345678, locked=True)
        assert image.acorn_metadata(readme.inode) == original_metadata
        assert image.nodes[readme.inode].locked is False


def test_replacement_rename_restores_both_names_after_midpoint_fault(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    faults = FaultController()
    with ReadOnlyImage.open(dat_path, writable=True, fault_injector=faults) as image:
        source = image.create_file(ROOT_INODE, b"SAVEFILE")
        image.replace_file(source.inode, b"new version")
        destination = image.lookup(ROOT_INODE, b"README")
        assert destination is not None
        old_version = image.read(destination.inode, 0, destination.size)

        faults.arm("rename.destination_removed")
        with pytest.raises(RuntimeError, match="destination_removed"):
            image.rename(ROOT_INODE, b"SAVEFILE", ROOT_INODE, b"README")

        source = image.lookup(ROOT_INODE, b"SAVEFILE")
        destination = image.lookup(ROOT_INODE, b"README")
        assert source is not None and destination is not None
        assert image.read(source.inode, 0, source.size) == b"new version"
        assert image.read(destination.inode, 0, destination.size) == old_version
        assert image.integrity_report().safe_for_write


def test_cross_parent_directory_move_updates_adfs_parent_address(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        moving = image.make_directory(ROOT_INODE, b"MOVING")
        image.make_directory(moving.inode, b"CHILD")
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert docs is not None
        moved = image.rename(ROOT_INODE, b"MOVING", docs.inode, b"MOVED")
        assert moved.parent_inode == docs.inode

        adfs = image._mount._adfs  # type: ignore[attr-defined]
        _root, docs_entry = adfs.path("$.DOCS")._resolve()
        _parent, moved_entry = adfs.path("$.DOCS.MOVED")._resolve()
        moved_directory = adfs._read_directory_at(moved_entry.start_sector)
        assert moved_directory.name == "MOVED"
        assert moved_directory.parent_address == docs_entry.start_sector

        renamed = image.rename(docs.inode, b"MOVED", docs.inode, b"RENAMED")
        _parent, renamed_entry = adfs.path("$.DOCS.RENAMED")._resolve()
        renamed_directory = adfs._read_directory_at(renamed_entry.start_sector)
        assert renamed.inode == moved.inode
        assert renamed_directory.name == "RENAMED"
        assert renamed_directory.parent_address == docs_entry.start_sector


def test_concurrent_creates_commit_unique_consistent_inodes(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    names = [f"FILE{index:02d}".encode() for index in range(24)]
    with ReadOnlyImage.open(dat_path, writable=True) as image:
        with ThreadPoolExecutor(max_workers=8) as executor:
            nodes = list(executor.map(lambda name: image.create_file(ROOT_INODE, name), names))
        assert len({node.inode for node in nodes}) == len(names)
        assert all(image.lookup(ROOT_INODE, name) is not None for name in names)
        assert image.integrity_report().safe_for_write


def test_successful_mutations_avoid_full_validation_until_unmount(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    with patch.object(image, "integrity_report", wraps=image.integrity_report) as validate:
        image.create_file(ROOT_INODE, b"FASTPATH")
        assert validate.call_count == 0
    image.close()


def test_disc_cycle_id_advances_once_per_committed_mutation(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    faults = FaultController()
    with ReadOnlyImage.open(dat_path, writable=True, fault_injector=faults) as image:
        free_space_map = image._mount._adfs._fsm  # type: ignore[attr-defined]
        original = free_space_map.disc_id

        created = image.create_file(ROOT_INODE, b"CYCLE")
        assert free_space_map.disc_id == (original + 1) & 0xFFFF

        image.replace_file(created.inode, b"one logical write")
        committed = (original + 2) & 0xFFFF
        assert free_space_map.disc_id == committed

        faults.arm("metadata.after")
        with pytest.raises(RuntimeError, match="metadata.after"):
            image.set_acorn_metadata(created.inode, locked=True)
        assert free_space_map.disc_id == committed
        assert image.integrity_report().safe_for_write


def test_unverifiable_rollback_fails_closed_and_retains_checkpoint(tmp_path: Path) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    faults = FaultController()
    image = ReadOnlyImage.open(dat_path, writable=True, fault_injector=faults)
    faults.arm("create.after")
    with (
        patch.object(image, "_finish_rollback", side_effect=OSError("injected fsync failure")),
        pytest.raises(AcornFSError, match="rollback could not be verified"),
    ):
        image.create_file(ROOT_INODE, b"FAILED")
    with pytest.raises(AcornFSError, match="session has failed"):
        image.create_file(ROOT_INODE, b"BLOCKED")
    image.close()
    assert pending_recovery(dat_path) is not None
    assert recover_image(dat_path, discard=True) == "Recovery checkpoint discarded."


def test_standalone_mutation_fault_restores_private_before_image(tmp_path: Path) -> None:
    image_path = create_dfs_floppy(tmp_path)
    faults = FaultController()
    with ReadOnlyImage.open(image_path, writable=True, fault_injector=faults) as image:
        default = image.lookup(ROOT_INODE, b"$")
        assert default is not None
        hello = image.lookup(default.inode, b"HELLO")
        assert hello is not None
        original = image.read(hello.inode, 0, hello.size)
        checkpoint_directory = image._checkpoint.directory  # type: ignore[union-attr]

        faults.arm("replace.after")
        with pytest.raises(RuntimeError, match="replace.after"):
            image.replace_file(hello.inode, b"this mutation must be rolled back")

        assert image.read(hello.inode, 0, hello.size) == original
        assert not tuple(checkpoint_directory.glob("operation-*.bin"))
        created = image.create_file(default.inode, b"RECOVER")
        image.replace_file(created.inode, b"the session remains usable")
