"""Explicit host transfers that preserve portable Acorn metadata."""

from __future__ import annotations

import os
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path

from oaknut.file import AcornMeta
from oaknut.file.filename_encoding import parse_encoded_filename
from oaknut.file.inf import format_trad_inf_line, parse_inf_line

from acornfs.core.image import ImageNode, ReadOnlyImage
from acornfs.errors import AcornFSError

TRANSFER_CHUNK_BYTES = 1024 * 1024
MAX_INF_BYTES = 4096


@dataclass(frozen=True, slots=True)
class InfRecord:
    name: str | None
    metadata: AcornMeta
    length: int | None


@dataclass(frozen=True, slots=True)
class ExportedFile:
    data_path: Path
    sidecar_path: Path
    acorn_path: str


@dataclass(frozen=True, slots=True)
class ImportedFile:
    node: ImageNode
    source_path: Path
    metadata_source: str


def _hex_field(value: str) -> int:
    text = value.strip()
    if text.startswith("&"):
        text = text[1:]
    return int(text, 16)


def _normalised_hex(value: str) -> str:
    parsed = _hex_field(value)
    if not 0 <= parsed <= 0xFFFFFFFF:
        raise ValueError("INF hexadecimal fields must fit in 32 bits")
    return f"{parsed:X}"


def parse_inf_record(data: bytes | str) -> InfRecord:
    """Parse an Acorn File Forge/Oaknut-compatible INF record."""

    text = data.decode("latin-1", "replace") if isinstance(data, bytes) else data
    line = next((candidate.strip() for candidate in text.splitlines() if candidate.strip()), "")
    try:
        fields = shlex.split(line, comments=False, posix=True)
    except ValueError as exc:
        raise AcornFSError(f"Invalid INF quoting: {exc}") from exc
    if len(fields) < 3:
        raise AcornFSError("An INF sidecar must contain a name, load and execution address.")

    try:
        first_is_hex = True
        _hex_field(fields[0])
    except ValueError:
        first_is_hex = False
    fourth_is_length = False
    if len(fields) > 3:
        try:
            fourth_is_length = len(fields[3].removeprefix("&").removeprefix("0x")) == 8
            _hex_field(fields[3])
        except ValueError:
            fourth_is_length = False
    traditional = not first_is_hex or fourth_is_length or len(fields) == 3

    length: int | None = None
    name: str | None = None
    try:
        if traditional:
            name = fields[0]
            load = _normalised_hex(fields[1])
            execute = _normalised_hex(fields[2])
            attributes = fields[3:]
            if len(fields) > 3:
                try:
                    length = _hex_field(fields[3])
                    if not 0 <= length <= 0xFFFFFFFF:
                        raise AcornFSError("INF length must fit in 32 bits.")
                    attributes = fields[4:]
                except ValueError:
                    length = None
            normalised = " ".join(["FILE", load, execute, f"{length or 0:08X}", *attributes])
        else:
            normalised_fields = list(fields)
            for index in (1, 2, 3):
                normalised_fields[index] = _normalised_hex(normalised_fields[index])
            normalised = " ".join(normalised_fields)
    except (ValueError, IndexError) as exc:
        raise AcornFSError(f"Invalid INF metadata: {exc}") from exc

    parsed = parse_inf_line(normalised)
    if parsed is None:
        raise AcornFSError("The INF sidecar metadata is not recognised.")
    _source, metadata = parsed
    if metadata.access is not None and not 0 <= metadata.access <= 0xFF:
        raise AcornFSError("INF access metadata must fit in 8 bits.")
    return InfRecord(name=name, metadata=metadata, length=length)


def format_inf_record(node: ImageNode, metadata: AcornMeta) -> str:
    """Format the portable traditional INF dialect shared with Acorn File Forge."""

    name = node.acorn_path
    if any(character.isspace() for character in name):
        if '"' in name:
            raise AcornFSError(
                "The Acorn path contains both whitespace and a quote and cannot be represented "
                "unambiguously in a portable INF sidecar."
            )
        name = f'"{name}"'
    line = str(
        format_trad_inf_line(
            name,
            int(metadata.load_address or 0),
            int(metadata.exec_address or 0),
            node.size,
        )
    )
    if int(metadata.access or 0) & 8:
        line += " Locked"
    return line + "\n"


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sidecar_candidates(source: Path) -> list[Path]:
    wanted = f"{source.name}.inf".casefold()
    return [child for child in source.parent.iterdir() if child.name.casefold() == wanted]


def _read_stable_file(source: Path, maximum_bytes: int) -> bytes:
    """Read one unchanged host file without exceeding the image's free space."""

    with source.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if before.st_size > maximum_bytes:
            raise AcornFSError(
                f"Host file needs {before.st_size} bytes but the image has "
                f"{maximum_bytes} bytes free."
            )
        data = handle.read(maximum_bytes + 1)
        after = os.fstat(handle.fileno())
    if len(data) > maximum_bytes:
        raise AcornFSError("The host file grew beyond the image's available space while reading.")
    before_signature = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_signature = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_signature != after_signature or len(data) != before.st_size:
        raise AcornFSError("The host file changed while it was being read; import was cancelled.")
    return data


