"""Privacy-safe runtime diagnostics suitable for support attachments."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from acornfs.mounts import MountRecord, active_mounts


def _image_identity(record: MountRecord) -> str | None:
    if record.image_device is None or record.image_inode is None:
        return None
    value = f"{record.image_device}:{record.image_inode}".encode("ascii")
    return hashlib.sha256(value).hexdigest()[:12]


def diagnostic_report() -> dict[str, Any]:
    """Collect operational metadata without image data or absolute paths."""

    try:
        version = importlib.metadata.version("nautilus-acornfs")
    except importlib.metadata.PackageNotFoundError:
        version = "development"
    mounts = active_mounts()
    return {
        "privacy": "No image contents or absolute paths are included.",
        "runtime": {
            "acornfs": version,
            "python": platform.python_version(),
            "implementation": sys.implementation.name,
            "platform": platform.system(),
            "architecture": platform.machine(),
        },
        "fuse": {
            "device_present": Path("/dev/fuse").exists(),
            "device_accessible": os.access("/dev/fuse", os.R_OK | os.W_OK),
            "fusermount3_available": shutil.which("fusermount3") is not None,
        },
        "mounts": [
            {
                "mount_name": Path(record.mountpoint).name,
                "source_name": record.source,
                "image_name": Path(record.image_path).name if record.image_path else None,
                "image_identity": _image_identity(record),
                "read_write": record.read_write,
                "registry_complete": record.image_path is not None,
                "options": record.options,
                "pid": record.pid,
            }
            for record in mounts
        ],
    }


__all__ = ["diagnostic_report"]
