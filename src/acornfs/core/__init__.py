"""Filesystem-independent Acorn image handling."""

from .beebscsi import BeebSCSIGeometry, BeebSCSIPair, discover_pair, inspect_pair
from .create import CreatedImage, create_beebscsi_image
from .formats import ImageCapabilities, ResolvedImage, resolve_image
from .image import ImageNode, ReadOnlyImage, validate_image
from .properties import ImageProperties, read_image_properties
from .repair import (
    RepairAction,
    RepairPlan,
    RepairResult,
    RepairRisk,
    apply_repairs,
    plan_repairs,
    plan_repairs_from_report,
)
from .transfer import ExportedFile, ImportedFile, export_file, import_file
from .validation import (
    COMPATIBILITY_PROFILE_ID,
    COMPATIBILITY_PROFILE_VERSION,
    VALIDATION_REPORT_SCHEMA_VERSION,
    FindingSeverity,
    IntegrityFinding,
    IntegrityReport,
    validate_image_report,
)

__all__ = [
    "BeebSCSIGeometry",
    "BeebSCSIPair",
    "COMPATIBILITY_PROFILE_ID",
    "COMPATIBILITY_PROFILE_VERSION",
    "CreatedImage",
    "ImageNode",
    "ImageCapabilities",
    "ImageProperties",
    "FindingSeverity",
    "ExportedFile",
    "IntegrityFinding",
    "IntegrityReport",
    "ImportedFile",
    "ReadOnlyImage",
    "ResolvedImage",
    "RepairAction",
    "RepairPlan",
    "RepairResult",
    "RepairRisk",
    "VALIDATION_REPORT_SCHEMA_VERSION",
    "discover_pair",
    "export_file",
    "create_beebscsi_image",
    "apply_repairs",
    "inspect_pair",
    "import_file",
    "read_image_properties",
    "resolve_image",
    "plan_repairs",
    "plan_repairs_from_report",
    "validate_image",
    "validate_image_report",
]
