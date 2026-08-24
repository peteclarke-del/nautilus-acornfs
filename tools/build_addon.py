#!/usr/bin/env python3
"""Build a self-contained per-user Nautilus AcornFS add-on bundle."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path


def create_bundle(wheel: Path, installer: Path, destination: Path, *, version: str) -> Path:
    """Package one wheel and its standalone lifecycle installer."""

    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"nautilus-acornfs-addon-{version}.zip"
    instructions = f"""Nautilus AcornFS add-on {version}

Requirements: Ubuntu 24.04 amd64, Python 3.11 or later, FUSE 3, python3-venv,
python3-nautilus, gir1.2-nautilus-4.0, shared-mime-info, desktop-file-utils,
libnotify-bin and zenity.

Install for the current user:
    python3 install.py --restart install

Upgrade an existing managed installation:
    python3 install.py --restart upgrade

Uninstall while retaining preferences and recovery data:
    python3 install.py --restart uninstall
"""
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.", suffix=".tmp", dir=destination, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(wheel, wheel.name)
            archive.write(installer, "install.py")
            archive.writestr("INSTALL.txt", instructions)
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target


def build_addon(project: Path, destination: Path) -> Path:
    """Build a wheel and wrap it in the standalone add-on archive."""

    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    with tempfile.TemporaryDirectory(prefix="acornfs-addon-wheel-") as temporary:
        wheel_directory = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(wheel_directory),
            ],
            check=True,
            cwd=project,
        )
        wheels = list(wheel_directory.glob("nautilus_acornfs-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected exactly one AcornFS wheel, found {len(wheels)}")
        return create_bundle(
            wheels[0], project / "tools" / "user_install.py", destination, version=version
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    arguments = parser.parse_args()
    project = Path(__file__).resolve().parent.parent
    print(build_addon(project, arguments.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
