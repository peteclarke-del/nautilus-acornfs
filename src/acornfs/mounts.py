"""Discovery of active AcornFS mounts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from acornfs.errors import AcornFSError


@dataclass(frozen=True, slots=True)
class MountRecord:
    mountpoint: str
    source: str
    options: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _decode_mount_field(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n")


def parse_mountinfo(text: str) -> list[MountRecord]:
    mounts: list[MountRecord] = []
    for line in text.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            filesystem = fields[separator + 1]
            source = fields[separator + 2]
            mountpoint = fields[4]
            options = fields[5]
        except (IndexError, ValueError):
            continue
        if filesystem != "fuse.acornfs":
            continue
        mounts.append(
            MountRecord(
                mountpoint=_decode_mount_field(mountpoint),
                source=_decode_mount_field(source),
                options=options,
            )
        )
    return mounts


def active_mounts() -> list[MountRecord]:
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise AcornFSError(f"Cannot read mount status: {exc}") from exc
    return parse_mountinfo(mountinfo)


def is_mounted(mountpoint: str | Path) -> bool:
    target = str(Path(mountpoint).expanduser().resolve())
    return any(record.mountpoint == target for record in active_mounts())
