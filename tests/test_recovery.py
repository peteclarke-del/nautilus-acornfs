import subprocess
import sys
from pathlib import Path

from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from acornfs.recovery import pending_recovery, recover_image
from tests.image_fixture import create_beebscsi_image


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
