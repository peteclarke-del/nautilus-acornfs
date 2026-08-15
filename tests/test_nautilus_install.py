import sys
import sysconfig
from pathlib import Path

import pytest

from acornfs.errors import AcornFSError
from acornfs.nautilus_install import install_extension, uninstall_extension


def test_installs_and_removes_user_extension(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))  # type: ignore[attr-defined]
    target = install_extension()
    content = target.read_text(encoding="utf-8")
    assert target == tmp_path / "nautilus-python" / "extensions" / "acornfs.py"
    assert repr(sys.executable) in content
    assert str(sysconfig.get_path("purelib")) in content
    assert "AcornFSMenuProvider" in content
    assert uninstall_extension() == target
    assert not target.exists()


def test_refuses_to_remove_foreign_extension(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))  # type: ignore[attr-defined]
    target = tmp_path / "nautilus-python" / "extensions" / "acornfs.py"
    target.parent.mkdir(parents=True)
    target.write_text("# user file\n", encoding="utf-8")
    with pytest.raises(AcornFSError, match="Refusing"):
        uninstall_extension()
