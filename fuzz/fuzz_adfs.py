#!/usr/bin/env python3
"""Coverage-guided target for ADFS old-map and catalogue validation."""

import sys
import tempfile
from pathlib import Path

import atheris

with atheris.instrument_imports():
    from acornfs.core.validation import validate_image_report

CAPACITY = 33 * 256
_TEMPORARY = tempfile.TemporaryDirectory(prefix="acornfs-fuzz-")
_ROOT = Path(_TEMPORARY.name)
_DAT = _ROOT / "fuzz.dat"
_DSC = _ROOT / "fuzz.dsc"
_DESCRIPTOR = bytearray(22)
_DESCRIPTOR[13:15] = (1).to_bytes(2, "big")
_DESCRIPTOR[15] = 1
_DSC.write_bytes(_DESCRIPTOR)


def test_one_input(data: bytes) -> None:
    image = data[:CAPACITY].ljust(CAPACITY, b"\0")
    _DAT.write_bytes(image)
    validate_image_report(_DAT)


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
