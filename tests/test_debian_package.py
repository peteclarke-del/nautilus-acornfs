import hashlib
import zipfile
from pathlib import Path

import pytest

from tools.debian_package import _locked_requirements, stage_package, verify_vendor_wheels


def _wheel(
    path: Path,
    *,
    name: str,
    version: str,
    licence: str = "MIT",
    members: dict[str, bytes] | None = None,
) -> Path:
    distribution = name.replace("-", "_")
    metadata_root = f"{distribution}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{metadata_root}/METADATA",
            f"Name: {name}\nVersion: {version}\nLicense-Expression: {licence}\n",
        )
        archive.writestr(
            f"{metadata_root}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{metadata_root}/licenses/LICENSE", b"MIT licence")
        for member, content in (members or {}).items():
            archive.writestr(member, content)
    return path


def test_vendor_lock_covers_runtime_without_command_only_oaknut_packages() -> None:
    locked = _locked_requirements()

    assert "oaknut-adfs" in locked
    assert "oaknut-dfs" in locked
    assert "oaknut-romfs" in locked
    assert "exit-codes" in locked
    assert "typename" in locked
    assert "oaknut-cli" not in locked
    assert "oaknut-disc" not in locked


def test_vendor_verification_refuses_hash_or_licence_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel(tmp_path / "fixture.whl", name="fixture", version="1.0")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "tools.debian_package._locked_requirements",
        lambda: {"fixture": ("1.0", digest)},
    )
    assert verify_vendor_wheels([wheel])["fixture"]["license"] == "MIT"

    wrong_licence = _wheel(
        tmp_path / "wrong.whl", name="fixture", version="1.0", licence="GPL-3.0-only"
    )
    wrong_digest = hashlib.sha256(wrong_licence.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "tools.debian_package._locked_requirements",
        lambda: {"fixture": ("1.0", wrong_digest)},
    )
    with pytest.raises(RuntimeError, match="audited MIT licence"):
        verify_vendor_wheels([wrong_licence])


def test_staging_builds_one_system_package_with_desktop_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _wheel(
        tmp_path / "project.whl",
        name="nautilus-acornfs",
        version="0.1.0",
        members={
            "acornfs/__init__.py": b'__version__ = "0.1.0"\n',
            "acornfs/cli.py": b"def main(): return 0\n",
            "acornfs_nautilus/__init__.py": b"NAUTILUS = True\n",
        },
    )
    vendor = _wheel(
        tmp_path / "vendor.whl",
        name="oaknut-filesystem",
        version="12.15.1",
        members={"oaknut/filesystem/__init__.py": b"FILESYSTEM = True\n"},
    )
    digest = hashlib.sha256(vendor.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "tools.debian_package._locked_requirements",
        lambda: {"oaknut-filesystem": ("12.15.1", digest)},
    )
    root = tmp_path / "root"

    files, inventory = stage_package(
        root,
        project_wheel=project,
        vendor_wheels=[vendor],
        version="0.1.0",
        epoch=1_700_000_000,
    )

    assert inventory["oaknut-filesystem"]["version"] == "12.15.1"
    assert "/usr/bin/acornfs" in files
    assert "/usr/lib/python3/dist-packages/oaknut/filesystem/__init__.py" in files
    assert "/usr/share/nautilus-python/extensions/nautilus_acornfs.py" in files
    assert "python3-pyfuse3" in (root / "DEBIAN/control").read_text(encoding="utf-8")
    assert (root / "DEBIAN/postinst").stat().st_mode & 0o111
    assert all(path.stat().st_mode & 0o777 == 0o755 for path in root.rglob("*") if path.is_dir())
    assert all(int(path.stat().st_mtime) == 1_700_000_000 for path in root.rglob("*"))


def test_staging_refuses_unsafe_or_overlapping_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _wheel(
        tmp_path / "project.whl",
        name="nautilus-acornfs",
        version="0.1.0",
        members={"shared.py": b"project"},
    )
    vendor = _wheel(
        tmp_path / "vendor.whl",
        name="oaknut-filesystem",
        version="12.15.1",
        members={"shared.py": b"vendor"},
    )
    digest = hashlib.sha256(vendor.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "tools.debian_package._locked_requirements",
        lambda: {"oaknut-filesystem": ("12.15.1", digest)},
    )

    with pytest.raises(RuntimeError, match="overlaps"):
        stage_package(
            tmp_path / "root",
            project_wheel=project,
            vendor_wheels=[vendor],
            version="0.1.0",
            epoch=1_700_000_000,
        )
