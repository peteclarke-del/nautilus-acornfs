from pathlib import Path
from unittest.mock import patch

from acornfs.cli import main
from acornfs.core.image import ROOT_INODE, ReadOnlyImage
from tests.image_fixture import create_beebscsi_image


def test_inspect_error_is_concise(tmp_path: Path, capsys: object) -> None:
    result = main(["inspect", str(tmp_path / "missing.dat")])
    assert result == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "does not exist" in captured.err


def test_status_reports_no_mounts(capsys: object) -> None:
    with patch("acornfs.cli.active_mounts", return_value=[]):
        assert main(["status"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == "No AcornFS mounts found.\n"


def test_recover_command_reports_and_restores_checkpoint(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    image = ReadOnlyImage.open(dat_path, writable=True)
    readme = image.lookup(ROOT_INODE, b"README")
    assert readme is not None
    image.replace_file(readme.inode, b"temporary")
    image.close(clean=False)

    assert main(["recover", str(dat_path)]) == 0
    assert "Use --restore" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert main(["recover", "--restore", str(dat_path)]) == 0
    assert "restored" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_desktop_mount_forwards_writable_choice() -> None:
    with patch("acornfs.desktop.desktop_mount", return_value=0) as desktop_mount:
        assert main(["desktop-mount", "--read-write", "/image.dat"]) == 0
    desktop_mount.assert_called_once_with("/image.dat", read_write=True)


def test_validate_command_reports_clean_image(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    assert main(["validate", str(dat_path)]) == 0
    assert "passed with no problems" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_validate_command_reports_corrupt_subdirectory(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with ReadOnlyImage.open(dat_path) as image:
        docs = image.lookup(ROOT_INODE, b"DOCS")
        assert docs is not None
        _parent, entry = image._mount._navigate(docs.acorn_path)._resolve()  # type: ignore[attr-defined]
        offset = entry.start_sector * 256 + (5 * 256 - 1)
    with dat_path.open("r+b") as handle:
        handle.seek(offset)
        original = handle.read(1)
        handle.seek(offset)
        handle.write(bytes([original[0] ^ 0xFF]))

    assert main(["validate", str(dat_path)]) == 1
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Validation found" in output
    assert "DOCS" in output


def test_validate_command_rejects_truncated_dat_cleanly(tmp_path: Path, capsys: object) -> None:
    dat_path, _dsc_path = create_beebscsi_image(tmp_path)
    with dat_path.open("r+b") as handle:
        handle.truncate(512)
    assert main(["validate", str(dat_path)]) == 2
    error = capsys.readouterr().err  # type: ignore[attr-defined]
    assert "could not run safely" in error
