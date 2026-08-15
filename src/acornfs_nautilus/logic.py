"""Pure selection logic for the Nautilus extension."""

from __future__ import annotations

from pathlib import Path

from acornfs.core import discover_pair
from acornfs.errors import PairDiscoveryError


def is_supported_image(path: str | Path) -> bool:
    try:
        discover_pair(path)
    except PairDiscoveryError:
        return False
    return True
