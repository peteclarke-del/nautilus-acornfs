"""Repair planning and tightly controlled low-risk repair application."""

from __future__ import annotations

import json
import os
import pwd
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from acornfs.core.beebscsi import discover_pair
from acornfs.core.image import ReadOnlyImage
from acornfs.core.validation import IntegrityFinding, IntegrityReport, validate_image_report
from acornfs.errors import AcornFSError
from acornfs.i18n import N_, _
from acornfs.operations import ProgressCallback, report_progress

SUPPORTED_REPAIR_ACTIONS = frozenset(
    {"normalise_directory_lengths", "clear_empty_file_extents", "pad_reserved_tail"}
)


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
        return (
            bool(self.actions)
            and not self.report.fatal_findings
            and all(
                action.action in SUPPORTED_REPAIR_ACTIONS
                and action.risk is RepairRisk.LOW
                and action.automatic_candidate
                and not action.requires_manual_decision
                for action in self.actions
            )
        )

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
            return _("No repair actions are needed; validation found no problems.")
        finding_count = len(self.report.findings)
        action_count = len(self.actions)
        lines = [
            _("Dry-run repair plan (the image was not modified):"),
            _("Validation findings: {findings}; planned actions: {actions}.").format(
                findings=finding_count, actions=action_count
            ),
        ]
        if not self.actions:
            lines.append(_("No repair action is proposed for informational findings only."))
        for index, action in enumerate(self.actions, 1):
            mode = _("candidate") if action.automatic_candidate else _("manual decision required")
            risk = {
                RepairRisk.LOW: _("low"),
                RepairRisk.MEDIUM: _("medium"),
                RepairRisk.HIGH: _("high"),
            }[action.risk]
            lines.append(
                _("{index}. {title} [{risk} risk; {mode}]").format(
                    index=index, title=action.title, risk=risk, mode=mode
                )
            )
            lines.append(f"   {action.description}")
            lines.append(
                _("   Findings: {findings}").format(findings=", ".join(action.finding_codes))
            )
            if action.paths:
                lines.append(_("   Paths: {paths}").format(paths=", ".join(action.paths)))
        if self.application_supported:
            lines.append(
                _(
                    "This complete plan can be applied with 'acornfs repair IMAGE "
                    "--confirm DAT_FILENAME'."
                )
            )
        else:
            lines.append(_("This plan cannot be applied automatically; no changes are permitted."))
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RepairResult:
    audit_path: str
    actions: tuple[RepairAction, ...]
    report: IntegrityReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "completed",
            "audit": self.audit_path,
            "actions": [action.as_dict() for action in self.actions],
            "validation": self.report.as_dict(),
        }


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
    N_("Resolve DAT/DSC geometry mismatch"),
    N_(
        "Restore the correct pair or explicitly choose which trusted geometry and capacity "
        "is valid."
    ),
    RepairRisk.HIGH,
    False,
    True,
)
_RESERVED_TAIL = _ActionTemplate(
    "pad_reserved_tail",
    N_("Restore omitted reserved DAT tail"),
    N_("Extend the DAT to its DSC capacity with zero-filled sectors beyond the ADFS boundary."),
    RepairRisk.LOW,
    True,
    False,
)
_UNREADABLE = _ActionTemplate(
    "restore_unreadable_structure",
    N_("Restore unreadable ADFS structures"),
    N_(
        "The image cannot be parsed deeply enough for safe automatic reconstruction; "
        "use a known-good copy."
    ),
    RepairRisk.HIGH,
    False,
    True,
)
_FREE_MAP = _ActionTemplate(
    "rebuild_free_space_map",
    N_("Rebuild free-space accounting from allocated extents"),
    N_("Recalculate free ranges from the traversable catalogue and regenerate old-map checksums."),
    RepairRisk.HIGH,
    True,
    False,
)
_CATALOGUE = _ActionTemplate(
    "resolve_catalogue_extents",
    N_("Resolve conflicting catalogue extents"),
    N_("Choose which overlapping or out-of-range catalogue entries own their recorded sectors."),
    RepairRisk.HIGH,
    False,
    True,
)
_DIRECTORY_LENGTH = _ActionTemplate(
    "normalise_directory_lengths",
    N_("Normalise directory entry lengths"),
    N_("Set directory entry lengths to the detected on-disc directory-format size."),
    RepairRisk.LOW,
    True,
    False,
)
_EMPTY_FILE = _ActionTemplate(
    "clear_empty_file_extents",
    N_("Clear stale extents from empty files"),
    N_("Set the start sector of zero-length files to zero without changing file contents."),
    RepairRisk.LOW,
    True,
    False,
)


def _template_for(finding: IntegrityFinding) -> _ActionTemplate | None:
    if finding.code == "geometry.dat_missing_reserved_tail":
        return _RESERVED_TAIL
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


def plan_repairs_from_report(report: IntegrityReport) -> RepairPlan:
    """Group an existing validation report into deterministic dry-run actions."""

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
            title=_(template.title),
            description=_(template.description),
            risk=template.risk,
            automatic_candidate=template.automatic_candidate,
            requires_manual_decision=template.requires_manual_decision,
            finding_codes=tuple(sorted(codes)),
            paths=tuple(sorted(paths)),
        )
        for template, codes, paths in sorted(grouped.values(), key=lambda item: item[0].action)
    )
    return RepairPlan(report=report, actions=actions)


def plan_repairs(selected: str | Path) -> RepairPlan:
    """Validate an image and group its findings into deterministic dry-run actions."""

    return plan_repairs_from_report(validate_image_report(selected))