def _import_metadata(
    source: Path, *, sidecar: str | Path | None, ignore_sidecar: bool
) -> tuple[str, AcornMeta, int | None, str]:
    if sidecar is not None and ignore_sidecar:
        raise AcornFSError("Specify either --sidecar or --ignore-sidecar, not both.")
    sidecar_path: Path | None = None
    if sidecar is not None:
        sidecar_path = Path(sidecar).expanduser().resolve()
        if not sidecar_path.is_file():
            raise AcornFSError(f"INF sidecar does not exist or is not a file: {sidecar_path}")
    elif not ignore_sidecar:
        candidates = _sidecar_candidates(source)
        if len(candidates) > 1:
            raise AcornFSError(f"More than one case-insensitive INF sidecar matches {source.name}.")
        sidecar_path = candidates[0] if candidates else None

    if sidecar_path is not None:
        if sidecar_path.stat().st_size > MAX_INF_BYTES:
            raise AcornFSError(f"INF sidecar exceeds the {MAX_INF_BYTES}-byte safety limit.")
        record = parse_inf_record(sidecar_path.read_bytes())
        suggested = record.name.rsplit(".", 1)[-1] if record.name else source.name
        return suggested, record.metadata, record.length, f"INF sidecar {sidecar_path.name}"

    suggested, encoded = parse_encoded_filename(source.name)
    if encoded is not None:
        return suggested, encoded, None, "encoded host filename"
    return (
        source.name,
        AcornMeta(load_address=0, exec_address=0, access=0),
        None,
        "neutral defaults",
    )


def export_file(selected: str | Path, acorn_path: str, destination: str | Path) -> ExportedFile:
    """Export one image file and a matching INF without overwriting host files."""

    requested = Path(destination).expanduser()
    parent = requested.parent.resolve()
    target = parent / requested.name
    sidecar = target.with_name(f"{target.name}.inf")
    if not parent.is_dir():
        raise AcornFSError(f"Export destination directory does not exist: {parent}")
    wanted = {target.name.casefold(), sidecar.name.casefold()}
    collisions = [child for child in parent.iterdir() if child.name.casefold() in wanted]
    if collisions:
        raise AcornFSError(f"Export would overwrite an existing file: {collisions[0]}")

    token = uuid.uuid4().hex
    temporary_data = parent / f".{target.name}.{token}.data"
    temporary_inf = parent / f".{target.name}.{token}.inf"
    published: list[Path] = []
    try:
        with ReadOnlyImage.open(selected) as image:
            node = image.node_at_path(acorn_path)
            if node.is_dir:
                raise AcornFSError(f"Export currently accepts files, not directories: {acorn_path}")
            metadata = image.acorn_metadata(node.inode)
            with temporary_data.open("xb") as output:
                offset = 0
                while offset < node.size:
                    data = image.read(
                        node.inode,
                        offset,
                        min(TRANSFER_CHUNK_BYTES, node.size - offset),
                    )
                    if not data:
                        raise AcornFSError(
                            f"Image read ended at {offset} bytes; expected {node.size}."
                        )
                    output.write(data)
                    offset += len(data)
                output.flush()
                os.fsync(output.fileno())
            with temporary_inf.open("x", encoding="latin-1", newline="") as output:
                output.write(format_inf_record(node, metadata))
                output.flush()
                os.fsync(output.fileno())
        try:
            os.link(temporary_data, target)
            published.append(target)
            os.link(temporary_inf, sidecar)
            published.append(sidecar)
            _sync_directory(parent)
        except OSError:
            for path in published:
                path.unlink(missing_ok=True)
            _sync_directory(parent)
            raise
    except AcornFSError:
        raise
    except Exception as exc:
        raise AcornFSError(f"Could not export {acorn_path}: {exc}") from exc
    finally:
        temporary_data.unlink(missing_ok=True)
        temporary_inf.unlink(missing_ok=True)
    return ExportedFile(data_path=target, sidecar_path=sidecar, acorn_path=node.acorn_path)


def import_file(
    selected: str | Path,
    source_file: str | Path,
    *,
    directory: str = "$",
    name: str | None = None,
    sidecar: str | Path | None = None,
    ignore_sidecar: bool = False,
) -> ImportedFile:
    """Import one host file and trusted metadata as one image mutation."""

    source = Path(source_file).expanduser().resolve()
    if not source.is_file():
        raise AcornFSError(f"Import source does not exist or is not a file: {source}")
    try:
        suggested, metadata, recorded_length, metadata_source = _import_metadata(
            source, sidecar=sidecar, ignore_sidecar=ignore_sidecar
        )
        target_name = name or suggested
        with ReadOnlyImage.open(selected) as image:
            parent = image.node_at_path(directory)
            if not parent.is_dir:
                raise AcornFSError(f"Import destination is not a directory: {directory}")
            if image.lookup(parent.inode, target_name.encode("utf-8")) is not None:
                raise AcornFSError(f"Import destination already exists: {target_name}")
            maximum_bytes = image.free_bytes
        data = _read_stable_file(source, maximum_bytes)
        if recorded_length is not None and recorded_length != len(data):
            raise AcornFSError(
                f"INF length {recorded_length} does not match host file length {len(data)}."
            )
        with ReadOnlyImage.open(selected, writable=True) as image:
            parent = image.node_at_path(directory)
            if not parent.is_dir:
                raise AcornFSError(f"Import destination is not a directory: {directory}")
            node = image.import_file(parent.inode, target_name.encode("utf-8"), data, metadata)
    except AcornFSError:
        raise
    except Exception as exc:
        raise AcornFSError(f"Could not import {source.name}: {exc}") from exc
    return ImportedFile(node=node, source_path=source, metadata_source=metadata_source)


__all__ = [
    "ExportedFile",
    "ImportedFile",
    "InfRecord",
    "export_file",
    "format_inf_record",
    "import_file",
    "parse_inf_record",
]
