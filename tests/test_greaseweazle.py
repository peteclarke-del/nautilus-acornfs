from __future__ import annotations

from io import StringIO
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace
from typing import Any

import pytest
from oaknut.filesystem import create_filesystem

from acornfs import greaseweazle
from acornfs.errors import AcornFSError
from acornfs.greaseweazle import (
    detected_command,
    detected_drives,
    greaseweazle_format,
    physical_write_available,
    supports_physical_write,
    write_floppy,
)
from tests.image_fixture import create_adfs_floppy


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
    assert detected_command("disc.ssd") is None

    monkeypatch.setattr(
        "acornfs.greaseweazle.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    assert detected_command("disc.ssd") == "/usr/bin/gw"

    def timed_out(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutExpired("gw info", 4)

    monkeypatch.setattr("acornfs.greaseweazle.subprocess.run", timed_out)
    assert detected_command("disc.ssd") is None


def test_menu_availability_uses_immediate_udev_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serial_devices = tmp_path / "by-id"
    serial_devices.mkdir()
    device = tmp_path / "ttyACM0"
    device.touch(mode=0o600)
    (serial_devices / "usb-Keir_Fraser_Greaseweazle_GW123-if00").symlink_to(device)
    monkeypatch.setattr(greaseweazle, "SERIAL_DEVICE_DIRECTORY", serial_devices)
    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", lambda _name: "/usr/bin/gw")
    monkeypatch.setattr(
        "acornfs.greaseweazle.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("menu detection must not start gw"),
    )
    image = tmp_path / "disc.ssd"
    image.write_bytes(bytes(100 * 1024))

    assert physical_write_available(image) is True


def test_menu_availability_requires_command_and_accessible_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "disc.adf"
    image.write_bytes(bytes(160 * 1024))
    monkeypatch.setattr(greaseweazle, "SERIAL_DEVICE_DIRECTORY", tmp_path / "missing")
    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", lambda _name: "/usr/bin/gw")
    assert physical_write_available(image) is False


def test_hfe_menu_availability_requires_a_container_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", lambda _name: "/usr/bin/gw")
    monkeypatch.setattr("acornfs.greaseweazle._device_available", lambda: True)
    image = tmp_path / "disc.hfe"
    image.write_bytes(b"not an hfe")
    assert physical_write_available(image) is False

    image.write_bytes(b"HXCHFEV3" + bytes(1024))
    assert physical_write_available(image) is True

    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", lambda _name: None)
    assert physical_write_available(image) is False


def test_amiga_sized_adf_is_not_offered_as_an_acorn_physical_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "ambiguous.adf"
    image.write_bytes(bytes(880 * 1024))
    monkeypatch.setattr("acornfs.greaseweazle.shutil.which", lambda _name: "/usr/bin/gw")
    monkeypatch.setattr("acornfs.greaseweazle._device_available", lambda: True)

    assert physical_write_available(image) is False


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("s", "acorn.adfs.160"),
        ("m", "acorn.adfs.320"),
        ("l", "acorn.adfs.640"),
        ("d", "acorn.adfs.800"),
        ("e", "acorn.adfs.800"),
        ("f", "acorn.adfs.1600"),
    ],
)
def test_acorn_adf_geometry_selects_explicit_greaseweazle_format(
    tmp_path: Path, variant: str, expected: str
) -> None:
    image = create_adfs_floppy(tmp_path, format_name=variant, filename=f"acorn-{variant}.adf")

    assert greaseweazle_format(image) == expected


@pytest.mark.parametrize(
    ("double_sided", "tracks", "expected"),
    [
        (False, 40, "acorn.dfs.ss"),
        (False, 80, "acorn.dfs.ss80"),
        (True, 40, "acorn.dfs.ds"),
        (True, 80, "acorn.dfs.ds80"),
    ],
)
def test_dfs_geometry_selects_explicit_greaseweazle_format(
    tmp_path: Path, double_sided: bool, tracks: int, expected: str
) -> None:
    filesystem = create_filesystem("acorn-dfs")
    preset = f"{tracks}t-{'ds' if double_sided else 'ss'}"
    geometry = filesystem.geometry_grammar().presets[preset]
    image = tmp_path / ("disc.dsd" if double_sided else "disc.ssd")
    filesystem.create(image, geometry, title="FORMAT")

    assert greaseweazle_format(image) == expected


