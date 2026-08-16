"""Persistent per-user AcornFS preferences."""

from __future__ import annotations

import json
import os
import pwd
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acornfs.errors import AcornFSError
from acornfs.mounts import runtime_root

CONFIG_VERSION = 1
MOUNT_ROOT_ENV = "ACORNFS_MOUNT_ROOT"


@dataclass(frozen=True, slots=True)
class MountLocation:
    mode: str
    root: Path
    source: str


def _account_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    return Path(configured).expanduser() if configured else _account_home() / ".config"


def preferences_path() -> Path:
    return config_home() / "acornfs" / "preferences.json"


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_location(value: str, *, source: str) -> MountLocation:
    candidate = value.strip()
    if candidate == "sidebar":
        return MountLocation("sidebar", _account_home() / "AcornFS Mounts", source)
    if candidate == "runtime":
        return MountLocation("runtime", runtime_root() / "images", source)
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        raise AcornFSError("The mount location must be 'sidebar', 'runtime', or an absolute path.")
    path = path.resolve(strict=False)
    if path == Path("/"):
        raise AcornFSError("The filesystem root cannot be used as the AcornFS mount location.")
    return MountLocation("custom", path, source)


def _read_preferences() -> dict[str, Any]:
    path = preferences_path()
    if not path.exists():
        return {}
    try:
        if path.is_symlink() or path.stat().st_size > 16 * 1024:
            raise AcornFSError(f"The AcornFS preferences file is unsafe: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except AcornFSError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcornFSError(f"Could not read AcornFS preferences: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != CONFIG_VERSION:
        raise AcornFSError("The AcornFS preferences file has an unsupported format.")
    return payload


def mount_location() -> MountLocation:
    """Resolve the effective mount root, with the environment taking precedence."""

    environment = os.environ.get(MOUNT_ROOT_ENV)
    if environment is not None:
        return _parse_location(environment, source="environment")
    configured = _read_preferences().get("mount_location", "sidebar")
    if not isinstance(configured, str):
        raise AcornFSError("The configured AcornFS mount location is invalid.")
    source = "default" if configured == "sidebar" and not preferences_path().exists() else "user"
    return _parse_location(configured, source=source)


def mount_root() -> Path:
    return mount_location().root


def set_mount_location(value: str) -> MountLocation:
    """Validate and atomically persist a per-user mount location."""

    parsed = _parse_location(value, source="user")
    stored = parsed.mode if parsed.mode != "custom" else str(parsed.root)
    path = preferences_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.is_symlink():
        raise AcornFSError(f"Refusing to replace a symbolic-link preferences file: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = {"version": CONFIG_VERSION, "mount_location": stored}
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AcornFSError(f"Could not save AcornFS preferences: {exc}") from exc
    return parsed


def reset_mount_location() -> MountLocation:
    """Remove the persisted preference and return the effective default/override."""

    path = preferences_path()
    if path.is_symlink():
        raise AcornFSError(f"Refusing to remove a symbolic-link preferences file: {path}")
    try:
        path.unlink(missing_ok=True)
        if path.parent.is_dir():
            _sync_directory(path.parent)
    except OSError as exc:
        raise AcornFSError(f"Could not reset AcornFS preferences: {exc}") from exc
    return mount_location()


__all__ = [
    "MountLocation",
    "config_home",
    "mount_location",
    "mount_root",
    "preferences_path",
    "reset_mount_location",
    "set_mount_location",
]
