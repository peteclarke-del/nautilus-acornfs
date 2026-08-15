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
from acornfs.mounts import active_mounts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acornfs", description="Inspect and mount Acorn images")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="validate basic image metadata")
    inspect_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    inspect_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    mount_parser = subparsers.add_parser(
        "mount", help="mount an image with FUSE 3 (read-only by default)"
    )
    mount_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    mount_parser.add_argument("mountpoint", help="an existing empty directory")
    mount_parser.add_argument(
        "--read-write",
        action="store_true",
        help="enable experimental durable writes to existing files",
    )
    mount_parser.add_argument("--debug", action="store_true", help="enable FUSE debug logging")
    unmount_parser = subparsers.add_parser("unmount", help="unmount an AcornFS mount")
    unmount_parser.add_argument("mountpoint", help="the mounted directory")
    unmount_parser.add_argument(
        "--lazy", action="store_true", help="detach even when an application still holds it open"
    )
    status_parser = subparsers.add_parser("status", help="show AcornFS mount status")
    status_parser.add_argument("mountpoint", nargs="?", help="optionally limit output to one path")
    status_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    install_parser = subparsers.add_parser(
        "install-nautilus", help="install the per-user Nautilus context-menu extension"
    )
    install_parser.add_argument("--restart", action="store_true", help="restart Nautilus now")
    uninstall_parser = subparsers.add_parser(
        "uninstall-nautilus", help="remove the per-user Nautilus extension"
    )
    uninstall_parser.add_argument("--restart", action="store_true", help="restart Nautilus now")
    desktop_mount_parser = subparsers.add_parser("desktop-mount")
    desktop_mount_parser.add_argument("image")
    desktop_unmount_parser = subparsers.add_parser("desktop-unmount")
    desktop_unmount_parser.add_argument("mountpoint")
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
        from acornfs.fuse_adapter.runner import mount_image
    except ImportError as exc:
        raise AcornFSError(
            "FUSE support is unavailable; install the 'fuse' package extra and FUSE 3 runtime."
        ) from exc
    mode = "read-write" if args.read_write else "read-only"
    print(f"Mounting {args.image} at {args.mountpoint} {mode}; press Ctrl-C to stop.")
    mount_image(args.image, args.mountpoint, read_write=args.read_write, debug=args.debug)
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


def _status(args: argparse.Namespace) -> int:
    mounts = active_mounts()
    if args.mountpoint:
        target = str(Path(args.mountpoint).expanduser().resolve())
        mounts = [mount for mount in mounts if mount.mountpoint == target]
    if args.json:
        print(json.dumps([mount.as_dict() for mount in mounts], indent=2, sort_keys=True))
    elif mounts:
        for mount in mounts:
            print(f"{mount.source} on {mount.mountpoint} ({mount.options})")
    else:
        print("No AcornFS mounts found.")
    return 0


def _install_nautilus(args: argparse.Namespace) -> int:
    from acornfs.nautilus_install import install_extension

    target = install_extension(restart=args.restart)
    print(f"Installed Nautilus extension: {target}")
    if not args.restart:
        print("Restart Nautilus to load it: nautilus --quit")
    return 0


def _uninstall_nautilus(args: argparse.Namespace) -> int:
    from acornfs.nautilus_install import uninstall_extension

    target = uninstall_extension(restart=args.restart)
    print(f"Removed Nautilus extension: {target}")
    return 0


def _desktop_mount(args: argparse.Namespace) -> int:
    from acornfs.desktop import desktop_mount

    return desktop_mount(args.image)


def _desktop_unmount(args: argparse.Namespace) -> int:
    from acornfs.desktop import desktop_unmount

    return desktop_unmount(args.mountpoint)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "inspect": _inspect,
        "mount": _mount,
        "unmount": _unmount,
        "status": _status,
        "install-nautilus": _install_nautilus,
        "uninstall-nautilus": _uninstall_nautilus,
        "desktop-mount": _desktop_mount,
        "desktop-unmount": _desktop_unmount,
    }
    try:
        return handlers[args.command](args)
    except AcornFSError as exc:
        print(f"acornfs: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
