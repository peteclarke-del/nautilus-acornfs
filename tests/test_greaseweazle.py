from __future__ import annotations

from io import StringIO
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace
from typing import Any

import pytest

from acornfs.errors import AcornFSError
from acornfs.greaseweazle import (
    detected_command,
    physical_write_available,
    supports_physical_write,
    write_floppy,
)


class _Process:
    def __init__(self, output: str, returncode: int = 0) -> None:
        self.stdout = StringIO(output)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


def test_detection_short_circuits_unsupported_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def which(_name: str) -> str | None:
        nonlocal called
        called = True
        return "/usr/bin/gw"

    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", which)

    assert detected_command("hard-disc.dat") is None
    assert called is False


def test_detection_requires_executable_and_responsive_hardware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", lambda _name: None)
    assert physical_write_available("disc.ssd") is False

    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", lambda _name: "/usr/bin/gw")
    monkeypatch.setattr(
        "acornfs.greaseweazle.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    assert physical_write_available("disc.ssd") is False

    monkeypatch.setattr(
        "acornfs.greaseweazle.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    assert physical_write_available("disc.ssd") is True

    def timed_out(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutExpired("gw info", 4)

    monkeypatch.setattr("acornfs.greaseweazle.subprocess.run", timed_out)
    assert physical_write_available("disc.ssd") is False


@pytest.mark.parametrize("suffix", ["ssd", "DSD", "adf", "ads", "adm", "adl"])
def test_supported_greaseweazle_image_suffixes(suffix: str) -> None:
    assert supports_physical_write(f"disc.{suffix}") is True


def test_write_uses_snapshot_drive_and_default_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "private-name.ssd"
    image.write_bytes(b"disc image")
    launched: list[list[str]] = []

    monkeypatch.setattr("acornfs.greaseweazle.detected_command", lambda _path: "/usr/bin/gw")

    def popen(command: list[str], **_kwargs: Any) -> _Process:
        launched.append(command)
        assert Path(command[-1]).read_bytes() == b"disc image"
        return _Process(
            "Writing c=0-1:h=0-1\n"
            "T0.0: Writing Track (Flux: 100.0ms)\n"
            "T0.1: Writing Track (Flux: 100.0ms)\n"
            "T1.0: Writing Track (Flux: 100.0ms)\n"
            "T1.1: Writing Track (Flux: 100.0ms)\n"
            "All tracks verified\n"
        )

    monkeypatch.setattr("acornfs.greaseweazle.subprocess.Popen", popen)
    updates: list[tuple[int, str]] = []

    result = write_floppy(
        image, "a", progress=lambda percent, text: updates.append((percent, text))
    )

    assert result.drive == "A"
    assert result.verified is True
    assert launched[0][:3] == ["/usr/bin/gw", "write", "--drive=A"]
    assert "--no-verify" not in launched[0]
    assert launched[0][-1] != str(image)
    assert updates[-1] == (100, "All tracks written and verified.")


def test_write_failure_warns_that_physical_floppy_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "disc.dsd"
    image.write_bytes(b"disc image")
    monkeypatch.setattr("acornfs.greaseweazle.detected_command", lambda _path: "/usr/bin/gw")
    monkeypatch.setattr(
        "acornfs.greaseweazle.subprocess.Popen",
        lambda *_args, **_kwargs: _Process("Command Failed: Failed to verify Track 2.1\n", 1),
    )

    with pytest.raises(AcornFSError, match="physical floppy may be incomplete"):
        write_floppy(image, "B")


def test_write_rejects_invalid_drive_before_touching_image() -> None:
    with pytest.raises(AcornFSError, match="drive is invalid"):
        write_floppy("missing.ssd", "7")
