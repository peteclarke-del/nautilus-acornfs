"""Command-line interface for AcornFS."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from acornfs.core import (
    FindingSeverity,
    apply_repairs,
    inspect_pair,
    plan_repairs,
    validate_image_report,
)
from acornfs.errors import AcornFSError
from acornfs.mounts import active_mounts, mount_at, wait_for_mount_shutdown
from acornfs.recovery import pending_recovery, recover_image


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acornfs", description="Inspect and mount Acorn images")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="validate basic image metadata")
    inspect_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    inspect_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    validate_parser = subparsers.add_parser(
        "validate", help="validate the ADFS structure without modifying it"
    )
    validate_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    validate_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    repair_parser = subparsers.add_parser(
        "repair-plan", help="create a read-only dry-run repair plan"
    )
    repair_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    repair_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    apply_parser = subparsers.add_parser(
        "repair", help="apply a complete eligible low-risk repair plan"
    )
    apply_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    apply_parser.add_argument(
        "--confirm",
        required=True,
        metavar="DAT_FILENAME",
        help="explicitly confirm by entering the exact DAT filename",
    )
    apply_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    mount_parser = subparsers.add_parser(
        "mount", help="mount an image with FUSE 3 (read-only by default)"
    )
    mount_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    mount_parser.add_argument("mountpoint", help="an existing empty directory")
    mount_parser.add_argument(
        "--read-write",
        action="store_true",
        help="enable checkpointed file and directory writes",
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
    diagnostics_parser = subparsers.add_parser(
        "diagnostics", help="print privacy-safe support diagnostics"
    )
    diagnostics_parser.add_argument("--json", action="store_true", help="emit JSON")
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
    desktop_mount_parser.add_argument("--read-write", action="store_true")
    desktop_unmount_parser = subparsers.add_parser("desktop-unmount")
    desktop_unmount_parser.add_argument("mountpoint")
    desktop_recover_parser = subparsers.add_parser("desktop-recover")
    desktop_recover_parser.add_argument("image")
    desktop_repair_parser = subparsers.add_parser("desktop-repair")
    desktop_repair_parser.add_argument("image")
    desktop_validate_parser = subparsers.add_parser("desktop-validate")
    desktop_validate_parser.add_argument("image")
    recover_parser = subparsers.add_parser(
        "recover", help="inspect or resolve an interrupted writable session"
    )
    recover_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    recover_action = recover_parser.add_mutually_exclusive_group()
    recover_action.add_argument("--restore", action="store_true", help="restore the checkpoint")
    recover_action.add_argument(
        "--discard", action="store_true", help="keep the current image and delete the checkpoint"
    )
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
    if os.environ.get("ACORNFS_DESKTOP_MOUNT") == "1":
        target = Path(args.mountpoint).expanduser().resolve()
        with suppress(OSError):
            target.rmdir()
        with suppress(OSError):
            target.parent.rmdir()
    return 0


def _validate(args: argparse.Namespace) -> int:
    report = validate_image_report(args.image)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(report.format_text())
    return 1 if any(item.severity is not FindingSeverity.ADVICE for item in report.findings) else 0


def _repair_plan(args: argparse.Namespace) -> int:
    plan = plan_repairs(args.image)
    if args.json:
        print(json.dumps(plan.as_dict(), indent=2, sort_keys=True))
    else:
        print(plan.format_text())
    return 1 if plan.report.fatal_findings else 0


def _repair(args: argparse.Namespace) -> int:
    result = apply_repairs(args.image, confirmation=args.confirm)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"Applied {len(result.actions)} repair action(s).")
        print(result.report.format_text())
        print(f"Audit report: {result.audit_path}")
    return 0


def _unmount(args: argparse.Namespace) -> int:
    target = Path(args.mountpoint).expanduser().resolve()
    record = mount_at(target)
    if record is None:
        raise AcornFSError(f"No active AcornFS mount was found at {target}.")
    if record.read_write is None:
        raise AcornFSError(
            "The mount has no lifecycle identity record; its write mode and safe shutdown "
            "cannot be verified."
        )
    if args.lazy and record.read_write:
        raise AcornFSError(
            "Lazy unmount is allowed only for a registry-confirmed read-only AcornFS mount."
        )
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
    if record.read_write:
        if not wait_for_mount_shutdown(target):
            raise AcornFSError(
                "The mount detached but its daemon did not confirm final flush and validation."
            )
        if record.image_path is not None and pending_recovery(record.image_path) is not None:
            raise AcornFSError(
                "The mount detached but final validation left a recovery checkpoint pending."
            )
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


def _diagnostics(args: argparse.Namespace) -> int:
    from acornfs.diagnostics import diagnostic_report

    report = diagnostic_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        runtime = report["runtime"]
        fuse = report["fuse"]
        mounts = report["mounts"]
        print(
            f"AcornFS {runtime['acornfs']} on {runtime['platform']} {runtime['architecture']} "
            f"(Python {runtime['python']})"
        )
        print(
            f"FUSE device: {'accessible' if fuse['device_accessible'] else 'unavailable'}; "
            f"fusermount3: {'available' if fuse['fusermount3_available'] else 'unavailable'}"
        )
        print(f"Active mounts: {len(mounts)}")
        for mount in mounts:
            mode = "read-write" if mount["read_write"] else "read-only/unknown"
            print(f"- {mount['source_name']} as {mount['mount_name']} ({mode})")
        print(report["privacy"])
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

    return desktop_mount(args.image, read_write=args.read_write)


def _desktop_unmount(args: argparse.Namespace) -> int:
    from acornfs.desktop import desktop_unmount

    return desktop_unmount(args.mountpoint)


def _desktop_recover(args: argparse.Namespace) -> int:
    from acornfs.desktop import desktop_recover

    return desktop_recover(args.image)


def _desktop_repair(args: argparse.Namespace) -> int:
    from acornfs.desktop import desktop_repair

    return desktop_repair(args.image)


def _desktop_validate(args: argparse.Namespace) -> int:
    from acornfs.desktop import desktop_validate

    return desktop_validate(args.image)


def _recover(args: argparse.Namespace) -> int:
    print(recover_image(args.image, restore=args.restore, discard=args.discard))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "inspect": _inspect,
        "validate": _validate,
        "repair-plan": _repair_plan,
        "repair": _repair,
        "mount": _mount,
        "unmount": _unmount,
        "status": _status,
        "diagnostics": _diagnostics,
        "install-nautilus": _install_nautilus,
        "uninstall-nautilus": _uninstall_nautilus,
        "desktop-mount": _desktop_mount,
        "desktop-unmount": _desktop_unmount,
        "desktop-recover": _desktop_recover,
        "desktop-repair": _desktop_repair,
        "desktop-validate": _desktop_validate,
        "recover": _recover,
    }
    try:
        return handlers[args.command](args)
    except AcornFSError as exc:
        if args.command == "mount" and os.environ.get("ACORNFS_DESKTOP_MOUNT") == "1":
            from acornfs.desktop import notify_mount_failure

            notify_mount_failure(str(exc))
        print(f"acornfs: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
