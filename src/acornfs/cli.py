"""Command-line interface for AcornFS."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from acornfs.core import inspect_pair
from acornfs.errors import AcornFSError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acornfs", description="Inspect and mount Acorn images")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="validate basic image metadata")
    inspect_parser.add_argument("image", help="a BeebSCSI DAT or DSC file")
    inspect_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = inspect_pair(args.image)
    except AcornFSError as exc:
        print(f"acornfs: {exc}", file=sys.stderr)
        return 2

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


if __name__ == "__main__":
    raise SystemExit(main())