def test_drive_detection_returns_only_pc_drives_with_index_pulses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = [
        SimpleNamespace(returncode=0, stdout="Command Failed: No Index\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="Rate: 299.981 rpm ; Period: 200.013 ms\n", stderr=""),
    ]
    monkeypatch.setattr("acornfs.greaseweazle.subprocess.run", lambda *_a, **_k: completed.pop(0))

    drives = detected_drives("disc.ssd", command="/usr/bin/gw")

    assert drives == ("B",)
    assert completed == []


def test_drive_detection_falls_back_to_shugart_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = iter(
        [
            "",
            "",
            "Rate: 300.000 rpm ; Period: 200.000 ms",
            "",
            "",
            "",
        ]
    )

    def probe(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    monkeypatch.setattr("acornfs.greaseweazle.subprocess.run", probe)

    assert detected_drives("disc.dsd", command="/usr/bin/gw") == ("0",)


def test_drive_probe_timeout_resets_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def probe(command: list[str], **_kwargs: Any) -> Any:
        commands.append(command)
        if command[1] == "rpm":
            raise TimeoutExpired(command, 5)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("acornfs.greaseweazle.subprocess.run", probe)

    assert detected_drives("disc.adf", command="/usr/bin/gw") == ()
    assert [command[1] for command in commands].count("reset") == 6


@pytest.mark.parametrize("suffix", ["ssd", "DSD", "adf", "ads", "adm", "adl", "hfe"])
def test_supported_greaseweazle_image_suffixes(suffix: str) -> None:
    assert supports_physical_write(f"disc.{suffix}") is True


def test_write_uses_snapshot_drive_and_default_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "private-name.ssd"
    image.write_bytes(b"disc image")
    launched: list[list[str]] = []

    monkeypatch.setattr("acornfs.greaseweazle.detected_command", lambda _path: "/usr/bin/gw")
    monkeypatch.setattr("acornfs.greaseweazle.greaseweazle_format", lambda _path: "acorn.dfs.ss")

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
    assert launched[0][3] == "--format=acorn.dfs.ss"
    assert launched[0][-1].endswith(".ssd")
    assert "--no-verify" not in launched[0]
    assert launched[0][-1] != str(image)
    assert updates[-1] == (100, "All tracks written and verified.")


def test_hfe_write_preserves_native_container_and_omits_sector_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "protected.hfe"
    image.write_bytes(b"HXCHFEV3" + bytes(1024))
    launched: list[list[str]] = []
    monkeypatch.setattr("acornfs.greaseweazle.detected_command", lambda _path: "/usr/bin/gw")

    def popen(command: list[str], **_kwargs: Any) -> _Process:
        launched.append(command)
        assert Path(command[-1]).read_bytes() == image.read_bytes()
        return _Process("Writing c=0-0:h=0\nT0.0: Writing Track\nAll tracks verified\n")

    monkeypatch.setattr("acornfs.greaseweazle.subprocess.Popen", popen)

    assert write_floppy(image, "A").verified is True
    assert launched[0][:3] == ["/usr/bin/gw", "write", "--drive=A"]
    assert not any(argument.startswith("--format=") for argument in launched[0])
    assert launched[0][-1].endswith(".hfe")


def test_write_failure_warns_that_physical_floppy_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "disc.dsd"
    image.write_bytes(b"disc image")
    monkeypatch.setattr("acornfs.greaseweazle.detected_command", lambda _path: "/usr/bin/gw")
    monkeypatch.setattr("acornfs.greaseweazle.greaseweazle_format", lambda _path: "acorn.dfs.ds")
    monkeypatch.setattr(
        "acornfs.greaseweazle.subprocess.Popen",
        lambda *_args, **_kwargs: _Process("Command Failed: Failed to verify Track 2.1\n", 1),
    )

    with pytest.raises(AcornFSError, match="physical floppy may be incomplete"):
        write_floppy(image, "B")


def test_write_rejects_invalid_drive_before_touching_image() -> None:
    with pytest.raises(AcornFSError, match="drive is invalid"):
        write_floppy("missing.ssd", "7")