def audit_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        return Path(configured).expanduser() / "acornfs" / "repair-audits"
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ".local" / "state" / "acornfs" / "repair-audits"


def _write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def apply_repairs(
    selected: str | Path,
    *,
    confirmation: str,
    progress: ProgressCallback | None = None,
) -> RepairResult:
    """Apply a complete low-risk plan with confirmation, checkpointing and audit."""

    report_progress(progress, 0, _("Planning repair…"))
    pair = discover_pair(selected)
    if confirmation != pair.dat_path.name:
        raise AcornFSError(
            _("Repair confirmation must exactly match the DAT filename: {name}").format(
                name=pair.dat_path.name
            )
        )
    plan = plan_repairs(pair.dat_path)
    report_progress(progress, 10, _("Repair plan validated"))
    if plan.clean:
        raise AcornFSError(_("Validation found no problems, so there is nothing to repair."))
    if not plan.application_supported:
        raise AcornFSError(
            _(
                "The complete repair plan is not eligible for automatic application; "
                "no checkpoint or image change was made."
            )
        )

    audit_path = (
        audit_root() / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4()}.json"
    )
    payload: dict[str, Any] = {
        "audit_version": 1,
        "audit_id": audit_path.stem,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "planned",
        "dat": str(pair.dat_path),
        "dsc": str(pair.dsc_path),
        "confirmation": confirmation,
        "checkpoint_created": False,
        "checkpoint_retained": False,
        "plan": plan.as_dict(),
        "applied_actions": [],
        "post_validation": None,
    }
    try:
        _write_audit(audit_path, payload)
        report_progress(progress, 15, _("Repair audit created"))
    except OSError as exc:
        raise AcornFSError(
            _("Could not create the mandatory repair audit: {error}").format(error=exc)
        ) from exc

    image: ReadOnlyImage | None = None
    checkpoint_completed = False
    try:
        report_progress(progress, 20, _("Revalidating image before checkpoint creation…"))

        def checkpoint_progress(copied: int, total: int) -> None:
            fraction = copied / total if total else 1.0
            copied_mib = copied / (1024 * 1024)
            total_mib = total / (1024 * 1024)
            report_progress(
                progress,
                20 + int(fraction * 40),
                _("Creating recovery checkpoint… {copied:.1f} of {total:.1f} MiB").format(
                    copied=copied_mib, total=total_mib
                ),
            )

        image = ReadOnlyImage.open(
            pair.dat_path,
            writable=True,
            repair_mode=True,
            checkpoint_progress=checkpoint_progress,
        )
        report_progress(progress, 60, _("Recovery checkpoint ready"))
        payload["status"] = "applying"
        payload["checkpoint_created"] = True
        _write_audit(audit_path, payload)
        for index, action in enumerate(plan.actions, 1):
            action_percent = 60 + int((index - 1) * 15 / len(plan.actions))
            report_progress(
                progress,
                action_percent,
                _("Applying: {action}").format(action=action.title),
            )
            if action.action == "pad_reserved_tail":
                image.pad_reserved_tail()
            else:
                image.apply_catalogue_repair(action.action, action.paths)
            payload["applied_actions"].append(action.as_dict())
            _write_audit(audit_path, payload)

        report_progress(progress, 78, _("Verifying the complete repaired image…"))
        report = image.integrity_report()
        remaining = {
            finding.code
            for finding in report.findings
            if finding.code in {code for action in plan.actions for code in action.finding_codes}
        }
        if report.fatal_findings or remaining:
            detail = (
                _("fatal findings remain")
                if report.fatal_findings
                else ", ".join(sorted(remaining))
            )
            raise AcornFSError(_("Post-repair validation failed: {detail}.").format(detail=detail))
        payload["status"] = "verified"
        payload["post_validation"] = report.as_dict()
        _write_audit(audit_path, payload)
        report_progress(progress, 92, _("Repair verified; finalising checkpoint…"))
        image.close()
        checkpoint_completed = True
        image = None
        payload["status"] = "completed"
        payload["completed_at"] = datetime.now(UTC).isoformat()
        _write_audit(audit_path, payload)
        report_progress(progress, 100, _("Repair completed and verified"))
        return RepairResult(str(audit_path), plan.actions, report)
    except Exception as exc:
        if image is not None:
            with suppress(Exception):
                image.close(clean=False)
        payload["status"] = "failed"
        payload["failed_at"] = datetime.now(UTC).isoformat()
        payload["error"] = str(exc)
        payload["checkpoint_retained"] = bool(
            payload["checkpoint_created"] and not checkpoint_completed
        )
        with suppress(OSError):
            _write_audit(audit_path, payload)
        if isinstance(exc, AcornFSError):
            raise AcornFSError(
                _("{error} Audit: {audit}").format(error=exc, audit=audit_path)
            ) from exc
        if checkpoint_completed:
            raise AcornFSError(
                _(
                    "Repair completed and verified, but its audit could not be marked complete. "
                    "The verified audit was retained at {audit}: {error}"
                ).format(audit=audit_path, error=exc)
            ) from exc
        raise AcornFSError(
            _("Repair failed; the checkpoint was retained. Audit: {audit}: {error}").format(
                audit=audit_path, error=exc
            )
        ) from exc


__all__ = [
    "RepairAction",
    "RepairPlan",
    "RepairResult",
    "RepairRisk",
    "apply_repairs",
    "audit_root",
    "plan_repairs",
    "plan_repairs_from_report",
]
