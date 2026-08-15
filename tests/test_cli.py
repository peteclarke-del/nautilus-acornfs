from pathlib import Path
from unittest.mock import patch

from acornfs.cli import main


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
