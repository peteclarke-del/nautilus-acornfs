"""Read-only repair planning; this module never mutates an image."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from acornfs.core.validation import IntegrityFinding, IntegrityReport, validate_image_report


class RepairRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class RepairAction:
    action: str
    title: str
    description: str
    risk: RepairRisk
    automatic_candidate: bool
    requires_manual_decision: bool
    finding_codes: tuple[str, ...]
    paths: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RepairPlan:
    report: IntegrityReport
    actions: tuple[RepairAction, ...]

    @property
    def clean(self) -> bool:
        return not self.report.findings

    @property
    def application_supported(self) -> bool:
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "dry-run",
            "application_supported": self.application_supported,
            "clean": self.clean,
            "validation": self.report.as_dict(),
            "actions": [action.as_dict() for action in self.actions],
        }

    def format_text(self) -> str:
        if self.clean:
            return "No repair actions are needed; validation found no problems."
        finding_count = len(self.report.findings)
        action_count = len(self.actions)
        lines = [
            "Dry-run repair plan (the image was not modified):",
            f"Validation findings: {finding_count}; planned actions: {action_count}.",
        ]
        if not self.actions:
            lines.append("No repair action is proposed for informational findings only.")
        for index, action in enumerate(self.actions, 1):
            mode = "candidate" if action.automatic_candidate else "manual decision required"
            lines.append(f"{index}. {action.title} [{action.risk.value} risk; {mode}]")
            lines.append(f"   {action.description}")
            lines.append(f"   Findings: {', '.join(action.finding_codes)}")
            if action.paths:
                lines.append(f"   Paths: {', '.join(action.paths)}")
        lines.append("Applying repairs is intentionally unsupported in this release.")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _ActionTemplate:
    action: str
    title: str
    description: str
    risk: RepairRisk
    automatic_candidate: bool
    requires_manual_decision: bool


_GEOMETRY = _ActionTemplate(
    "restore_geometry",
    "Resolve DAT/DSC geometry mismatch",
    "Restore the correct pair or explicitly choose which trusted geometry and capacity is valid.",
    RepairRisk.HIGH,
    False,
    True,
)
_UNREADABLE = _ActionTemplate(
    "restore_unreadable_structure",
    "Restore unreadable ADFS structures",
    "The image cannot be parsed deeply enough for safe automatic reconstruction; "
    "use a known-good copy.",
    RepairRisk.HIGH,
    False,
    True,
)
_FREE_MAP = _ActionTemplate(
    "rebuild_free_space_map",
    "Rebuild free-space accounting from allocated extents",
    "Recalculate free ranges from the traversable catalogue and regenerate old-map checksums.",
    RepairRisk.HIGH,
    True,
    False,
)
_CATALOGUE = _ActionTemplate(
    "resolve_catalogue_extents",
    "Resolve conflicting catalogue extents",
    "Choose which overlapping or out-of-range catalogue entries own their recorded sectors.",
    RepairRisk.HIGH,
    False,
    True,
)
_DIRECTORY_LENGTH = _ActionTemplate(
    "normalise_directory_lengths",
    "Normalise directory entry lengths",
    "Set directory entry lengths to the detected on-disc directory-format size.",
    RepairRisk.LOW,
    True,
    False,
)
_EMPTY_FILE = _ActionTemplate(
    "clear_empty_file_extents",
    "Clear stale extents from empty files",
    "Set the start sector of zero-length files to zero without changing file contents.",
    RepairRisk.LOW,
    True,
    False,
)


def _template_for(finding: IntegrityFinding) -> _ActionTemplate | None:
    if finding.code.startswith("geometry.") and finding.code != "geometry.reserved_tail":
        return _GEOMETRY
    if finding.code in {
        "adfs.open_failed",
        "adfs.structure",
        "directory.root_unreadable",
        "directory.unreadable",
        "directory.cycle",
    }:
        return _UNREADABLE
    if finding.code in {
        "extent.free_empty",
        "extent.free_out_of_range",
        "extent.free_overlap",
        "extent.free_used_overlap",
        "extent.free_unreadable",
        "extent.unaccounted",
    }:
        return _FREE_MAP
    if finding.code in {"extent.used_out_of_range", "extent.used_overlap"}:
        return _CATALOGUE
    if finding.code == "directory.length_unusual":
        return _DIRECTORY_LENGTH
    if finding.code == "file.empty_has_extent":
        return _EMPTY_FILE
    return None


def plan_repairs(selected: str | Path) -> RepairPlan:
    """Validate an image and group its findings into deterministic dry-run actions."""

    report = validate_image_report(selected)
    grouped: dict[str, tuple[_ActionTemplate, set[str], set[str]]] = {}
    for finding in report.findings:
        template = _template_for(finding)
        if template is None:
            continue
        _template, codes, paths = grouped.setdefault(template.action, (template, set(), set()))
        codes.add(finding.code)
        if finding.path:
            paths.add(finding.path)
    actions = tuple(
        RepairAction(
            action=template.action,
            title=template.title,
            description=template.description,
            risk=template.risk,
            automatic_candidate=template.automatic_candidate,
            requires_manual_decision=template.requires_manual_decision,
            finding_codes=tuple(sorted(codes)),
            paths=tuple(sorted(paths)),
        )
        for template, codes, paths in sorted(grouped.values(), key=lambda item: item[0].action)
    )
    return RepairPlan(report=report, actions=actions)


__all__ = ["RepairAction", "RepairPlan", "RepairRisk", "plan_repairs"]
