#!/usr/bin/env python3
"""Coverage-guided target for the untrusted BeebSCSI descriptor parser."""

import sys
from contextlib import suppress

import atheris

with atheris.instrument_imports():
    from acornfs.core.beebscsi import parse_descriptor
    from acornfs.errors import DescriptorError


def test_one_input(data: bytes) -> None:
    for candidate in (data, data[:22].ljust(22, b"\0")):
        with suppress(DescriptorError):
            parse_descriptor(candidate)


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
