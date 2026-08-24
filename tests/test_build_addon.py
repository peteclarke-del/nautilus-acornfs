import zipfile
from pathlib import Path

from tools.build_addon import create_bundle


def test_addon_bundle_contains_wheel_installer_and_instructions(tmp_path: Path) -> None:
    wheel = tmp_path / "nautilus_acornfs-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    installer = tmp_path / "user_install.py"
    installer.write_text("# installer\n", encoding="utf-8")
    output = tmp_path / "output"

    bundle = create_bundle(wheel, installer, output, version="0.1.0")

    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {wheel.name, "install.py", "INSTALL.txt"}
        assert "python3 install.py --restart install" in archive.read("INSTALL.txt").decode()

    assert create_bundle(wheel, installer, output, version="0.1.0") == bundle
