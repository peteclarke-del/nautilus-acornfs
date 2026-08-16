"""Safe creation and publication of paired BeebSCSI ADFS images."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from oaknut.filesystem import create_filesystem

from acornfs.core.beebscsi import MAX_ADFS_SECTORS, BeebSCSIPair, discover_pair
from acornfs.core.validation import validate_image_report
from acornfs.errors import AcornFSError
from acornfs.i18n import _
from acornfs.operations import ProgressCallback, report_progress

_SAFE_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class CreatedImage:
    pair: BeebSCSIPair
    capacity_bytes: int
    title: str


def _normalise_stem(name: str) -> str:
    candidate = name.strip() or "scsi0"
    path = Path(candidate)
    if path.name != candidate or candidate in {".", ".."}:
        raise AcornFSError(_("The image name must be a filename, not a path."))
    if path.suffix.casefold() in {".dat", ".dsc"}:
        candidate = path.stem
    if not _SAFE_STEM.fullmatch(candidate):
        raise AcornFSError(
            _(
                "The image name must contain 1–64 ASCII letters, digits, dots, dashes or "
                "underscores."
            )
        )
    return candidate


def _normalise_title(title: str) -> str:
    candidate = title.strip() or "BLANK"
    try:
        encoded = candidate.encode("ascii")
    except UnicodeEncodeError as exc:
        raise AcornFSError(_("The ADFS title must contain ASCII characters only.")) from exc
    if len(encoded) > 12 or any(value < 0x20 or value == 0x7F for value in encoded):
        raise AcornFSError(_("The ADFS title must contain 1–12 printable ASCII characters."))
    return candidate


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_beebscsi_image(
    directory: str | Path,
    *,
    name: str = "scsi0",
    title: str = "BLANK",
    capacity: str = "20MB",
    progress: ProgressCallback | None = None,
) -> CreatedImage:
    """Create, fully validate and publish a new DAT/DSC pair without overwriting files."""

    report_progress(progress, 0, _("Checking image settings…"))
    parent = Path(directory).expanduser().resolve()
    if not parent.is_dir():
        raise AcornFSError(
            _("The image destination is not a directory: {path}").format(path=parent)
        )
    stem = _normalise_stem(name)
    disc_title = _normalise_title(title)
    target_dat = parent / f"{stem}.dat"
    target_dsc = parent / f"{stem}.dsc"
    target_names = {target_dat.name.casefold(), target_dsc.name.casefold()}
    try:
        collisions = [
            child.name for child in parent.iterdir() if child.name.casefold() in target_names
        ]
    except OSError as exc:
        raise AcornFSError(
            _("Could not inspect the destination directory: {error}").format(error=exc)
        ) from exc
    if collisions:
        raise AcornFSError(
            _("Image creation would overwrite existing file: {name}").format(name=collisions[0])
        )

    filesystem = create_filesystem("adfs")
    try:
        geometry = filesystem.geometry_grammar().parse(f"capacity={capacity.strip() or '20MB'}")
    except Exception as exc:
        raise AcornFSError(_("Invalid BeebSCSI capacity: {error}").format(error=exc)) from exc
    if (
        geometry.num_sectors > MAX_ADFS_SECTORS
        or not 1 <= geometry.cylinders <= 0xFFFF
        or not 1 <= geometry.heads <= 16
        or geometry.sectors_per_track != 33
    ):
        raise AcornFSError(
            _("The requested capacity exceeds BeebSCSI's 21-bit ADFS, 16-head or DSC limits.")
        )

    token = uuid.uuid4().hex
    temporary_dat = parent / f".{stem}.{token}.dat"
    temporary_dsc = temporary_dat.with_suffix(".dsc")
    published: list[Path] = []
    try:
        report_progress(progress, 10, _("Creating empty old-format ADFS filesystem…"))
        try:
            filesystem.create(temporary_dat, geometry, title=disc_title)
        except Exception as exc:
            raise AcornFSError(
                _("Could not create the temporary ADFS filesystem: {error}").format(error=exc)
            ) from exc
        if not temporary_dsc.is_file():
            raise AcornFSError(_("The filesystem engine did not create a matching DSC descriptor."))
        report_progress(progress, 70, _("Validating the complete DAT/DSC pair…"))
        report = validate_image_report(temporary_dat)
        if report.fatal_findings:
            first = report.fatal_findings[0]
            raise AcornFSError(
                _("Created image failed validation: {code}: {message}").format(
                    code=first.code, message=first.message
                )
            )

        report_progress(progress, 90, _("Publishing the validated image pair…"))
        try:
            os.link(temporary_dat, target_dat)
            published.append(target_dat)
            os.link(temporary_dsc, target_dsc)
            published.append(target_dsc)
            _sync_directory(parent)
        except OSError as exc:
            for path in published:
                path.unlink(missing_ok=True)
            _sync_directory(parent)
            raise AcornFSError(
                _("Could not publish the image pair without overwriting: {error}").format(error=exc)
            ) from exc
    finally:
        temporary_dat.unlink(missing_ok=True)
        temporary_dsc.unlink(missing_ok=True)

    pair = discover_pair(target_dat)
    report_progress(progress, 100, _("BeebSCSI image created and verified"))
    return CreatedImage(pair=pair, capacity_bytes=target_dat.stat().st_size, title=disc_title)


__all__ = ["CreatedImage", "create_beebscsi_image"]
