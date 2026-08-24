#!/usr/bin/env python3
"""Build a reproducible Ubuntu 24.04 amd64 package without install-time downloads."""

from __future__ import annotations

import argparse
import email.policy
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

from acornfs.nautilus_install import (
    DESKTOP_FILE_NAME,
    EXTENSION_NAME,
    MIME_PACKAGE_NAME,
    desktop_file_content,
    extension_loader_content,
    mime_package_content,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "nautilus-acornfs"
ARCHITECTURE = "amd64"
PYTHON_ROOT = PurePosixPath("usr/lib/python3/dist-packages")
VENDOR_REQUIREMENTS = PROJECT_ROOT / "packaging/debian/vendor-requirements.txt"
RUNTIME_DEPENDENCIES = (
    "python3 (>= 3.11~)",
    "python3 (<< 3.13)",
    "fuse3",
    "python3-pyfuse3 (>= 3.3)",
    "python3-trio (>= 0.24)",
    "python3-stevedore (>= 1:5.0)",
    "python3-nautilus",
    "gir1.2-nautilus-4.0",
    "shared-mime-info",
    "desktop-file-utils",
    "libnotify-bin",
    "zenity",
)
_LOCKED_REQUIREMENT = re.compile(r"^([a-z0-9-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})$")


def _run(
    command: list[str],
    *,
    environment: dict[str, str],
    cwd: Path | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=capture_output,
    )


def _normalise_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _locked_requirements() -> dict[str, tuple[str, str]]:
    locked: dict[str, tuple[str, str]] = {}
    for line in VENDOR_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = _LOCKED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise RuntimeError(f"invalid vendored requirement: {line}")
        name, version, digest = match.groups()
        normalised = _normalise_name(name)
        if normalised in locked:
            raise RuntimeError(f"duplicate vendored requirement: {name}")
        locked[normalised] = (version, digest)
    if not locked:
        raise RuntimeError("the vendored requirement lock is empty")
    return locked


def _source_date_epoch(explicit: int | None) -> int:
    if explicit is not None:
        epoch = explicit
    elif configured := os.environ.get("SOURCE_DATE_EPOCH"):
        try:
            epoch = int(configured)
        except ValueError as exc:
            raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer") from exc
    else:
        result = _run(
            ["git", "log", "-1", "--format=%ct"],
            environment=os.environ.copy(),
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        epoch = int(result.stdout.strip())
    if epoch < 0:
        raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer")
    return epoch


def _project_version() -> str:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version: Any = configuration["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise RuntimeError("the project version is not a release-compatible semantic version")
    return version


def _debian_version(project_version: str) -> str:
    exact = subprocess.run(
        ["git", "describe", "--exact-match", "--tags", "--match", f"v{project_version}"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if exact.returncode == 0 and exact.stdout.strip() == f"v{project_version}":
        return project_version
    result = _run(
        ["git", "show", "-s", "--format=%cd:%h", "--date=format:%Y%m%d", "HEAD"],
        environment=os.environ.copy(),
        cwd=PROJECT_ROOT,
        capture_output=True,
    )
    date, revision = result.stdout.strip().split(":", 1)
    return f"{project_version}+git{date}.{revision}"


def _build_environment(epoch: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": str(epoch),
            "TZ": "UTC",
        }
    )
    return environment


def _build_project_wheel(destination: Path, *, epoch: int) -> Path:
    _run(
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
        environment=_build_environment(epoch),
    )
    wheels = list(destination.glob("nautilus_acornfs-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one AcornFS wheel, found {len(wheels)}")
    return wheels[0]


def _download_vendor_wheels(destination: Path) -> list[Path]:
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--require-hashes",
            "--no-deps",
            "--only-binary=:all:",
            "--dest",
            str(destination),
            "--requirement",
            str(VENDOR_REQUIREMENTS),
        ],
        environment=os.environ.copy(),
    )
    return sorted(destination.glob("*.whl"))


def _safe_wheel_member(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts or not member.parts:
        raise RuntimeError(f"wheel contains an unsafe member: {name}")
    if any(part.endswith(".data") for part in member.parts):
        raise RuntimeError(f"wheel contains an unsupported data-layout member: {name}")
    return member


def _wheel_identity(path: Path) -> tuple[str, str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        wheel_names = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
        if len(metadata_names) != 1 or len(wheel_names) != 1:
            raise RuntimeError(f"wheel has invalid distribution metadata: {path.name}")
        metadata = BytesParser(policy=email.policy.default).parsebytes(
            archive.read(metadata_names[0])
        )
        wheel_metadata = BytesParser(policy=email.policy.default).parsebytes(
            archive.read(wheel_names[0])
        )
        name = metadata.get("Name")
        version = metadata.get("Version")
        licence = metadata.get("License-Expression") or metadata.get("License")
        if not all(isinstance(value, str) and value for value in (name, version, licence)):
            raise RuntimeError(f"wheel has incomplete identity or licence metadata: {path.name}")
        if wheel_metadata.get("Root-Is-Purelib", "").lower() != "true":
            raise RuntimeError(f"vendored wheel is not pure Python: {path.name}")
        tags = wheel_metadata.get_all("Tag", [])
        if not tags or not all(tag.endswith("-none-any") for tag in tags):
            raise RuntimeError(f"vendored wheel is not platform independent: {path.name}")
        licence_names = [
            entry
            for entry in archive.namelist()
            if ".dist-info/" in entry and PurePosixPath(entry).name.upper().startswith("LICENSE")
        ]
        if not licence_names:
            raise RuntimeError(f"vendored wheel does not carry its licence text: {path.name}")
    return _normalise_name(name), version, licence


def verify_vendor_wheels(wheels: list[Path]) -> dict[str, dict[str, str]]:
    """Verify the hash, version, pure-Python boundary and MIT licence of every wheel."""

    locked = _locked_requirements()
    inventory: dict[str, dict[str, str]] = {}
    for wheel in wheels:
        name, version, licence = _wheel_identity(wheel)
        if name not in locked:
            raise RuntimeError(f"downloaded an unlocked vendored distribution: {name}")
        expected_version, expected_digest = locked[name]
        digest = _sha256(wheel)
        if version != expected_version or digest != expected_digest:
            raise RuntimeError(f"vendored wheel does not match its lock: {wheel.name}")
        if licence not in {"MIT", "MIT License"}:
            raise RuntimeError(f"vendored wheel does not declare the audited MIT licence: {name}")
        if name in inventory:
            raise RuntimeError(f"duplicate vendored wheel: {name}")
        inventory[name] = {
            "filename": wheel.name,
            "license": "MIT",
            "sha256": digest,
            "version": version,
        }
    missing = sorted(set(locked) - set(inventory))
    if missing:
        raise RuntimeError(f"missing vendored wheels: {', '.join(missing)}")
    return inventory


def _write(
    root: Path,
    installed: PurePosixPath,
    content: bytes,
    owned: set[str],
    *,
    mode: int = 0o644,
) -> None:
    rendered = "/" + installed.as_posix()
    if rendered in owned:
        raise RuntimeError(f"package payload path overlaps: {rendered}")
    target = root / installed.as_posix()
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(mode)
    owned.add(rendered)


def _extract_wheel(path: Path, root: Path, owned: set[str]) -> None:
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            member = _safe_wheel_member(info.filename)
            if info.is_dir():
                continue
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type not in (0, stat.S_IFREG):
                raise RuntimeError(f"wheel contains a non-regular member: {info.filename}")
            _write(root, PYTHON_ROOT / member, archive.read(info), owned)


def _stage_documentation(root: Path, owned: set[str]) -> None:
    destination = PurePosixPath("usr/share/doc") / PACKAGE_NAME
    for name in ("README.md", "CHANGELOG.md", "LICENSE"):
        _write(root, destination / name, (PROJECT_ROOT / name).read_bytes(), owned)
    for source in sorted((PROJECT_ROOT / "docs").glob("*.md")):
        _write(root, destination / "manual" / source.name, source.read_bytes(), owned)
    _write(
        root,
        destination / "copyright",
        (PROJECT_ROOT / "packaging/debian/copyright").read_bytes(),
        owned,
    )


def _stage_desktop(root: Path, owned: set[str]) -> None:
    files = {
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
    for installed, content in files.items():
        _write(root, installed, content, owned)


def _installed_size(root: Path) -> int:
    total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return max(1, (total + 1023) // 1024)


def _stage_control(root: Path, *, version: str, installed_size: int, oaknut_version: str) -> None:
    control = f"""Package: {PACKAGE_NAME}
Version: {version}
Section: utils
Priority: optional
Architecture: {ARCHITECTURE}
Maintainer: Pete Clarke <249926147+peteclarke-del@users.noreply.github.com>
Homepage: https://github.com/peteclarke-del/nautilus-acornfs
Depends: {", ".join(RUNTIME_DEPENDENCIES)}
Installed-Size: {installed_size}
X-Oaknut-Version: {oaknut_version}
Description: Nautilus integration for Acorn disk images
 Mount validated Acorn ADFS, DFS, FileCore, MMB, ROMFS and BeebSCSI images
 through FUSE 3, with protected writes, recovery and GNOME Files integration.
"""
    maintainer_script = """#!/bin/sh
set -e
if command -v update-mime-database >/dev/null 2>&1; then
    update-mime-database /usr/share/mime
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications
fi
exit 0
"""
    debian = root / "DEBIAN"
    debian.mkdir(mode=0o755)
    control_path = debian / "control"
    control_path.write_text(control, encoding="utf-8")
    control_path.chmod(0o644)
    for name in ("postinst", "postrm"):
        target = debian / name
        target.write_text(maintainer_script, encoding="utf-8")
        target.chmod(0o755)


def _normalise_timestamps(root: Path, *, epoch: int) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o755)
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    root.chmod(0o755)
    os.utime(root, (epoch, epoch), follow_symlinks=False)


def stage_package(
    root: Path,
    *,
    project_wheel: Path,
    vendor_wheels: list[Path],
    version: str,
    epoch: int,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Create one policy-bounded package root and return its payload and vendor inventory."""

    if root.is_symlink():
        raise RuntimeError(f"package root must not be a symbolic link: {root}")
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"package root is not empty: {root}")
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    inventory = verify_vendor_wheels(vendor_wheels)
    oaknut_versions = {
        item["version"] for name, item in inventory.items() if name.startswith("oaknut-")
    }
    if oaknut_versions != {"12.15.1"}:
        raise RuntimeError("the Debian package must vendor exactly Oaknut 12.15.1")
    owned: set[str] = set()
    _extract_wheel(project_wheel, root, owned)
    for wheel in vendor_wheels:
        _extract_wheel(wheel, root, owned)
    _write(
        root,
        PurePosixPath("usr/bin/acornfs"),
        b"#!/usr/bin/python3\nfrom acornfs.cli import main\nraise SystemExit(main())\n",
        owned,
        mode=0o755,
    )
    _stage_documentation(root, owned)
    _stage_desktop(root, owned)
    _stage_control(
        root,
        version=version,
        installed_size=_installed_size(root),
        oaknut_version=oaknut_versions.pop(),
    )
    _normalise_timestamps(root, epoch=epoch)
    return sorted(owned), inventory


def _build_one(
    destination: Path,
    *,
    project_wheel: Path,
    vendor_wheels: list[Path],
    version: str,
    epoch: int,
) -> tuple[Path, list[str], dict[str, dict[str, str]]]:
    root = destination / "root"
    owned, inventory = stage_package(
        root,
        project_wheel=project_wheel,
        vendor_wheels=vendor_wheels,
        version=version,
        epoch=epoch,
    )
    package = destination / f"{PACKAGE_NAME}_{version}_{ARCHITECTURE}.deb"
    _run(
        ["dpkg-deb", "--root-owner-group", "--build", str(root), str(package)],
        environment=_build_environment(epoch),
    )
    return package, owned, inventory


def build_deb(output: Path, *, epoch: int | None = None) -> tuple[Path, Path]:
    """Build twice, verify reproducibility and publish the package plus its manifest."""

    if platform.machine() not in {"amd64", "x86_64"}:
        raise RuntimeError("Debian package production is currently limited to amd64")
    if output.is_symlink():
        raise RuntimeError(f"Debian output directory must not be a symbolic link: {output}")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Debian output directory is not empty: {output}")
    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    resolved_epoch = _source_date_epoch(epoch)
    project_version = _project_version()
    version = _debian_version(project_version)
    with tempfile.TemporaryDirectory(prefix="acornfs-deb-build-") as temporary:
        workspace = Path(temporary)
        wheels = workspace / "wheels"
        wheels.mkdir()
        project_wheel = _build_project_wheel(wheels, epoch=resolved_epoch)
        vendor_directory = workspace / "vendor"
        vendor_directory.mkdir()
        vendor_wheels = _download_vendor_wheels(vendor_directory)
        verify_vendor_wheels(vendor_wheels)
        builds: list[tuple[Path, list[str], dict[str, dict[str, str]]]] = []
        for name in ("first", "second"):
            destination = workspace / name
            destination.mkdir()
            builds.append(
                _build_one(
                    destination,
                    project_wheel=project_wheel,
                    vendor_wheels=vendor_wheels,
                    version=version,
                    epoch=resolved_epoch,
                )
            )
        first, second = builds
        if _sha256(first[0]) != _sha256(second[0]):
            raise RuntimeError("Debian package is not reproducible")
        if first[1:] != second[1:]:
            raise RuntimeError("Debian package builds produced different manifests")
        package = output / first[0].name
        shutil.copyfile(first[0], package)
        manifest = output / f"{PACKAGE_NAME}-deb-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "architecture": ARCHITECTURE,
                    "artifact": {
                        "filename": package.name,
                        "sha256": _sha256(package),
                    },
                    "depends": list(RUNTIME_DEPENDENCIES),
                    "files": first[1],
                    "package": PACKAGE_NAME,
                    "publishable": True,
                    "schema": 1,
                    "source_version": project_version,
                    "vendor": first[2],
                    "version": version,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return package, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/debian"))
    parser.add_argument("--source-date-epoch", type=int)
    arguments = parser.parse_args()
    package, manifest = build_deb(arguments.output, epoch=arguments.source_date_epoch)
    print(f"Verified reproducible Debian package: {package}")
    print(f"Package manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
