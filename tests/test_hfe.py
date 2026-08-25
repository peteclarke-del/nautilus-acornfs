from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from acornfs.core import ReadOnlyImage, hfe, resolve_image
from acornfs.errors import UnsupportedImageError
from tests.image_fixture import create_adfs_floppy, create_dfs_floppy


def _hfe_container(signature: bytes, *, tracks: int, sides: int, mfm_sectors: int = 0) -> bytes:
    header = bytearray(1024)
    header[:8] = signature
    header[9] = tracks
    header[10] = sides
    header[18:20] = (1).to_bytes(2, "little")
    header[512:514] = (2).to_bytes(2, "little")
    header[514:516] = (512).to_bytes(2, "little")
    marker = bytes.fromhex("229122912291aa2a")
    track = (marker * mfm_sectors).ljust(512, b"\x55")
    return bytes(header) + track


@pytest.mark.parametrize("signature", [hfe.HFE_V1_SIGNATURE, hfe.HFE_V3_SIGNATURE])
def test_resolves_complete_hfe_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signature: bytes
) -> None:
    raw = create_dfs_floppy(tmp_path, double_sided=True, filename="source.dsd")
    image = tmp_path / "disc.hfe"
    image.write_bytes(_hfe_container(signature, tracks=80, sides=2))
    calls: list[str] = []

    monkeypatch.setattr(hfe.shutil, "which", lambda _name: "/usr/bin/gw")

    def convert(_command: str, _source: Path, destination: Path, format_name: str) -> str:
        calls.append(format_name)
        assert format_name == "acorn.dfs.ds80"
        shutil.copyfile(raw, destination)
        return "Found 1600 sectors of 1600 (100%)"

    monkeypatch.setattr(hfe, "_run_convert", convert)

    resolved = resolve_image(image)
    try:
        assert resolved.filesystem == "acorn-dfs"
        assert resolved.container is not None
        assert resolved.container.version == (3 if signature == hfe.HFE_V3_SIGNATURE else 1)
        assert resolved.capabilities.mount_read_write is True
        assert calls == ["acorn.dfs.ds80"]
    finally:
        resolved.close()


def test_mfm_probe_selects_adfs_layout_without_trial_conversions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = create_adfs_floppy(tmp_path, format_name="l", filename="source.adl")
    image = tmp_path / "disc.hfe"
    image.write_bytes(_hfe_container(hfe.HFE_V1_SIGNATURE, tracks=80, sides=2, mfm_sectors=16))
    calls: list[str] = []
    monkeypatch.setattr(hfe.shutil, "which", lambda _name: "/usr/bin/gw")

    def convert(_command: str, _source: Path, destination: Path, format_name: str) -> str:
        calls.append(format_name)
        shutil.copyfile(raw, destination)
        return "Found 2560 sectors of 2560 (100%)"

    monkeypatch.setattr(hfe, "_run_convert", convert)

    resolved = resolve_image(image)
    try:
        assert resolved.filesystem == "adfs"
        assert resolved.container is not None
        assert resolved.container.format.greaseweazle_name == "acorn.adfs.640"
        assert calls == ["acorn.adfs.640"]
    finally:
        resolved.close()


def test_incomplete_hfe_is_not_flattened_for_mounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "protected.hfe"
    image.write_bytes(_hfe_container(hfe.HFE_V1_SIGNATURE, tracks=80, sides=2))
    monkeypatch.setattr(hfe.shutil, "which", lambda _name: "/usr/bin/gw")
    monkeypatch.setattr(
        hfe,
        "_run_convert",
        lambda *_args: "Found 1599 sectors of 1600 (99%)",
    )

    with pytest.raises(UnsupportedImageError, match="without losing track-level data"):
        resolve_image(image)


def test_hfe_mount_requires_host_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = tmp_path / "disc.hfe"
    image.write_bytes(_hfe_container(hfe.HFE_V1_SIGNATURE, tracks=80, sides=2))
    monkeypatch.setattr(hfe.shutil, "which", lambda _name: None)

    with pytest.raises(UnsupportedImageError, match="requires the Greaseweazle host tools"):
        resolve_image(image)


def test_read_write_hfe_reencodes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = create_adfs_floppy(tmp_path, format_name="l", filename="source.adl")
    container_header = _hfe_container(hfe.HFE_V1_SIGNATURE, tracks=80, sides=2, mfm_sectors=16)
    image = tmp_path / "disc.hfe"
    image.write_bytes(container_header + raw.read_bytes())
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(hfe.shutil, "which", lambda _name: "/usr/bin/gw")

    def convert(_command: str, source: Path, destination: Path, _format_name: str) -> str:
        target = Path(str(destination).split("::", 1)[0])
        if source.suffix.casefold() == ".hfe":
            target.write_bytes(source.read_bytes()[len(container_header) :])
        else:
            target.write_bytes(container_header + source.read_bytes())
        return "Found 2560 sectors of 2560 (100%)"

    monkeypatch.setattr(hfe, "_run_convert", convert)

    with ReadOnlyImage.open(image, writable=True) as mounted:
        mounted.create_file(1, b"HFEtest")

    with ReadOnlyImage.open(image) as reopened:
        assert reopened.lookup(1, b"HFEtest") is not None
