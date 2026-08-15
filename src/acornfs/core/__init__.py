"""Filesystem-independent Acorn image handling."""

from .beebscsi import BeebSCSIGeometry, BeebSCSIPair, discover_pair, inspect_pair
from .image import ImageNode, ReadOnlyImage, validate_image
from .validation import (
    FindingSeverity,
    IntegrityFinding,
    IntegrityReport,
    validate_image_report,
)

__all__ = [
    "BeebSCSIGeometry",
    "BeebSCSIPair",
    "ImageNode",
    "FindingSeverity",
    "IntegrityFinding",
    "IntegrityReport",
    "ReadOnlyImage",
    "discover_pair",
    "inspect_pair",
    "validate_image",
    "validate_image_report",
]
