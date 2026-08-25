"""Safe HFE v1/v3 conversion for sector-backed AcornFS mounts."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from acornfs.core.storage import open_locked_handle
from acornfs.errors import AcornFSError, UnsupportedImageError
from acornfs.i18n import _

HFE_V1_SIGNATURE = b"HXCPICFE"
HFE_V3_SIGNATURE = b"HXCHFEV3"
HFE_SIGNATURES = frozenset({HFE_V1_SIGNATURE, HFE_V3_SIGNATURE})
CONVERSION_TIMEOUT = 5 * 60.0
MAX_HFE_BYTES = 64 * 1024 * 1024
_SECTOR_RESULT = re.compile(
    r"Found\s+(?P<found>\d+)\s+sectors\s+of\s+(?P<total>\d+)\s+\((?P<percent>\d+)%\)"
)


@dataclass(frozen=True, slots=True)
class HFEFormat:
    """One exact Acorn sector layout accepted inside an HFE container."""

    greaseweazle_name: str
    suffix: str
    size: int
    tracks: int
    sides: int
    sectors: int


# Larger layouts come first. Greaseweazle can otherwise decode a valid prefix
# of an 80-track image using a 40-track definition.
HFE_FORMATS = (
    HFEFormat("acorn.adfs.1600", ".adf", 1600 * 1024, 80, 2, 1600),
    HFEFormat("acorn.adfs.800", ".adf", 800 * 1024, 80, 2, 800),
    HFEFormat("acorn.adfs.640", ".adl", 640 * 1024, 80, 2, 2560),
    HFEFormat("acorn.adfs.320", ".adm", 320 * 1024, 80, 1, 1280),
    HFEFormat("acorn.adfs.160", ".ads", 160 * 1024, 40, 1, 640),
    HFEFormat("acorn.dfs.ds80", ".dsd", 400 * 1024, 80, 2, 1600),
    HFEFormat("acorn.dfs.ds", ".dsd", 200 * 1024, 40, 2, 800),
    HFEFormat("acorn.dfs.ss80", ".ssd", 200 * 1024, 80, 1, 800),
    HFEFormat("acorn.dfs.ss", ".ssd", 100 * 1024, 40, 1, 400),
)


def _environment() -> dict[str, str]:
    return {
        name: value
        for name in ("HOME", "LANG", "LC_ALL", "PATH")
        if (value := os.environ.get(name)) is not None
    }


def hfe_version(path: str | Path) -> int | None:
    """Return the HFE container version identified from its file header."""

    try:
        with Path(path).open("rb") as handle:
            signature = handle.read(8)
    except OSError:
        return None
    if signature == HFE_V1_SIGNATURE:
        return 1
    if signature == HFE_V3_SIGNATURE:
        return 3
    return None


def _hfe_geometry(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            header = handle.read(11)
    except OSError:
        return None
    if len(header) != 11 or header[:8] not in HFE_SIGNATURES:
        return None
    tracks, sides = header[9], header[10]
    return (tracks, sides) if tracks and sides in {1, 2} else None


def _mfm_sector_ids(path: Path) -> int:
    """Count IBM MFM ID address marks on the first HFE track.

    HFE stores each side in alternating 256-byte blocks. The byte patterns are
    bit-reversed on disk, hence ``22 91`` for the missing-clock A1 sync word and
    ``aa 2a`` for the FE ID mark. This lightweight probe avoids several costly
    full-disc conversions merely to distinguish Acorn ADFS sector sizes.
    """

    try:
        with path.open("rb") as handle:
            handle.seek(18)
            lookup = int.from_bytes(handle.read(2), "little") * 512
            if lookup < 512:
                return 0
            handle.seek(lookup)
            track_offset = int.from_bytes(handle.read(2), "little") * 512
            track_length = int.from_bytes(handle.read(2), "little")
            if track_offset < 512 or not 0 < track_length <= 1024 * 1024:
                return 0
            handle.seek(track_offset)
            interleaved = handle.read(track_length)
            if len(interleaved) != track_length:
                return 0
        side_zero = b"".join(
            interleaved[offset : offset + 256] for offset in range(0, len(interleaved), 512)
        )
    except (OSError, ValueError):
        return 0
    id_mark = bytes.fromhex("229122912291aa2a")
    return side_zero.count(id_mark)


def is_hfe(path: str | Path) -> bool:
    """Return whether a regular file has a supported HFE v1/v3 signature."""

    return hfe_version(path) is not None


def _run_convert(command: str, source: Path, destination: Path, format_name: str) -> str:
    try:
        result = subprocess.run(
            [command, "convert", f"--format={format_name}", str(source), str(destination)],
            check=False,
            capture_output=True,
            text=True,
            env=_environment(),
            timeout=CONVERSION_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise AcornFSError(_("Greaseweazle timed out while converting the HFE image.")) from exc
    except OSError as exc:
        raise AcornFSError(
            _("Could not start Greaseweazle for HFE conversion: {error}").format(error=exc)
        ) from exc
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise AcornFSError(_("Greaseweazle rejected the HFE conversion."))
    return output


class HFEWorkspace:
    """Private decoded image plus enough state for atomic HFE write-back."""

    def __init__(
        self,
        temporary: tempfile.TemporaryDirectory[str],
        raw_path: Path,
        format: HFEFormat,
        version: int,
        source_signature: tuple[int, int, int, int, int],
        command: str,
    ) -> None:
        self._temporary = temporary
        self.raw_path = raw_path
        self.format = format
        self.version = version
        self.source_signature = source_signature
        self.command = command
        self._closed = False

    def export(self, destination: Path) -> None:
        """Re-encode the decoded sectors and atomically replace the HFE file."""

        before = destination.stat(follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise AcornFSError(_("The HFE destination is no longer a regular file."))
        encoded = destination.parent / f".{destination.stem}.acornfs-{os.getpid()}.hfe"
        encoded.unlink(missing_ok=True)
        output_path = f"{encoded}::version=3" if self.version == 3 else str(encoded)
        try:
            _run_convert(
                self.command,
                self.raw_path,
                Path(output_path),
                self.format.greaseweazle_name,
            )
            if not encoded.is_file() or encoded.stat().st_size == 0:
                raise AcornFSError(_("Greaseweazle did not produce a replacement HFE image."))
            os.chmod(encoded, stat.S_IMODE(before.st_mode))
            encoded_stat = encoded.stat(follow_symlinks=False)
            if (encoded_stat.st_uid, encoded_stat.st_gid) != (before.st_uid, before.st_gid):
                try:
                    os.chown(encoded, before.st_uid, before.st_gid)
                except PermissionError as exc:
                    raise AcornFSError(
                        _("The replacement HFE could not retain the source file ownership.")
                    ) from exc
            try:
                for name in os.listxattr(destination, follow_symlinks=False):
                    value = os.getxattr(destination, name, follow_symlinks=False)
                    os.setxattr(encoded, name, value, follow_symlinks=False)
            except OSError as exc:
                raise AcornFSError(
                    _("The replacement HFE could not retain the source file attributes.")
                ) from exc
            with encoded.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(encoded, destination)
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            encoded.unlink(missing_ok=True)

    def close(self) -> None:
        if not self._closed:
            self._temporary.cleanup()
            self._closed = True

    def __del__(self) -> None:
        self.close()


def decode_hfe(path: str | Path) -> HFEWorkspace:
    """Decode a complete standard Acorn HFE image into a private raw image.

    Images with missing sectors or a nonstandard/copy-protected track layout are
    intentionally refused for mounting because sector write-back would flatten
    information that AcornFS cannot represent. They remain eligible for a native
    Greaseweazle physical write.
    """

    source = Path(path).expanduser().resolve(strict=True)
    version = hfe_version(source)
    if version is None:
        raise UnsupportedImageError(_("The file does not contain an HFE v1 or HFEv3 image."))
    command = shutil.which("gw")
    if command is None:
        raise UnsupportedImageError(_("HFE mounting requires the Greaseweazle host tools ('gw')."))
    source_handle = open_locked_handle(source, writable=False)
    source_stat = os.fstat(source_handle.fileno())
    signature = (
        source_stat.st_dev,
        source_stat.st_ino,
        source_stat.st_size,
        source_stat.st_mtime_ns,
        source_stat.st_ctime_ns,
    )
    temporary = tempfile.TemporaryDirectory(prefix="acornfs-hfe-")
    root = Path(temporary.name)
    stable_source = root / "source.hfe"
    try:
        if source_stat.st_size > MAX_HFE_BYTES:
            raise UnsupportedImageError(
                _("The HFE container exceeds the supported 64 MiB safety limit.")
            )
        with stable_source.open("xb") as target:
            source_handle.seek(0)
            shutil.copyfileobj(source_handle, target, length=1024 * 1024)
        after = os.fstat(source_handle.fileno())
        current = source.stat(follow_symlinks=False)
        current_signature = (
            current.st_dev,
            current.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if current_signature != signature or stable_source.stat().st_size != source_stat.st_size:
            raise AcornFSError(_("The HFE image changed while AcornFS was preparing it."))
    except BaseException:
        temporary.cleanup()
        raise
    finally:
        source_handle.close()
    failures: list[Exception] = []
    geometry = _hfe_geometry(stable_source)
    candidates = tuple(
        format_info
        for format_info in HFE_FORMATS
        if geometry is None or (format_info.tracks, format_info.sides) == geometry
    )
    mfm_sectors = _mfm_sector_ids(stable_source)
    if mfm_sectors:
        candidates = tuple(
            sorted(
                candidates,
                key=lambda item: item.sectors // (item.tracks * item.sides) != mfm_sectors,
            )
        )
    else:
        candidates = tuple(
            sorted(candidates, key=lambda item: not item.greaseweazle_name.startswith("acorn.dfs"))
        )
    index = 0
    while index < len(candidates):
        format_info = candidates[index]
        raw = root / f"image-{index}{format_info.suffix}"
        try:
            output = _run_convert(command, stable_source, raw, format_info.greaseweazle_name)
            matches = tuple(_SECTOR_RESULT.finditer(output))
            complete = bool(matches) and matches[-1]["percent"] == "100"
            if complete and raw.is_file() and raw.stat().st_size == format_info.size:
                return HFEWorkspace(temporary, raw, format_info, version, signature, command)
            if matches:
                found = int(matches[-1]["found"])
                inferred = next(
                    (
                        candidate
                        for candidate in candidates[index + 1 :]
                        if candidate.sectors == found
                    ),
                    None,
                )
                if inferred is not None:
                    index = candidates.index(inferred)
                    raw.unlink(missing_ok=True)
                    continue
        except Exception as exc:
            failures.append(exc)
        raw.unlink(missing_ok=True)
        index += 1
    temporary.cleanup()
    if failures and all(isinstance(exc, AcornFSError) for exc in failures):
        detail = str(failures[-1])
    else:
        detail = _("no complete standard Acorn sector layout matched")
    raise UnsupportedImageError(
        _(
            "The HFE image cannot be mounted without losing track-level data: {detail}. "
            "It can still be written directly to a physical floppy."
        ).format(detail=detail)
    )


__all__ = [
    "HFE_FORMATS",
    "HFE_SIGNATURES",
    "HFEFormat",
    "HFEWorkspace",
    "decode_hfe",
    "hfe_version",
    "is_hfe",
]
