#!/usr/bin/env python3
"""Stage deterministic, non-distributable Debian package payloads for amd64."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

from acornfs.nautilus_install import (
    DESKTOP_FILE_NAME,
    EXTENSION_NAME,
    MIME_PACKAGE_NAME,
    desktop_file_content,
    extension_loader_content,
    mime_package_content,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = PurePosixPath("usr/lib/python3/dist-packages")
PACKAGE_NAMES = (
    "nautilus-acornfs-core",
    "nautilus-acornfs-fuse",
    "nautilus-acornfs-nautilus",
)
_OAKNUT_REQUIREMENT = re.compile(r"oaknut-([a-z0-9-]+)==([0-9]+\.[0-9]+\.[0-9]+)")


def _package_for(member: PurePosixPath) -> str:
    text = member.as_posix()
    if text.startswith("acornfs/fuse_adapter/"):
        return "nautilus-acornfs-fuse"
    if text.startswith("acornfs_nautilus/"):
        return "nautilus-acornfs-nautilus"
    return "nautilus-acornfs-core"


def _safe_wheel_member(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts or not member.parts:
        raise RuntimeError(f"wheel contains an unsafe member: {name}")
    return member


def _write(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def _oaknut_dependencies() -> tuple[str, list[str]]:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = configuration["project"]["dependencies"]
    parsed = [
        match.groups()
        for requirement in requirements
        if (match := _OAKNUT_REQUIREMENT.fullmatch(requirement)) is not None
    ]
    if not parsed or len(parsed) != len(requirements):
        raise RuntimeError(
            "every required runtime package must be an exactly pinned Oaknut package"
        )
    versions = {version for _name, version in parsed}
    if len(versions) != 1:
        raise RuntimeError("all Oaknut packages must use one exact release")
    version = versions.pop()
    major, minor, patch = (int(part) for part in version.split("."))
    upper = f"{major}.{minor}.{patch + 1}~"
    dependencies = [
        f"python3-oaknut-{name} (>= {version}), python3-oaknut-{name} (<< {upper})"
        for name, _version in sorted(parsed)
    ]
    return version, dependencies


def _package_dependencies(oaknut: list[str]) -> dict[str, list[str]]:
    return {
        "nautilus-acornfs-core": ["python3 (>= 3.11)", *oaknut],
        "nautilus-acornfs-fuse": [
            "nautilus-acornfs-core (= ${binary:Version})",
            "fuse3",
            "python3-pyfuse3",
            "python3-trio",
        ],
        "nautilus-acornfs-nautilus": [
            "nautilus-acornfs-core (= ${binary:Version})",
            "nautilus-acornfs-fuse (= ${binary:Version})",
            "python3-nautilus",
            "gir1.2-nautilus-4.0",
            "shared-mime-info",
            "desktop-file-utils",
            "libnotify-bin",
            "zenity",
        ],
    }


def _stage_wheel(wheel: Path, output: Path) -> dict[str, list[str]]:
    owned: dict[str, list[str]] = {name: [] for name in PACKAGE_NAMES}
    with zipfile.ZipFile(wheel) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            member = _safe_wheel_member(info.filename)
            if info.is_dir():
                continue
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type not in (0, stat.S_IFREG):
                raise RuntimeError(f"wheel contains a non-regular member: {info.filename}")
            package = _package_for(member)
            installed = PYTHON_ROOT / member
            _write(output / package / installed.as_posix(), archive.read(info))
            owned[package].append("/" + installed.as_posix())

    launcher = PurePosixPath("usr/bin/acornfs")
    _write(
        output / "nautilus-acornfs-core" / launcher.as_posix(),
        b"#!/usr/bin/python3\nfrom acornfs.cli import main\nraise SystemExit(main())\n",
        mode=0o755,
    )
    owned["nautilus-acornfs-core"].append("/" + launcher.as_posix())

    docs = ("README.md", "CHANGELOG.md")
    for name in docs:
        installed = PurePosixPath("usr/share/doc/nautilus-acornfs-core") / name
        _write(
            output / "nautilus-acornfs-core" / installed.as_posix(),
            (PROJECT_ROOT / name).read_bytes(),
        )
        owned["nautilus-acornfs-core"].append("/" + installed.as_posix())

    copyright_source = PROJECT_ROOT / "packaging/debian/copyright"
    for package in PACKAGE_NAMES:
        installed = PurePosixPath("usr/share/doc") / package / "copyright"
        _write(output / package / installed.as_posix(), copyright_source.read_bytes())
        owned[package].append("/" + installed.as_posix())

    desktop_files = {
        PurePosixPath("usr/share/nautilus-python/extensions") / EXTENSION_NAME: (
            extension_loader_content(["/usr/bin/acornfs"]).encode()
        ),
        PurePosixPath("usr/share/mime/packages") / MIME_PACKAGE_NAME: (
            mime_package_content().encode()
        ),
        PurePosixPath("usr/share/applications") / DESKTOP_FILE_NAME: (
            desktop_file_content(["/usr/bin/acornfs"]).encode()
        ),
    }
    for installed, content in desktop_files.items():
        _write(output / "nautilus-acornfs-nautilus" / installed.as_posix(), content)
        owned["nautilus-acornfs-nautilus"].append("/" + installed.as_posix())

    all_paths = [path for paths in owned.values() for path in paths]
    if len(all_paths) != len(set(all_paths)):
        raise RuntimeError("Debian package payload ownership overlaps")
    return {name: sorted(paths) for name, paths in owned.items()}


def _build_wheel(destination: Path) -> Path:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--outdir",
            str(destination),
            str(PROJECT_ROOT),
        ],
        check=True,
    )
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, found {len(wheels)}")
    return wheels[0]


def stage(output: Path, *, wheel: Path | None = None) -> Path:
    """Stage three disjoint package roots and return their ownership manifest."""

    if output.is_symlink():
        raise RuntimeError(f"staging output must not be a symbolic link: {output}")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"staging output is not empty: {output}")
    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="acornfs-debian-wheel-") as temporary:
        selected = (
            wheel.resolve(strict=True) if wheel is not None else _build_wheel(Path(temporary))
        )
        owned = _stage_wheel(selected, output)
    oaknut_version, oaknut_dependencies = _oaknut_dependencies()
    package_dependencies = _package_dependencies(oaknut_dependencies)
    manifest = output / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "architecture": "amd64",
                "blocked_by": [
                    f"Oaknut {oaknut_version} Debian packages or an approved vendoring plan",
                ],
                "packages": {
                    name: {
                        "depends": package_dependencies[name],
                        "files": owned[name],
                    }
                    for name in PACKAGE_NAMES
                },
                "publishable": False,
                "schema": 1,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/debian-staging"))
    parser.add_argument("--wheel", type=Path)
    arguments = parser.parse_args()
    manifest = stage(arguments.output, wheel=arguments.wheel)
    print(f"Staged three amd64 package roots: {manifest}")
    print("Not publishable: resolve the Oaknut packaging blocker first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
