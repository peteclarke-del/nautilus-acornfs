import json
import zipfile
from pathlib import Path

import pytest

from tools.debian_staging import stage


def _wheel(path: Path, *, unsafe_member: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("acornfs/__init__.py", b'__version__ = "0.1.0"\n')
        archive.writestr("acornfs/cli.py", b"def main(): return 0\n")
        archive.writestr("acornfs/fuse_adapter/__init__.py", b"FUSE = True\n")
        archive.writestr("acornfs_nautilus/__init__.py", b"NAUTILUS = True\n")
        archive.writestr("nautilus_acornfs-0.1.0.dist-info/METADATA", b"Version: 0.1.0\n")
        if unsafe_member is not None:
            archive.writestr(unsafe_member, b"escape")
    return path


def test_staging_splits_core_fuse_and_nautilus_without_overlap(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "fixture.whl")
    output = tmp_path / "stage"

    manifest_path = stage(output, wheel=wheel)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["architecture"] == "amd64"
    assert payload["publishable"] is False
    packages = payload["packages"]
    assert set(packages) == {
        "nautilus-acornfs-core",
        "nautilus-acornfs-fuse",
        "nautilus-acornfs-nautilus",
    }
    owned = [path for package in packages.values() for path in package["files"]]
    assert len(owned) == len(set(owned))
    assert (
        "/usr/lib/python3/dist-packages/acornfs/cli.py"
        in packages["nautilus-acornfs-core"]["files"]
    )
    assert (
        "/usr/lib/python3/dist-packages/acornfs/fuse_adapter/__init__.py"
        in packages["nautilus-acornfs-fuse"]["files"]
    )
    assert (
        "/usr/lib/python3/dist-packages/acornfs_nautilus/__init__.py"
        in packages["nautilus-acornfs-nautilus"]["files"]
    )
    assert (output / "nautilus-acornfs-core/usr/bin/acornfs").stat().st_mode & 0o111
    loader = output / (
        "nautilus-acornfs-nautilus/usr/share/nautilus-python/extensions/nautilus_acornfs.py"
    )
    assert "configure_command(['/usr/bin/acornfs'])" in loader.read_text(encoding="utf-8")
    desktop = output / (
        "nautilus-acornfs-nautilus/usr/share/applications/org.acornfs.NautilusAcornFS.desktop"
    )
    assert 'Exec="/usr/bin/acornfs" desktop-open %U' in desktop.read_text(encoding="utf-8")


def test_staging_records_exact_oaknut_family_and_desktop_dependencies(tmp_path: Path) -> None:
    manifest = stage(tmp_path / "stage", wheel=_wheel(tmp_path / "fixture.whl"))
    packages = json.loads(manifest.read_text(encoding="utf-8"))["packages"]

    core_dependencies = packages["nautilus-acornfs-core"]["depends"]
    assert (
        len([dependency for dependency in core_dependencies if "python3-oaknut-" in dependency])
        == 13
    )
    assert all("(>= 12.15.1)" in dependency for dependency in core_dependencies[1:])
    assert all("(<< 12.15.2~)" in dependency for dependency in core_dependencies[1:])
    assert "python3-pyfuse3" in packages["nautilus-acornfs-fuse"]["depends"]
    assert "python3-nautilus" in packages["nautilus-acornfs-nautilus"]["depends"]


def test_staging_refuses_unsafe_wheel_members(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "unsafe.whl", unsafe_member="../escaped")

    with pytest.raises(RuntimeError, match="unsafe member"):
        stage(tmp_path / "stage", wheel=wheel)

    assert not (tmp_path / "escaped").exists()


def test_staging_refuses_nonempty_or_symbolic_link_output(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "fixture.whl")
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep").write_text("user data", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not empty"):
        stage(nonempty, wheel=wheel)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symbolic link"):
        stage(link, wheel=wheel)
