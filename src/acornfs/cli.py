"""Command-line interface for AcornFS."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from acornfs.core import inspect_pair
from acornfs.errors import AcornFSError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acornfs", description="Inspect and mount Acorn images")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="validate basic image metadata")
    inspect_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    inspect_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    mount_parser = subparsers.add_parser("mount", help="mount an image read-only with FUSE 3")
    mount_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    mount_parser.add_argument("mountpoint", help="an existing empty directory")
    mount_parser.add_argument("--debug", action="store_true", help="enable FUSE debug logging")
    unmount_parser = subparsers.add_parser("unmount", help="unmount an AcornFS mount")
    unmount_parser.add_argument("mountpoint", help="the mounted directory")
    unmount_parser.add_argument(
        "--lazy", action="store_true", help="detach even when an application still holds it open"
    )
    status_parser = subparsers.add_parser("status", help="show AcornFS mount status")
    status_parser.add_argument("mountpoint", nargs="?", help="optionally limit output to one path")
    status_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _inspect(args: argparse.Namespace) -> int:
    result = inspect_pair(args.image)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        geometry = result["geometry"]
        print("Format: BeebSCSI DAT/DSC")
        print(f"DAT: {result['dat']}")
        print(f"DSC: {result['dsc']}")
        print(
            f"Geometry: {geometry['cylinders']} cylinders, {geometry['heads']} heads, "
            f"{geometry['capacity']} bytes"
        )
        for warning in result["warnings"]:
            print(f"Warning: {warning}")
    return 0


def _mount(args: argparse.Namespace) -> int:
    try:
        from acornfs.fuse_adapter.runner import mount_read_only
    except ImportError as exc:
        raise AcornFSError(
            "FUSE support is unavailable; install the 'fuse' package extra and FUSE 3 runtime."
        ) from exc
    print(f"Mounting {args.image} at {args.mountpoint} read-only; press Ctrl-C to stop.")
    mount_read_only(args.image, args.mountpoint, debug=args.debug)
    return 0


def _unmount(args: argparse.Namespace) -> int:
    target = Path(args.mountpoint).expanduser().resolve()
    command = ["fusermount3", "-u"]
    if args.lazy:
        command.append("-z")
    command.append(str(target))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or "fusermount3 failed"
        raise AcornFSError(f"Could not unmount {target}: {detail}")
    return 0


def _decode_mount_field(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n")


def _mounts() -> list[dict[str, str]]:
    mounts: list[dict[str, str]] = []
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AcornFSError(f"Cannot read mount status: {exc}") from exc
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        filesystem = fields[separator + 1]
        if filesystem != "fuse.acornfs":
            continue
        mounts.append(
            {
                "mountpoint": _decode_mount_field(fields[4]),
                "source": _decode_mount_field(fields[separator + 2]),
                "options": fields[5],
            }
        )
    return mounts


def _status(args: argparse.Namespace) -> int:
    mounts = _mounts()
    if args.mountpoint:
        target = str(Path(args.mountpoint).expanduser().resolve())
        mounts = [mount for mount in mounts if mount["mountpoint"] == target]
    if args.json:
        print(json.dumps(mounts, indent=2, sort_keys=True))
    elif mounts:
        for mount in mounts:
            print(f"{mount['source']} on {mount['mountpoint']} ({mount['options']})")
    else:
        print("No AcornFS mounts found.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {"inspect": _inspect, "mount": _mount, "unmount": _unmount, "status": _status}
    try:
        return handlers[args.command](args)
    except AcornFSError as exc:
        print(f"acornfs: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
