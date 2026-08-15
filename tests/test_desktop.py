from pathlib import Path

from acornfs.desktop import mountpoint_for_image
from tests.image_fixture import create_beebscsi_image


def test_mountpoint_is_stable_for_either_pair_member(tmp_path: Path, monkeypatch: object) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))  # type: ignore[attr-defined]
    dat_path, dsc_path = create_beebscsi_image(tmp_path)
    assert mountpoint_for_image(dat_path) == mountpoint_for_image(dsc_path)
    assert mountpoint_for_image(dat_path).parent == runtime / "acornfs"
    assert mountpoint_for_image(dat_path).name.startswith("scsi0-")
