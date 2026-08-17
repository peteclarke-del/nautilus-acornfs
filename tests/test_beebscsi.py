from pathlib import Path

import pytest

from acornfs.core.beebscsi import discover_pair, inspect_pair, parse_descriptor
from acornfs.errors import DescriptorError, PairDiscoveryError


def descriptor(*, cylinders: int = 80, heads: int = 2) -> bytes:
    data = bytearray(22)
    data[13:15] = cylinders.to_bytes(2, "big")
    data[15] = heads
    return bytes(data)


def make_pair(directory: Path, stem: str = "scsi0") -> tuple[Path, Path]:
    dat = directory / f"{stem}.dat"
    dsc = directory / f"{stem}.dsc"
    dat.write_bytes(bytes(80 * 2 * 33 * 256))
    dsc.write_bytes(descriptor())
    return dat, dsc


@pytest.mark.parametrize("selected_index", [0, 1])
def test_discovers_pair_from_either_member(tmp_path: Path, selected_index: int) -> None:
    members = make_pair(tmp_path)
    pair = discover_pair(members[selected_index])
    assert pair.dat_path == members[0].resolve()
    assert pair.dsc_path == members[1].resolve()


def test_discovers_case_insensitive_extensions(tmp_path: Path) -> None:
    dat, dsc = make_pair(tmp_path, "Drive")
    dat.rename(tmp_path / "Drive.DAT")
    dsc.rename(tmp_path / "drive.DSC")
    pair = discover_pair(tmp_path / "Drive.DAT")
    assert pair.dsc_path.name == "drive.DSC"


def test_rejects_missing_partner(tmp_path: Path) -> None:
    dat = tmp_path / "scsi0.dat"
    dat.touch()
    with pytest.raises(PairDiscoveryError, match="one matching"):
        discover_pair(dat)


def test_pair_discovery_errors_are_translatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acornfs.core.beebscsi._", lambda message: f"translated: {message}")

    with pytest.raises(PairDiscoveryError, match="translated: Image member"):
        discover_pair(tmp_path / "missing.dat")


def test_rejects_ambiguous_case_variants(tmp_path: Path) -> None:
    dat, _ = make_pair(tmp_path)
    (tmp_path / "SCSI0.DSC").write_bytes(descriptor())
    with pytest.raises(PairDiscoveryError, match="2 DSC"):
        discover_pair(dat)


def test_parses_geometry() -> None:
    geometry = parse_descriptor(descriptor(cylinders=640, heads=4))
    assert geometry.cylinders == 640
    assert geometry.heads == 4
    assert geometry.capacity == 640 * 4 * 33 * 256


@pytest.mark.parametrize("size", [0, 16, 21, 23])
def test_rejects_nonstandard_descriptor_size(size: int) -> None:
    with pytest.raises(DescriptorError, match="exactly 22"):
        parse_descriptor(bytes(size))


@pytest.mark.parametrize(("cylinders", "heads", "message"), [(0, 2, "cylinders"), (80, 0, "heads")])
def test_rejects_zero_geometry(cylinders: int, heads: int, message: str) -> None:
    with pytest.raises(DescriptorError, match=message):
        parse_descriptor(descriptor(cylinders=cylinders, heads=heads))


def test_rejects_dat_larger_than_geometry(tmp_path: Path) -> None:
    dat, dsc = make_pair(tmp_path)
    dat.write_bytes(bytes(dat.stat().st_size + 1))
    with pytest.raises(DescriptorError, match="allows only"):
        inspect_pair(dsc)


def test_reports_short_dat_as_warning(tmp_path: Path) -> None:
    dat, _ = make_pair(tmp_path)
    dat.write_bytes(bytes(256))
    result = inspect_pair(dat)
    assert result["default_read_only"] is True
    assert result["writable_supported"] is True
    assert result["warnings"]
