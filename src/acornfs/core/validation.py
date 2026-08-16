"""Read-only integrity reporting for paired BeebSCSI old-format ADFS images."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from oaknut.filesystem import create_filesystem, geometry_from_dsc
from oaknut.filesystem.capabilities import Mount
from oaknut.filesystem.reader import ImageReader

from acornfs.core.beebscsi import (
    SECTOR_SIZE,
    BeebSCSIGeometry,
    BeebSCSIPair,
    discover_pair,
    open_locked_reader,
    parse_descriptor,
)
from acornfs.errors import AcornFSError, OperationCancelled
from acornfs.i18n import _, ngettext
from acornfs.operations import CancellationCheck, cancellation_point


class FindingSeverity(StrEnum):
    FATAL = "fatal"
    WARNING = "warning"
    ADVICE = "advice"


@dataclass(frozen=True, slots=True)
class IntegrityFinding:
    severity: FindingSeverity
    code: str
    message: str
    path: str | None = None

    @property
    def severity_label(self) -> str:
        """Return the translated human label without changing the stable enum value."""

        return {
            FindingSeverity.FATAL: _("FATAL"),
            FindingSeverity.WARNING: _("WARNING"),
            FindingSeverity.ADVICE: _("ADVICE"),
        }[self.severity]

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    dat_path: str
    dsc_path: str
    dat_bytes: int
    geometry_sectors: int | None
    adfs_sectors: int | None
    used_sectors: int | None
    free_sectors: int | None
    findings: tuple[IntegrityFinding, ...]

    @property
    def fatal_findings(self) -> tuple[IntegrityFinding, ...]:
        return tuple(item for item in self.findings if item.severity is FindingSeverity.FATAL)

    @property
    def warning_findings(self) -> tuple[IntegrityFinding, ...]:
        return tuple(item for item in self.findings if item.severity is FindingSeverity.WARNING)

    @property
    def advice_findings(self) -> tuple[IntegrityFinding, ...]:
        return tuple(item for item in self.findings if item.severity is FindingSeverity.ADVICE)

    @property
    def safe_for_write(self) -> bool:
        return not self.fatal_findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "dat": self.dat_path,
            "dsc": self.dsc_path,
            "dat_bytes": self.dat_bytes,
            "geometry_sectors": self.geometry_sectors,
            "adfs_sectors": self.adfs_sectors,
            "used_sectors": self.used_sectors,
            "free_sectors": self.free_sectors,
            "safe_for_write": self.safe_for_write,
            "summary": {
                "fatal": len(self.fatal_findings),
                "warning": len(self.warning_findings),
                "advice": len(self.advice_findings),
            },
            "findings": [item.as_dict() for item in self.findings],
        }

    def format_text(self) -> str:
        """Return the same complete, readable report for CLI and desktop UIs."""

        if not self.findings:
            return _("ADFS validation passed with no problems.")
        finding_label = ngettext("finding", "findings", len(self.findings))
        lines = [
            _(
                "Validation found {fatal} fatal, {warning} warning, and "
                "{advice} advice {finding_label}:"
            ).format(
                fatal=len(self.fatal_findings),
                warning=len(self.warning_findings),
                advice=len(self.advice_findings),
                finding_label=finding_label,
            )
        ]
        for finding in self.findings:
            location = f" {finding.path}" if finding.path else ""
            lines.append(
                _("- [{severity}] {code}{location}: {message}").format(
                    severity=finding.severity_label,
                    code=finding.code,
                    location=location,
                    message=finding.message,
                )
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _Extent:
    start: int
    end: int
    owner: str

    @property
    def length(self) -> int:
        return self.end - self.start


def _finding(
    severity: FindingSeverity, code: str, message: str, path: str | None = None
) -> IntegrityFinding:
    return IntegrityFinding(severity=severity, code=code, message=message, path=path)


def _overlap_findings(extents: list[_Extent], *, code: str, label: str) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    ordered = sorted(extents, key=lambda item: (item.start, item.end))
    if not ordered:
        return findings
    furthest = ordered[0]
    for current in ordered[1:]:
        if current.start < furthest.end:
            findings.append(
                _finding(
                    FindingSeverity.FATAL,
                    code,
                    _(
                        "{label} extents overlap in sectors {start}..{end}: "
                        "{first_owner} and {second_owner}."
                    ).format(
                        label=label,
                        start=current.start,
                        end=min(current.end, furthest.end) - 1,
                        first_owner=furthest.owner,
                        second_owner=current.owner,
                    ),
                    current.owner if current.owner.startswith("$") else None,
                )
            )
        if current.end > furthest.end:
            furthest = current
    return findings


def _cross_overlap_findings(used: list[_Extent], free: list[_Extent]) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    used_ordered = sorted(used, key=lambda item: item.start)
    free_ordered = sorted(free, key=lambda item: item.start)
    used_index = 0
    free_index = 0
    while used_index < len(used_ordered) and free_index < len(free_ordered):
        used_extent = used_ordered[used_index]
        free_extent = free_ordered[free_index]
        start = max(used_extent.start, free_extent.start)
        end = min(used_extent.end, free_extent.end)
        if start < end:
            findings.append(
                _finding(
                    FindingSeverity.FATAL,
                    "extent.free_used_overlap",
                    _(
                        "Sectors {start}..{end} are both allocated to {owner} and marked free."
                    ).format(start=start, end=end - 1, owner=used_extent.owner),
                    used_extent.owner if used_extent.owner.startswith("$") else None,
                )
            )
        if used_extent.end <= free_extent.end:
            used_index += 1
        else:
            free_index += 1
    return findings


def _gap_ranges(extents: list[_Extent], total_sectors: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for extent in sorted(extents, key=lambda item: item.start):
        start = max(0, extent.start)
        end = min(total_sectors, extent.end)
        if start > cursor:
            ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total_sectors:
        ranges.append((cursor, total_sectors))
    return ranges


def _geometry_findings(
    pair: BeebSCSIPair, descriptor_geometry: BeebSCSIGeometry
) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    dat_bytes = pair.dat_path.stat().st_size
    if dat_bytes % SECTOR_SIZE:
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "geometry.dat_unaligned",
                _("DAT length {length} is not a multiple of {sector_size} bytes.").format(
                    length=dat_bytes, sector_size=SECTOR_SIZE
                ),
            )
        )
    if dat_bytes < descriptor_geometry.capacity:
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "geometry.dat_short",
                _("DAT length {length} is shorter than DSC capacity {capacity}.").format(
                    length=dat_bytes, capacity=descriptor_geometry.capacity
                ),
            )
        )
    elif dat_bytes > descriptor_geometry.capacity:
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "geometry.dat_oversized",
                _("DAT length {length} exceeds DSC capacity {capacity}.").format(
                    length=dat_bytes, capacity=descriptor_geometry.capacity
                ),
            )
        )
    return findings


def validate_open_mount(
    pair: BeebSCSIPair,
    mount: Mount,
    descriptor_geometry: BeebSCSIGeometry,
    *,
    initial_findings: tuple[IntegrityFinding, ...] = (),
    cancelled: CancellationCheck | None = None,
) -> IntegrityReport:
    """Validate geometry, directory structures, and used/free extent accounting."""

    cancellation_point(cancelled)
    findings = [*initial_findings, *_geometry_findings(pair, descriptor_geometry)]
    dat_bytes = pair.dat_path.stat().st_size
    geometry_sectors = descriptor_geometry.sectors

    adfs = cast(Any, mount)._adfs
    fsm = adfs._fsm
    adfs_sectors = int(fsm.total_sectors)
    dat_sectors = dat_bytes // SECTOR_SIZE
    if (
        dat_bytes < descriptor_geometry.capacity
        and dat_bytes % SECTOR_SIZE == 0
        and dat_sectors == adfs_sectors < geometry_sectors
    ):
        findings = [finding for finding in findings if finding.code != "geometry.dat_short"]
        findings.append(
            _finding(
                FindingSeverity.WARNING,
                "geometry.dat_missing_reserved_tail",
                ngettext(
                    "DAT ends exactly at the ADFS boundary and omits {count} reserved DSC "
                    "sector; the missing tail can be restored without changing ADFS data.",
                    "DAT ends exactly at the ADFS boundary and omits {count} reserved DSC "
                    "sectors; the missing tail can be restored without changing ADFS data.",
                    geometry_sectors - adfs_sectors,
                ).format(count=geometry_sectors - adfs_sectors),
            )
        )
    if adfs_sectors > dat_sectors:
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "geometry.map_exceeds_dat",
                _("ADFS claims {adfs} sectors but the DAT contains {dat}.").format(
                    adfs=adfs_sectors, dat=dat_sectors
                ),
            )
        )
    if adfs_sectors > geometry_sectors:
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "geometry.map_exceeds_dsc",
                _("ADFS claims {adfs} sectors but the DSC allows {dsc}.").format(
                    adfs=adfs_sectors, dsc=geometry_sectors
                ),
            )
        )
    elif adfs_sectors < geometry_sectors:
        findings.append(
            _finding(
                FindingSeverity.ADVICE,
                "geometry.reserved_tail",
                _(
                    "ADFS occupies {adfs} of {dsc} DSC sectors; the remaining tail may host "
                    "another filesystem."
                ).format(adfs=adfs_sectors, dsc=geometry_sectors),
            )
        )

    validator = getattr(mount, "validate", None)
    if callable(validator):
        for problem in validator():
            cancellation_point(cancelled)
            findings.append(
                _finding(
                    FindingSeverity.FATAL,
                    "adfs.structure",
                    _("ADFS structure error: {error}").format(error=problem),
                )
            )

    free_extents: list[_Extent] = []
    try:
        for index, (start_bytes, length_bytes) in enumerate(fsm.free_space_entries()):
            cancellation_point(cancelled)
            start = int(start_bytes) // SECTOR_SIZE
            length = int(length_bytes) // SECTOR_SIZE
            if length <= 0:
                findings.append(
                    _finding(
                        FindingSeverity.FATAL,
                        "extent.free_empty",
                        _("Free-space entry {index} has zero length.").format(index=index),
                    )
                )
                continue
            extent = _Extent(
                start,
                start + length,
                _("free-space entry {index}").format(index=index),
            )
            free_extents.append(extent)
            if extent.start < 7 or extent.end > adfs_sectors:
                findings.append(
                    _finding(
                        FindingSeverity.FATAL,
                        "extent.free_out_of_range",
                        _(
                            "Free-space entry {index} covers sectors {start}..{end}, outside "
                            "allocatable ADFS sectors 7..{adfs_end}."
                        ).format(
                            index=index,
                            start=extent.start,
                            end=extent.end - 1,
                            adfs_end=adfs_sectors - 1,
                        ),
                    )
                )
    except OperationCancelled:
        raise
    except Exception as exc:
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "extent.free_unreadable",
                _("The free-space extent list could not be read: {error}").format(error=exc),
            )
        )

    used_extents = [_Extent(0, 2, _("ADFS free-space map")), _Extent(2, 7, "$")]
    seen_directories: set[int] = {2}
    directory_sectors = int(adfs._dir_format.size_in_sectors)
    directory_bytes = int(adfs._dir_format.size_in_bytes)

    def walk(directory: Any, path: str) -> None:
        for entry in directory.entries:
            cancellation_point(cancelled)
            child_path = f"{path}.{entry.name}"
            if entry.is_directory:
                start = int(entry.start_sector)
                extent = _Extent(start, start + directory_sectors, child_path)
                used_extents.append(extent)
                if int(entry.length) != directory_bytes:
                    findings.append(
                        _finding(
                            FindingSeverity.WARNING,
                            "directory.length_unusual",
                            _("Directory entry length is {length}; expected {expected}.").format(
                                length=entry.length, expected=directory_bytes
                            ),
                            child_path,
                        )
                    )
                if start in seen_directories:
                    findings.append(
                        _finding(
                            FindingSeverity.FATAL,
                            "directory.cycle",
                            _(
                                "Directory reuses sector {sector}, creating a cycle or alias."
                            ).format(sector=start),
                            child_path,
                        )
                    )
                    continue
                seen_directories.add(start)
                try:
                    child = adfs._read_directory_at(start)
                except Exception as exc:
                    findings.append(
                        _finding(
                            FindingSeverity.FATAL,
                            "directory.unreadable",
                            _("Directory at sector {sector} cannot be read: {error}").format(
                                sector=start, error=exc
                            ),
                            child_path,
                        )
                    )
                    continue
                walk(child, child_path)
            else:
                length = int(entry.length)
                sectors = (length + SECTOR_SIZE - 1) // SECTOR_SIZE
                start = int(entry.start_sector)
                if sectors:
                    used_extents.append(_Extent(start, start + sectors, child_path))
                elif start != 0:
                    findings.append(
                        _finding(
                            FindingSeverity.WARNING,
                            "file.empty_has_extent",
                            _("Empty file records non-zero start sector {sector}.").format(
                                sector=start
                            ),
                            child_path,
                        )
                    )

    try:
        root = adfs._read_root_directory()
        walk(root, "$")
    except OperationCancelled:
        raise
    except Exception as exc:
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "directory.root_unreadable",
                _("The root directory cannot be traversed: {error}").format(error=exc),
                "$",
            )
        )

    for extent in used_extents:
        cancellation_point(cancelled)
        if extent.start < 0 or extent.end > adfs_sectors or extent.start >= extent.end:
            findings.append(
                _finding(
                    FindingSeverity.FATAL,
                    "extent.used_out_of_range",
                    _(
                        "Allocated extent covers sectors {start}..{end}, outside ADFS sectors "
                        "0..{adfs_end}."
                    ).format(
                        start=extent.start,
                        end=extent.end - 1,
                        adfs_end=adfs_sectors - 1,
                    ),
                    extent.owner if extent.owner.startswith("$") else None,
                )
            )

    cancellation_point(cancelled)
    findings.extend(
        _overlap_findings(used_extents, code="extent.used_overlap", label=_("Allocated"))
    )
    findings.extend(_overlap_findings(free_extents, code="extent.free_overlap", label=_("Free")))
    findings.extend(_cross_overlap_findings(used_extents, free_extents))

    gaps = _gap_ranges([*used_extents, *free_extents], adfs_sectors)
    if gaps:
        preview = ", ".join(f"{start}..{end - 1}" for start, end in gaps[:4])
        suffix = "…" if len(gaps) > 4 else ""
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "extent.unaccounted",
                ngettext(
                    "{count} ADFS sector range is neither allocated nor free: {ranges}{suffix}.",
                    "{count} ADFS sector ranges are neither allocated nor free: {ranges}{suffix}.",
                    len(gaps),
                ).format(count=len(gaps), ranges=preview, suffix=suffix),
            )
        )

    used_sectors = sum(extent.length for extent in used_extents)
    free_sectors = sum(extent.length for extent in free_extents)
    return IntegrityReport(
        dat_path=str(pair.dat_path),
        dsc_path=str(pair.dsc_path),
        dat_bytes=dat_bytes,
        geometry_sectors=geometry_sectors,
        adfs_sectors=adfs_sectors,
        used_sectors=used_sectors,
        free_sectors=free_sectors,
        findings=tuple(findings),
    )


def validate_image_report(
    selected: str | Path, *, cancelled: CancellationCheck | None = None
) -> IntegrityReport:
    """Open and validate one pair read-only, returning fatal findings where possible."""

    cancellation_point(cancelled)
    pair = discover_pair(selected)
    dat_bytes = pair.dat_path.stat().st_size
    try:
        descriptor = pair.dsc_path.read_bytes()
        descriptor_geometry = parse_descriptor(descriptor)
    except Exception as exc:
        return IntegrityReport(
            dat_path=str(pair.dat_path),
            dsc_path=str(pair.dsc_path),
            dat_bytes=dat_bytes,
            geometry_sectors=None,
            adfs_sectors=None,
            used_sectors=None,
            free_sectors=None,
            findings=(
                _finding(
                    FindingSeverity.FATAL,
                    "geometry.descriptor_invalid",
                    _("The DSC descriptor is invalid: {error}").format(error=exc),
                ),
            ),
        )

    reader: ImageReader | None = None
    mount: Mount | None = None
    closeables: tuple[Any, ...] = ()
    try:
        geometry = geometry_from_dsc(descriptor)
        reader, closeables = open_locked_reader(pair, writable=False)
        mount = create_filesystem("adfs").open(reader, geometry)
        return validate_open_mount(pair, mount, descriptor_geometry, cancelled=cancelled)
    except OperationCancelled:
        raise
    except Exception as exc:
        findings = _geometry_findings(pair, descriptor_geometry)
        findings.append(
            _finding(
                FindingSeverity.FATAL,
                "adfs.open_failed",
                _("The ADFS image could not be opened for validation: {error}").format(error=exc),
            )
        )
        return IntegrityReport(
            dat_path=str(pair.dat_path),
            dsc_path=str(pair.dsc_path),
            dat_bytes=dat_bytes,
            geometry_sectors=descriptor_geometry.sectors,
            adfs_sectors=None,
            used_sectors=None,
            free_sectors=None,
            findings=tuple(findings),
        )
    finally:
        if mount is not None:
            adfs = getattr(mount, "_adfs", None)
            close = getattr(adfs, "close", None)
            if callable(close):
                close()
        if reader is not None:
            reader.close()
        for closeable in closeables:
            with suppress(Exception):
                closeable.close()


def require_safe_for_write(report: IntegrityReport) -> None:
    """Reject a writable mount with a concise summary of fatal integrity findings."""

    if report.safe_for_write:
        return
    first = report.fatal_findings[0]
    count = len(report.fatal_findings)
    raise AcornFSError(
        ngettext(
            "Writable mount refused: validation found {count} fatal problem. {code}: {message}",
            "Writable mount refused: validation found {count} fatal problems. {code}: {message}",
            count,
        ).format(count=count, code=first.code, message=first.message)
    )


__all__ = [
    "FindingSeverity",
    "IntegrityFinding",
    "IntegrityReport",
    "require_safe_for_write",
    "validate_image_report",
    "validate_open_mount",
]
